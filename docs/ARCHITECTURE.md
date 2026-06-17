# 技术架构设计

## 1. 系统总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    Next.js Dashboard                              │
└─────────────────────────┬────────────────────────────────────────┘
                          │ REST API / WebSocket
┌─────────────────────────▼────────────────────────────────────────┐
│                      FastAPI Backend                              │
│   ┌────────────┐  ┌────────────┐  ┌──────────────────────────┐  │
│   │  API Layer │  │  Webhook   │  │  APScheduler (daily cron) │  │
│   └─────┬──────┘  └─────┬──────┘  └────────────┬─────────────┘  │
│         └───────────────┴──────────────────────┘                 │
│                    Service Layer                                   │
│         IssueService │ PRService │ CrawlerService                 │
│                             │                                      │
│                      PostgreSQL                                    │
└──────────────────────────────────────────────────────────────────┘
                          │
              ┌───────────▼───────────┐
              │    Redis              │
              │  Task Queues (3)      │
              │  WebSocket PubSub     │
              └───────────┬───────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
┌───────▼──────┐  ┌───────▼──────┐  ┌──────▼───────┐
│  Agent A     │  │  Agent B     │  │  Agent C     │
│  Workers     │  │  Workers     │  │  Workers     │
│  (Celery)    │  │  (Celery)    │  │  (Celery)    │
│  LLM API     │  │  Docker      │  │  LLM API     │
└──────────────┘  │  Sandbox     │  └──────────────┘
                  │  + LLM API   │
                  └──────┬───────┘
                         │
              ┌──────────▼──────────┐
              │    GitHub API        │
              │  抓取 / Fork / PR    │
              └──────────────────────┘
```

---

## 2. 技术栈决策

### 后端

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI | 异步原生，自动生成 OpenAPI |
| 任务队列 | Celery + Redis | 多 Worker 并发，任务重试，成熟稳定 |
| 定时任务 | APScheduler（内嵌） | 轻量，无需独立进程 |
| 数据库 | PostgreSQL | JSONB 存 Agent 输出，事务可靠 |
| ORM | SQLAlchemy 2.0 + Alembic | 类型安全，迁移完善 |
| GitHub 集成 | PyGithub + httpx | REST 封装 + GraphQL |

### 前端

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | Next.js 14 App Router | SSR/CSR 灵活 |
| UI | shadcn/ui + Tailwind CSS | 可定制，轻量 |
| 状态 | Zustand | 够用，不过度 |
| 实时 | WebSocket | 看板状态 + Agent B 日志实时推送 |
| 请求 | TanStack Query | 缓存 + 乐观更新 |

### Agent B 沙箱

| 组件 | 选型 | 理由 |
|------|------|------|
| 隔离方式 | Docker 容器（MVP） | 完全可控，无外部依赖 |
| 语言镜像 | agent-sandbox-{python/node/go/rust/java} | 按仓库语言选择 |
| 依赖缓存 | Docker volume 挂载 | npm/pip/go mod 跨任务复用 |
| 执行引擎 | Claude Code CLI headless | 原生工具支持，无需自实现 agent loop |

---

## 3. 数据流

### 抓取 → 评估

```
触发入口（两种，输出统一）：
  ├─ APScheduler（cron）→ CrawlerTask（按 crawl_targets 配置）
  └─ POST /crawl-jobs/manual（看板输入框）→ CrawlerTask（manual URL）

  → CrawlerService.fetch_issues()
     ├─ GitHub API 获取 Issues → bulk_upsert（去重）
     ├─ 对每个新出现的 repo：
     │    if not repo_profiles.is_fresh(repo): enqueue profile_queue
     │    （异步生成，不阻塞下面这条线）
     └─ 推入 analyze_queue

  → analyze_worker → Agent A → 写入 evaluations
  → Issue.status = PENDING_DECISION
```

### Profile 生成（独立异步）

```
profile_queue → profile_worker → Agent D（Single-Shot Tool Use）
  在 agent-sandbox-{lang} 内执行，--allowedTools 限于 Read/Glob/Grep + 受限 Bash
  → 写入 repo_profiles，TTL 90 天
```

### 开发

```
用户 POST /decide (start_dev)
  → Issue.status = QUEUED_DEV → 推入 dev_queue
  → dev_worker：
       1. 查询 repo_profiles：
            if fresh: 取 profile，注入 Agent B system prompt
            if missing/expired: 不阻塞，Agent B 降级自学习
       2. 启动 Docker 容器（agent-sandbox-{lang}）
       3. Agent B（Claude Code CLI headless）执行
       4. 实时日志 → Redis PubSub → WebSocket → 前端
  → 完成 → Issue.status = QUEUED_REVIEW
```

### 评审 → PR

```
review_worker → Agent C
  （system prompt 注入 repo_profile.code_style_notes，
   评审 dimension `code_style` 直接对比 profile 而非凭模型记忆）
  → APPROVED: PRService → GitHub API 创建 PR → Issue.status = PR_SUBMITTED
  → REJECTED: 携带意见重入 dev_queue → Issue.status = QUEUED_DEV
```

---

## 4. Agent B Docker 沙箱

```
任务开始
  → 检测仓库主语言（GitHub API languages 字段）
  → 选择镜像：agent-sandbox-{lang}
  → docker run --rm
       -v /cache/npm:/root/.npm          # 共享依赖缓存
       -v /cache/pip:/root/.cache/pip
       -v /cache/go:/root/go/pkg/mod
       -v /workspaces/{task_id}:/workspace
       --network=agent-net               # 只允许访问 GitHub + 包管理源 + LLM API
       --memory=4g --cpus=2
       agent-sandbox-{lang}
  → 容器内：claude --headless ... （Claude Code CLI）
  → 任务结束 → 容器自动销毁（--rm）
```

---

## 5. 部署（单机 Docker Compose）

```
postgres       主数据库
redis          队列 + PubSub
api            FastAPI（含 APScheduler）
worker-analyze Agent A Workers（scale: 2）
worker-dev     Agent B Workers（scale: 2，挂载 /var/run/docker.sock）
worker-review  Agent C Workers（scale: 2）
frontend       Next.js 生产构建
nginx          反向代理
```

---

## 6. Observability

### LLM 调用追踪

使用 **Langfuse**（可自部署）对每次 LLM 调用做链路追踪：
- Trace 粒度：单个 Agent 完整执行（含所有 Loop 迭代）
- Span 粒度：每次 LLM API 调用
- 记录：输入消息、输出内容、token 用量、延迟、模型版本、Prompt 版本

### 关键指标

| 指标 | 存储 | 用途 |
|------|------|------|
| 每次 Agent 执行的 token 用量 | `evaluations` / `dev_tasks` / `review_tasks` 表 | 成本统计 |
| Agent B 迭代次数 | `dev_tasks.loop_iterations` | 效率分析 |
| 卡死检测触发次数 | `dev_tasks.stuck_count` | Prompt 质量信号 |
| Celery 队列深度 | Redis + Prometheus | 容量规划 |
| Docker 容器资源用量 | cAdvisor | Agent B 资源控制 |

### 成本告警

在 `dev_tasks` 创建时估算预期成本，超过单任务阈值（默认 $1.0）时记录警告，不阻断执行。每日成本汇总推送到日志。

---

## 7. LLM 兜底策略（Fallback）

> **基础假设：** 主 LLM 提供商（Anthropic 直连或中转）会偶发性故障（限流、网络抖动、模型一次性输出异常）。
> **PoC 验证（2026-06-17）：** DeepSeek-V4-Pro 的 Anthropic 兼容接口可完整跑通 Agent B（含 MCP 自定义工具、Tool Use、ReAct 多轮、Prompt Injection 防御），可作为备用 provider。

### 7.1 触发粒度：混合策略

| 故障类型 | 切换粒度 | 处理位置 |
|----------|----------|----------|
| 网络错误、HTTP 5xx、429 限流、超时 | **单次 LLM 调用级**——同 task 内重试主 API（最多 N 次，指数退避），仍失败切备用 provider 继续 | `LLMClient` 抽象层 |
| Tool Use schema 反复校验失败（pydantic ValidationError ≥ 3 次） | **同上**（视为模型质量问题） | `LLMClient` |
| Agent B `report_failure` 或 `max_turns` 触发 | **整 task 级**——完整重新跑一遍 dev_task，第二次切备用 provider | `dev_worker` |
| Agent C 退回 ≥ 2 次（仍未达成 APPROVED） | **整 task 级**——下次 dev 用备用 provider 跑 | `dev_worker` |

### 7.2 触发参数（写入 config）

```yaml
# backend/config/models.yaml 新增字段
agents:
  agent_b:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
    base_url_env: ANTHROPIC_BASE_URL          # 可选，中转 API
    max_tokens: 8192
    temperature: 0.2

    retry:
      max_attempts: 3            # 单次 LLM 调用的重试次数
      backoff_seconds: [2, 5, 10]
      retry_on_http: [408, 429, 500, 502, 503, 504]

    fallback:                    # 切换触发时使用的备用 provider
      enabled: true
      provider: deepseek
      model: DeepSeek-V4-Pro
      auth_token_env: DEEPSEEK_ANTHROPIC_TOKEN
      base_url: https://api.deepseek.com/anthropic
      trigger_on_task_failure: true   # 整 task 失败时切换重跑
```

`LLMClient.factory()` 读取此配置，构造一个具备 fallback 能力的 client wrapper。

### 7.3 风险与边界

| 风险 | 缓解 |
|------|------|
| 主备模型行为不一致，同一 task 内中途切换可能让 ReAct loop 失忆（prompt cache 跨厂商失效） | **不在 ReAct loop 中途切换**。单次 LLM 调用级 fallback 只用于无状态的 LLM 调用（含 Agent A/C 这种 Single-Shot）；Agent B 的中途调用失败仍 retry 同 provider，task 级失败才切换 |
| DeepSeek 中转返回的 `total_cost_usd` 是 Anthropic 价格估算，非真实计费 | 数据层 `llm_call_logs.cost_usd` 字段对 fallback 调用打标记 `is_fallback=true`，月底用真实账单核对 |
| 备用 API 也故障 | 不做三级 fallback，直接抛错让 task 进入 `DEV_FAILED` 等人工 |
| 用户密钥/费用泄漏到错的 provider | `LLMClient` 严格使用 `api_key_env` / `auth_token_env` 字段，不读 fallback 之外的环境变量 |

### 7.4 可观测性

- `llm_call_logs.is_fallback` 布尔字段——所有走备用的调用打标
- Grafana / Langfuse：分主备拆分看 P50/P99 延迟、失败率、token 消耗
- 当 fallback 比例连续 1 小时超过 20% → 告警（主 API 在恶化）

---

## 8. PR 全链路与学习闭环

> **目的：** PR 的最终结果（merged / closed / reverted）和被退回原因是 Prompt 迭代最有价值的反馈。把这条信号工程化沉淀。
> **优先级：** Phase 1 数据采集（`pr_outcomes`、`rejection_reasons` 表）；Phase 3 喂回 Prompt / Eval。

### 8.1 数据采集来源

```
   Agent C 评审 ─── REJECTED ───→ rejection_reasons (source=agent_c)
                                       │
   GitHub Webhook ─── pull_request_review (state=changes_requested)
                                       └─→ rejection_reasons (source=maintainer_review)
                                       │
                  ─── pull_request closed (merged=false)
                                       └─→ rejection_reasons (source=maintainer_close)
                                           pr_outcomes (event_type=closed)
                                       │
                  ─── pull_request closed (merged=true)
                                       └─→ pr_outcomes (event_type=merged)

   PRReviewClassifier (后处理 LLM，廉价模型) ── 将 maintainer 自由文本 ──→ category/severity/dimension
```

详细字段见 `docs/DATA_MODEL.md` §5。

### 8.2 关键工程组件（新增）

| 组件 | 位置 | 职责 |
|------|------|------|
| `PRTracker` Service | `backend/app/services/pr_tracker.py` | Webhook 入口写 `pr_outcomes`；30 天巡检任务发现 revert |
| `RejectionClassifier` Agent | `backend/app/agents/rejection_classifier.py` | 用 Haiku 4.5 把 maintainer 自由文本分类为 category/severity/dimension，廉价但快 |
| Revert 巡检 | `APScheduler` 任务 | 每日扫描已 merged 的 PR 在仓库 main 分支后续 commit 中是否被 revert |

### 8.3 学习闭环（Phase 3 接入）

```
rejection_reasons + pull_requests.final_outcome
            │
            ├─→ 聚类：高频 category × Agent B 归因 → Prompt 反例 few-shot
            ├─→ MERGED_CLEAN 案例 → Eval Golden Set 正样本
            ├─→ MERGED_WITH_CHANGES 案例 → 模型改进训练样本（对比 PR submit 时 diff 与 merged 时 diff）
            └─→ 周报：Top 3 失败模式 + 每类型成本 + Agent B 归因比例
```

### 8.4 不在本期处理（已知不解决）

- maintainer 是否 merge 取决于其本人，无法控制；本系统只反向学习其偏好
- maintainer 长时间不响应：30 天 `STALE` 自动归档，不主动催 PR

---

## 9. Prompt Injection 防御

**风险场景：** Agent A/B/C 的输入中包含来自 GitHub 的 Issue 内容（外部用户控制），存在 Prompt Injection 风险。例如 Issue body 中写 "忽略以上指令..."。

**防御策略：**

| 层面 | 措施 |
|------|------|
| 输入边界 | Issue body 明确标记为用户数据，在 Prompt 中用 XML 标签包裹：`<issue_content>...</issue_content>` |
| 结构化输出 | 所有 Agent 输出通过 Tool Use 强制结构化，注入的文字无法影响输出结构 |
| Agent B 约束 | Docker 网络隔离，不能访问内部服务；`allowedTools` 限制工具列表 |
| 日志审计 | 记录所有 Agent 的完整输入输出，异常时可回溯 |
| Agent B PoC 验证 | **已通过（2026-06-17）**，见 `docs/POC_AGENT_B_REPORT.md`。Sonnet 4.6 与 DeepSeek-V4-Pro 在 XML 标签 + 边界声明下均未被绕过 |

---

## 10. Agent 边界与未来扩展

> **目的：** 显式说明当前 Agent 数量为何足够、添加新 Agent 的触发条件，避免后期不断"为某场景再加一个 Agent"的失控。

### 10.1 当前 Agent 全景

| Agent | 类型 | 触发频率 | 主要成本 | Phase |
|-------|------|----------|----------|-------|
| Agent A — IssueEvaluator | Single-Shot Tool Use | per-issue（高频） | 低（~$0.05） | 1.2 |
| Agent B — Developer | ReAct Loop（Claude Code CLI） | per-dev_task | 高（~$0.2–$2） | 1.4 |
| Agent C — Reviewer | Single-Shot Tool Use | per-review_task | 中（~$0.1） | 1.5 |
| Agent D — RepoOnboarding | Single-Shot Tool Use | per-repo（每 90 天） | 中（~$0.3） | 1.5 |
| RejectionClassifier（辅助） | Single-Shot Tool Use | per-rejection-event | 极低（Haiku，~$0.005） | 2.3 |

### 10.2 不打算引入的 Agent（及理由）

| 候选 | 为什么不做 |
|------|----------|
| CrawlerAgent | GitHub API 抓取是确定性任务，没有"判断"环节。规则+APScheduler 已足够；动态目标发现是 Phase 3 才考虑的优化项 |
| WebhookHandlerAgent | 纯数据搬运，写 DB 更新状态，无 LLM 推理必要 |
| LanguageDetectorAgent | GitHub `repo.languages` 字段 + 简单规则在 90%+ 仓库上正确，加 LLM 反而引入不确定性 |
| SandboxSelectorAgent | 同上，由 `repositories.primary_language` 直接映射镜像名 |
| ConflictResolverAgent | Agent C 退回 ≥3 次的硬停规则简单可靠；当前没有数据支持"meta agent 能改进决策"的假设 |
| MaintainerCommunicatorAgent | 让 Bot 用自然语言回 maintainer 评论的风险（语义出错、激怒维护者、降低人类沟通质量）远大于收益，明确不做 |
| WeeklyReportAgent | Phase 3 自然产物——但它的核心数据是 `rejection_reasons` 和 `llm_call_logs`，**先把数据层做对**，报告只是 SQL + 一次 LLM 总结 |

### 10.3 新增 Agent 的硬性触发条件

新增 Agent 前必须同时满足以下三点：

1. **任务确实包含语义判断**——而非数据搬运、确定性映射、纯规则
2. **数据证明现状是瓶颈**——例如 `rejection_reasons` 中某类失败比例 ≥ 15% 且现有 Agent 无法承担
3. **新 Agent 与现有 Agent 输出 schema 不能合并**——能合并则扩展现有 Agent，而非新增

不满足以上任一条 → 用规则/服务层处理，**不引入新 Agent**。

### 10.4 演进示例

- **若未来 maintainer 评论分类质量不足**：先尝试更换 RejectionClassifier 的模型（Haiku → Sonnet），而非新增"PreClassifier + Classifier"两段式
- **若 Agent B 在大型 Issue 上多轮失败**：先尝试扩 `max_turns` + 上下文压缩，而非引入"Planner + Executor"双 Agent 拆分
- **若动态发现新仓库价值**：先扩 `crawl_targets` 的过滤规则，而非引入 CrawlerAgent
