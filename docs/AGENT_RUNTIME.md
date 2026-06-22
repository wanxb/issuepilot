# Agent 运行时设计

> 本文档覆盖 Agent 执行引擎的架构决策：生命周期管理、Loop 设计、Context 管理、重试、卡死检测、并发调度。
> 实现代码在 `backend/app/agents/runtime/`，本文档只记录决策，不重复代码。

---

## 运行时职责

```
AgentRuntime
├── 生命周期管理        初始化 → 运行 → 清理，超时强制终止
├── Tool Router         tool_name → handler，校验 Schema，执行，回传结果
├── Loop Controller     控制推理迭代，检测终止条件，防卡死
├── Context Manager     token 计量，必要时压缩历史
├── Stream Handler      流式输出 → Redis PubSub → WebSocket → 前端
└── Retry Manager       API 错误重试、Schema 校验失败重试
```

---

## 执行模型：Single-Shot vs ReAct

### Agent A / C — Single-Shot

期望模型在一次推理后调用终止工具。Loop 设计：

```
构建消息 → LLM 推理
  ├─ 调用了终止工具 → Schema 校验 → 通过：结束 ✓ / 失败：重试（≤2次）
  └─ 未调用工具（纯文本）→ 追加"请调用工具"提示 → 重试（≤2次）
超过重试次数 → HARNESS_ERROR
```

### Agent B — ReAct Loop

多轮推理，每轮执行工具调用，直到终止工具触发。

```
每轮迭代：
  LLM 推理（流式）
    ├─ 纯文本（模型在思考）→ 连续3轮无工具调用 → 卡死检测
    ├─ 标准工具调用        → 执行 → 结果回传 → 继续
    └─ 终止工具调用        → 退出 Loop，返回结果

退出条件（任一触发）：
  ① 终止工具被调用（正常）
  ② 迭代次数 > 50（强制）
  ③ 运行时间 > 60min（强制）
  ④ 卡死检测触发（干预或强制）
```

---

## 终止状态

| 终止原因 | 类型 | 后续动作 |
|----------|------|----------|
| `tool_called` (A/C) | 正常 | 更新 Issue 状态 |
| `report_completion` (B) | 正常 | 推入 review_queue |
| `report_failure` (B) | 主动失败 | 重入 dev_queue，携带失败原因 |
| `timeout_by_time` (B) | 异常 | 重入 dev_queue，retry_count+1 |
| `timeout_by_iter` (B) | 异常 | 重入 dev_queue，retry_count+1 |
| `loop_stuck` (B) | 异常 | 注入干预提示后重入，或强制终止 |
| `schema_failed` (A/C) | 异常 | HARNESS_ERROR，记录，人工查看 |
| `api_error` (任意) | 异常 | Celery 重试（≤3次），超限记录错误 |

---

## 卡死检测（Agent B）

三种卡死模式，检测到后先注入干预提示，若下一轮仍未改善则强制终止：

| 模式 | 检测条件 | 干预提示 |
|------|----------|----------|
| `repeated_read` | 同文件 Read 超过 10 次且迭代数 > 15 | "你已多次读取相同文件，请基于已有信息开始实现" |
| `no_code_written` | Bash 超过 20 次，Write/Edit 为 0 | "你已执行多次命令但未修改代码，请开始实现" |
| `pure_text_loop` | 连续 3 轮无工具调用 | "请调用工具执行下一步操作" |

---

## Context 管理策略（Agent B）

Agent B 的 Loop 可能累积大量工具调用结果，需要主动管理 Context：

**压缩触发：** 当前 token 用量超过 max_tokens 的 85%

**压缩策略（优先级从高到低保留）：**
1. System Prompt — 永不压缩
2. 首条 User Message（任务描述）— 永不压缩
3. 最近 8 轮 Assistant + Tool Result — 完整保留
4. 更早的 Tool Result 内容 — 替换为 `[已压缩，原始 {N} tokens]`
5. Tool Use 调用记录（tool_name + input）— 始终保留（供模型理解历史轨迹）

**Token 计量：** 使用 Anthropic Token Counting API，不做估算。

---

## 重试策略

| 错误类型 | 最大重试 | 退避方式 |
|----------|----------|----------|
| Rate limit | 5 次 | 指数退避，base=2s |
| Overloaded | 3 次 | 指数退避，base=3s |
| API timeout | 3 次 | 固定 5s |
| Schema 校验失败 | 2 次 | 立即（附错误提示重新调用） |
| 模型未调用工具 | 2 次 | 立即（附提醒重新调用） |

Schema 校验失败时，将错误信息作为 `tool_result`（`is_error: true`）回传，引导模型自我修正。

---

## 流式日志推送

Agent B 执行时日志实时推送到前端：

```
LLM Stream → StreamHandler
  → 工具调用开始：推送 step 变更事件（ANALYZE/IMPLEMENT/TEST）
  → Bash 执行结果：推送输出摘要
  → 阶段完成：推送进度事件

Redis Channel: dev_task:{task_id}:logs
WebSocket: 前端订阅 → 实时显示进度
```

工具 → 步骤映射（启发式）：`Read/Grep/Glob → ANALYZE`，`Write/Edit → IMPLEMENT`，`Bash → TEST`（含 test/pytest/go test 关键词）。

---

## 并发调度

通过 Celery Worker 多进程实现并发，每个 queue 独立扩缩：

```
analyze_queue → worker-analyze (推荐 scale: 3)
dev_queue     → worker-dev     (推荐 scale: 2，受 Docker 资源限制)
review_queue  → worker-review  (推荐 scale: 3)
```

**Agent B 并发资源控制：** 每个 dev_worker 实例启动一个 Docker 容器，通过 Redis 分布式信号量限制并发容器数（上限 = `MAX_DEV_WORKERS` 配置项），超出时任务在队列中等待，不丢弃。

---

## 错误 → Issue 状态重入规则

| Agent | 错误类型 | Issue 状态 | 重入条件 |
|-------|----------|------------|----------|
| A | 任意失败 | 回退 `DISCOVERED` | Celery 重试 |
| B | 主动 `report_failure` | `DEV_FAILED` → `QUEUED_DEV` | retry_count < MAX_DEV_RETRY(2) |
| B | 超时/卡死 | `DEV_FAILED` → `QUEUED_DEV` | 同上 |
| B | retry_count 超限 | `ARCHIVED` | 通知用户 |
| C | `REJECTED` | `REVIEW_REJECTED` → `QUEUED_DEV` | review_cycle < MAX_REVIEW_RETRY(3) |
| C | review_cycle 超限 | `ARCHIVED` | 防死循环保护 |
| C | `APPROVED` | `PR_SUBMITTED` | — |

---

## Agent B 上下文压缩（2.3）

Agent B 在沙箱内通过 Claude Code CLI headless 运行，CLI 自身管理对话上下文。
我们的控制面只有以下三道防线，不实现自己的压缩器：

1. **`--max-turns`（默认 25）**：上限保护；超过即使没有 `/compact` 也会终止
   循环，dev_worker 走失败路径
2. **Claude Code CLI 内置 auto-compact**：CLI 在 context window 接近上限时
   自动压缩历史对话（不需要我们传 flag；默认开启）。我们仅通过
   `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 关闭遥测，未触动压缩行为
3. **卡死检测（dev_worker 端）**：`StuckDetector` 监测连续
   `agent_b_stuck_window`（默认 12）个 tool_use 全为只读工具（Read/Glob/Grep/
   WebFetch/WebSearch/TodoWrite）时主动停容器，标 `failure_reason=stuck`，
   走 Agent B 重试链路（max_dev_retry）

只读工具 vs 推进工具的判定见 `app/agents/stuck_detector.py::READ_ONLY_TOOLS`
+ `MUTATING_TOOLS`。

> 如果未来 Agent B 需要更细粒度的上下文压缩（如选择性裁剪历史 tool 输出 /
> 分阶段任务记忆），3.x 阶段再做。当前 MVP 不需要——`--max-turns 25` +
> CLI 内置压缩 + 卡死检测三层兜底已足。
