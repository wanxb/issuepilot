# Phase 1 端到端 dry run 报告

> 日期：2026-06-18 · 测试仓库：`wanxb/c-drive-cleaner`（Python / default branch `dev`） · 测试 Issue：#1「优化UI细节」

---

## TL;DR

**主流程跑通**：crawl → Agent A 评估 → 用户决策 → Agent D 异步 profile → Agent B 沙箱开发 → Agent C 评审。Pipeline 11 段除"PR 真实创建 + Webhook"外全部通过验证。受用户 GITHUB_DEV_TOKEN 的 fork 权限受限，PR 链路最后一公里被设计兜底（`pr_skip_no_fork`）。

**修了 3 个真 bug**（验证前未发现）：
1. SQLAlchemy 异步 engine 在 Celery worker 内复用 connection pool 跨 `asyncio.run()` 会抛 `Future attached to a different loop` — 用 NullPool 隔离
2. 1.5a 引入的 11 个 lowercase enum，SQLAlchemy 默认按 `.name`（UPPERCASE）序列化与 PG type 取值（lowercase）不匹配，首次 INSERT 即炸 — 新增 `pg_enum()` helper 用 `values_callable`
3. Claude Code CLI 升级到 2.1.179 后 `--print --output-format=stream-json` 必须配 `--verbose`，老 entrypoint.sh 没加，沙箱启动即 stderr 退出

**沙箱成本**（Agent B 单次成功开发）：26 turns / $0.92 / 4 分 30 秒 / 273 行 dev_logs / 3.7KB diff

**Provider fallback 真实触发**：测试期间 Anthropic 中转返回 `503 No available accounts`，Agent A / D / C 全部 retry 3 次后切到 DeepSeek-V4-Pro 成功（is_fallback=true）。1.2c 的兜底链路在真实失败下完整 work。

**Agent B 产出质量**：面对"优化UI细节"这种极度模糊的 issue，Agent B 仍输出了 5 个实质性改动（去 placeholder bug / 保留光标位置 / 新增快捷键 / 按钮 label 动态化 / 加 CSS 层次）。Agent C 评分 8.15 / APPROVED。

---

## 1. 阶段验证逐项

| # | 阶段 | 结果 | 备注 |
|---|------|------|------|
| 1 | manual URL 入库 + crawl_jobs 行写入 | ✅ | issues_enqueued=1 |
| 2 | Agent A 评估（profile_quality 子流并行） | ✅ | total_score=2.0 HARD（合理：issue 模糊） |
| 3 | Agent D profile 写入 | ✅ | profile_quality=low（无 CONTRIBUTING / 无 CI） |
| 4 | LLM provider 兜底切换 | ✅ | Anthropic 503 → DeepSeek（4 attempt / agent） |
| 5 | 用户决策 start_dev | ✅ | Issue 转 QUEUED_DEV，dev_task 入队 |
| 6 | dev_worker fork → 失败兜底 | ✅ | fork 403 → forked_repo = base_repo |
| 7 | 沙箱启动 → 旧镜像 stderr | ✅（设计正确） | dev_task FAILED / Issue DEV_FAILED → 状态机走完整失败路径 |
| 8 | 镜像 rebuild 后重启 dev_task | ✅ | 26 turns / $0.92 / SUCCEEDED |
| 9 | git diff 采集 + push 状态采集 | ✅ | diff 3697 bytes / branch_pushed=false（dev_token 写权限不够） |
| 10 | Agent C 评审 | ✅ | APPROVED / overall_score=8.15 |
| 11 | review_worker PR 路径 | ✅（设计正确） | head_repo == base_repo → `pr_skip_no_fork`，Issue 保持 IN_REVIEW |
| 12 | PR 真实创建 | ⏭ 未验证 | 受 token 权限限制；需 classic PAT with `repo` scope 或 fine-grained 加 Administration |
| 13 | GitHub Webhook 接收 | ⏭ 未验证 | 没有 PR 可点 close/merge；HMAC 验签 + payload 路由已被 5 unit + 7 integration 覆盖 |

---

## 2. 修复的 bug 详情

### bug 1 — asyncpg connection pool 跨 event loop 复用

**症状**：worker 内任何 Celery task `asyncio.run(_xxx_async())` 第二次执行 SQL 时抛
```
RuntimeError: Task got Future attached to a different loop
```

**根因**：`app/db/database.py` 模块级 `create_async_engine()` 在第一次 import 时创建连接池；asyncpg.Connection 与创建时的 event loop 绑死；Celery 每个 task 一个新 loop，复用池里的旧连接就炸。`pool_pre_ping=True` 让问题在每次 checkout 都暴露。

**修复**：worker 进程通过环境变量 `DB_USE_NULL_POOL=1` 启用 `NullPool`（不复用连接，每次新建）。API 容器保持 QueuePool 不变。

**代码改动**：
- `backend/app/db/database.py` — 按 `DB_USE_NULL_POOL` env 切换 poolclass
- `docker-compose.yml` — worker service 加 `DB_USE_NULL_POOL: "1"`

代价：worker 每次 `session_scope()` 多一次 TCP 握手；可接受（worker 任务以 LLM / Docker 为大头）。

### bug 2 — lowercase enum 与 SQLAlchemy 默认 `.name` 序列化不匹配

**症状**：第一次往 `repo_profiles` INSERT 时抛
```
invalid input value for enum profile_quality: "LOW"
```

**根因**：
- 1.5a 我手写 migration 时把 enum value 用 lowercase（`"low"`, `"medium"`, `"high"`），与 docs 对齐
- 但 SQLAlchemy 的 `Enum(EnumCls, native_enum=True)` 默认按 Python `.name` 序列化（`"LOW"`）
- PG type 取值是小写 → 不匹配
- **历史 enum 没出问题**：1.2-1.4 引入的 `IssueStatus` / `DevTaskStatus` 等 Python enum 取值是 UPPERCASE，与 `.name` 一致 → 巧合 OK
- 1.5a 引入的 11 个 enum 全部踩中这个坑

**修复**：新增 `app/models/_pg_enum.py::pg_enum(enum_cls, name)` helper，强制用 `values_callable=lambda x: [e.value for e in x]` 按 `.value` 序列化。1.5a 引入的 5 个 model 文件（repo_profile / review_task / pull_request / pr_outcome / rejection_reason）所有 `Enum(...)` 换 `pg_enum(...)`。

历史 model 不动（取值是 UPPERCASE，与 `.name` 一致，行为不变）。

### bug 3 — Claude Code CLI 2.1.179 要求 `--verbose`

**症状**：沙箱启动后立刻退出
```
Error: When using --print, --output-format=stream-json requires --verbose
```

**修复**：`sandbox/python/entrypoint.sh` 中 `claude -p -` 命令加 `--verbose` flag。镜像 rebuild。

---

## 3. 兜底路径触发记录（设计正确性验证）

| 兜底场景 | 触发原因 | 行为 |
|----------|----------|------|
| Anthropic provider 不可用 | 503 No available accounts | LLMClient retry 3 次 → DeepSeek fallback → 4 行 llm_call_logs（3 fail + 1 success） |
| Fork 403（dev_token 权限不够） | Fine-grained PAT 没 Administration 权限 | `forked_repo = base_repo` + warning 日志 |
| 沙箱启动即错 | entrypoint.sh + Claude CLI 新版不兼容 | dev_task FAILED / Issue DEV_FAILED |
| Push 403 | dev_token 没目标仓库写权限 | `branch_pushed = false` / 日志记录 |
| PR 创建时无 fork | `head_repo == base_repo` | `pr_skip_no_fork` / Issue 保持 IN_REVIEW（不破坏退回链路） |

---

## 4. Agent B 产出（diff 3.7KB）

Issue #1 原文：
> 优化UI细节和操作选择细节。做整体的优化。

Agent B 探索了 `src/c_drive_cleaner/` 全部 .py 文件 + README + pyproject.toml 后，在 `app.py` 做了 5 处改动：

1. **去掉 `"???????"` placeholder**：`status-label` 默认值从 `"???????"`（明显的占位符 / 未替换字符串）改为 `""`
2. **保留光标位置**：按 Space 标记后光标不再跳到表格顶部，改为 stay 在原行
3. **新增 `m` 快捷键** → action_clean_marked（之前只有按钮无快捷键）
4. **按钮 label 动态切换**：`#toggle` 按钮根据当前是否已标记显示"取消标记"/"标记选中"
5. **新增 `#confirm-title` CSS**：对话框标题加粗 + primary color + margin

文件：`src/c_drive_cleaner/app.py`（仅 1 个文件）
新增测试：0（仓库本来就没测试，Agent B `test_output_snippet="repository has no test suite"` 跳过）

Agent C 评分：
- correctness 8 / passed
- test_coverage 6 / passed（test_passed=true + failed_tests=0 满足硬条件）
- code_style 8 / passed
- security 9 / passed
- pr_description 9 / passed
- overall_score 8.15 → APPROVED

---

## 5. 实测成本与延时

| Agent | provider 用量 | 总成本 | 延时 |
|-------|---------------|--------|------|
| Agent A | DeepSeek（Anthropic retry 3 次失败） | ~$0.01 | ~30s |
| Agent D | DeepSeek（同上） | ~$0.005 | ~40s |
| Agent B | Anthropic Sonnet 4.6（中转此时已恢复） | **$0.92** | **4m 30s** |
| Agent C | DeepSeek | ~$0.02 | ~60s |
| **合计** | — | **~$0.96** | **~6m 40s**（从 start_dev 到 review 完成） |

Agent B 的 $0.92 接近 PoC 报告的 $0.22 的 4 倍，原因：
- 调用了 Explore subagent（大量 Read / Bash 探索）
- 26 turns 接近 max_turns=25 上限（issue 模糊导致探索成本高）
- 输出涉及 5 处独立改动 + reasoning

---

## 6. 未验证项的补救方案

### 6.1 PR 创建

最快路径：**用户把 GITHUB_DEV_TOKEN 换成 classic PAT** 勾 `repo` scope（含 fork + push + PR create），重跑一次。

或者：**手动在 GitHub 上把目标仓库 fork 到 dev 账户**，让 `_upsert_repo` 的 fork API 调用因为 fork 已存在直接返回 200（GitHub fork 是幂等的）。

### 6.2 Webhook

无需 ngrok。**用一个本地脚本** 拿真实 GitHub PR API 数据当 payload（保证字段结构与 GitHub 实际推送完全一致），HMAC 签好直接 POST 到 `http://localhost:8000/api/v1/webhooks/github`。整数据保真度等价于 GitHub 真实推送。

样例脚本路径：`scripts/replay_github_webhook.py`（未写，dry run 报告完后视需要补）。

---

## 7. 收尾

- [x] 测试数据已清（TRUNCATE 全表）后又重跑一次，留下：1 issue（IN_REVIEW）+ 1 dev_task + 1 review_task + 1 repo_profile + 11 llm_call_logs + 273 dev_logs。**留作回归参考样本**，不清理
- [ ] dev_token / webhook secret 已配置；保留以备后续补 PR 创建验证
- [ ] 待办（不阻塞 Phase 2）：
   - 补 `scripts/replay_github_webhook.py` 把 webhook 路径在本地走通
   - 补 GITHUB_DEV_TOKEN 升级文档
   - dev_log_service 的事务行为（commit 在 sandbox phase 退出时一次性落地）应该写到 `docs/AGENT_RUNTIME.md`，避免后人误判 observability bug
   - 2.3 阶段把 Agent B `agent-sandbox-{lang}` 镜像版本固定 + 自动 build hook，避免 Claude CLI 升级引入新 flag 要求时无人发现
