# Roadmap

## 总体策略

分三个阶段，每阶段交付可独立运行的产物：

- **Phase 1（MVP）**：跑通主干流程，人工干预点明确，可用但粗糙
- **Phase 2（完善）**：补全边界处理，提升稳定性和可观测性
- **Phase 3（优化）**：提升 Agent 质量，降低成本，支持扩展

---

## Phase 1 — MVP（主干流程跑通）

**目标：** 能从 Issue 发现到 PR 提交完整跑通一个真实案例

### 里程碑 1.0 — Agent B PoC 验证（开发前置）✅ 已完成 2026-06-17

> **已通过。** 详细报告 `docs/POC_AGENT_B_REPORT.md`。

- [x] 验证 Claude Code CLI headless 模式的基本调用方式（`--print` / `--output-format json`）
- [x] 验证 `--allowedTools` 参数是否能限制 Agent B 的工具访问范围
- [x] 验证 Docker 容器内 Claude Code CLI 能否正常运行（网络、认证）
- [x] 验证 Issue body 含 Prompt Injection 内容时，Tool Use 结构化输出是否仍然稳定
- [x] 验证 `report_completion` / `report_failure` 自定义工具能否注入并被模型调用（MCP stdio server）
- [x] **额外验证**：DeepSeek-V4-Pro 的 Anthropic 兼容接口可作为 Agent B 兜底 provider
- [x] 输出 PoC 报告

**PoC 关键数字：** Sonnet 4.6 clean run 9 turns / 49.6s / $0.22；injection run 9 turns / 37s / $0.11，零绕过。

### 里程碑 1.1 — 基础设施 ✅ 已完成 2026-06-17

- [x] Docker Compose 环境（postgres 16 + redis 7 + api + worker + frontend）
- [x] PostgreSQL 数据库初始化（alembic baseline migration + 异步 env.py）
- [x] FastAPI 骨架 + `/healthz` + `/readyz`（DB+Redis 连通性双探针）
- [x] Celery 任务队列接通 Redis（analyze / dev / review 三队列 + ping 冒烟任务跑通）
- [x] Next.js 14 前端骨架 + Tailwind + shadcn/ui base + API 代理（rewrites `/api/*` `/readyz` 到 api 容器）
- [x] 前端首页含 HealthPanel，实时轮询 `/readyz`

### 里程碑 1.2 — Crawler + Agent A + 手动输入入口 ✅ 已完成 2026-06-17

- [x] GitHub API 客户端（Issue 抓取、仓库信息、PR 列表预取）—— httpx async，5 个端点
- [x] CrawlerService（手动 URL 入口 + dedup 逻辑）—— cron 触发留到 2.2 抓取配置管理
- [x] **手动 URL 入口**：POST `/api/v1/crawl-jobs/manual`（含 needs_confirmation 两步）
- [x] **看板 URL 输入框**（UrlInputBar + window.confirm 确认对话框；shadcn Dialog 留到 2.4）
- [x] LLMClient 抽象层 + AnthropicClient（直连 / 中转双模式）
- [x] DeepSeek Anthropic 兼容兜底 wrapper（FallbackLLMClient）
- [x] `llm_call_logs` 表 + 调用埋点
- [x] 单次调用级 retry + provider 切换
- [x] Agent A Harness（Single-Shot Tool Use）
- [x] Agent A Prompt v1.0（含 hard rules + 反 injection XML 边界）
- [x] analyze_worker（Celery + asyncio.run）
- [x] `/api/v1/issues` GET 接口（分页 + 5 维筛选 + 排序）
- [x] 看板 Issue 列表页（IssuesList 5s 轮询 + Card 卡片）

**真实端到端验证（已通过）：** 3 个 demo issue 入库 → worker 跑完 → 看板呈现评估分数 + 摘要。
proxy 故障时 fallback 全程自动接管，4 行 llm_call_logs 完整审计。

### 里程碑 1.3 — 用户决策流 ✅ 已完成 2026-06-17

- [x] `POST /api/v1/issues/{id}/decide` 接口（action: ignore / start_dev）
- [x] Issue 状态流转校验（PENDING_DECISION → QUEUED_DEV / IGNORED），非法状态返 409、非法 action 返 422
- [x] 看板操作按钮 DecideActions（"加入开发" / "忽略"，乐观更新 + 错误回退）

### 里程碑 1.4 — Agent B（Docker 沙箱 + 开发）✅ 已完成 2026-06-17

- [x] Docker 沙箱管理器（SandboxManager：start/stream_logs/collect_report/stop/wait）
- [x] agent-sandbox-python 镜像（Dockerfile + entrypoint.sh + mcp_report_server.py）
- [x] GitHub Fork + Clone 逻辑（GitHubService，无 dev_token 时跳过 fork）
- [x] Agent B Harness（stream-json 解析，ParsedLine，report_completion/failure 提取）
- [x] Claude Code CLI headless 模式集成（base64 prompt → stdin，--output-format stream-json）
- [x] dev_worker（Celery，Phase 1 DB + Phase 2 沙箱 + Phase 3 结果写回，三阶段编排）
- [x] dev_logs 实时写入（DevLogService：DB insert + Redis publish）
- [x] WebSocket 日志推送（Redis PubSub → `/ws/dev-tasks/{id}/logs` → 前端）
- [x] 看板开发进度展示（DevLogStream 组件，IN_DEV/DEV_TESTING 状态自动展示）

### 里程碑 1.5 — Agent C + Agent D（RepoOnboarding）+ PR 提交 + PR 链路数据采集

> 拆为 5 个子里程碑串行推进：1.5a 数据基础 → 1.5b Agent C → 1.5c Agent D → 1.5d PR 创建 → 1.5e Webhook。

#### 1.5a — 数据基础（5 张表 + 11 个 enum）✅ 已完成 2026-06-18

- [x] enums.py 扩展：ReviewVerdict / ReviewTaskStatus / PullRequestStatus / PRFinalOutcome / PROutcomeEventType / RejectionSource / RejectionCategory / RejectionSeverity / RejectionDimension / AgentBAttribution / ProfileQuality
- [x] ORM 模型：`repo_profiles`、`review_tasks`、`pull_requests`、`pr_outcomes`、`rejection_reasons`
- [x] Alembic migration `c2d6e2f6a2b2`（upgrade/downgrade 双向验证通过）

#### 1.5b — Agent C harness + review_worker + 评审拒绝路径 ✅ 已完成 2026-06-18

- [x] Agent C schemas（AgentCInput / AgentCOutput / ReviewDimension / TestResult）+ SUBMIT_REVIEW_TOOL
- [x] Agent C prompt v1.0（5 维评分 + 通过门槛 + 退回意见规范 + 反 injection XML 边界 + 可注入 repo_style_notes/contributing_summary）
- [x] Agent C harness（SingleShotLoop，schema 校验 + tool 未调检测）
- [x] git diff 采集链路：entrypoint.sh 记录 BASE_SHA + `git diff base..HEAD` → SandboxManager.collect_diff → dev_tasks.git_diff / base_sha 列 + alembic migration `d3e7f3a7c3d3`
- [x] review_worker（Celery；APPROVED 留在 IN_REVIEW 待 1.5d；REJECTED 转 REVIEW_REJECTED）
- [x] Agent C REJECTED 时按 5 维最低未通过维度推断 category/severity/dimension，写 `rejection_reasons` source=agent_c
- [x] IssueService.mark_in_review / mark_review_rejected
- [x] dev_worker 成功路径 chain 触发 review_worker
- [x] celery_app 补齐 dev_worker + review_worker import
- [x] 单测：11 项 test_agent_c.py 全绿（5 schema + 2 verdict + 2 失败路径）

> **沙箱镜像**：entrypoint.sh 已更新；下次跑端到端前需 `docker build -t agent-sandbox-python:latest sandbox/python/`。

#### 1.5c — Agent D + repo_profiles 异步生成 + Agent B/C 注入 ✅ 已完成 2026-06-18

- [x] Agent D schemas（AgentDInput / AgentDOutput / MergedPRSample / MergedPRExample）+ REPORT_PROFILE_TOOL
- [x] Agent D prompt v1.0（**MVP 路径选择**：SingleShot API 调用，而非 AGENT_DESIGN 建议的沙箱模式；profile_worker 用 GitHub API 预取 README/CONTRIBUTING/manifest/PR diff 后注入，省去沙箱启动成本，正式版可在 2.x 改为沙箱）
- [x] Agent D harness（SingleShot + Tool Use）
- [x] GitHubClient 扩展：`get_file_content` + `get_pr_diff`（Accept: vnd.github.diff）
- [x] `profile_queue` Celery 队列 + `profile_worker.generate_profile(repo_id, force=False)`，TTL 90 天，旧 profile 过期自动重生
- [x] CrawlerService 在 `_upsert_repo` 中检测 RepoProfile fresh 状态，缺失/过期时入 profile_queue（不阻塞 analyze_queue）
- [x] AgentBInput 新增 `repo_profile_block` 字段；dev_worker 加载 RepoProfile 渲染为文本块注入 prompt；缺失时 prompt 走自学习降级
- [x] Agent B SYSTEM_PROMPT 加 "HONOR <repo_profile>" 一条规则
- [x] review_worker 加载 RepoProfile 注入 `repo_style_notes` / `repo_contributing_summary`（Agent C prompt 已就位）
- [x] celery_app 新增 profile_queue + route + import；docker-compose worker 命令补 profile_queue
- [x] models.yaml + models.example.yaml 增 agent_d 配置（含 retry / fallback）
- [x] 单测：8 项 test_agent_d.py 全绿（5 schema + 3 harness 路径）；总 78 单测 + 21 integration 全绿
- [x] worker 容器重建后 4 队列 / 5 任务正确注册

#### 1.5d — PR 创建 + PR_SUBMITTED 链路 ✅ 已完成 2026-06-18

- [x] entrypoint.sh 在 diff 采集后 `git push origin BRANCH_NAME`，写 `.agent_push.ok` / `.agent_push.fail`
- [x] SandboxManager.collect_push_status；dev_tasks.branch_pushed Boolean 列 + migration `e4f8g4b8d4e4`
- [x] PRService（GitHub API 创建 PR；获取 default_branch；预检 head 分支存在性；422 重复 PR / BranchNotPushed / NoDevToken 分别建模）
- [x] IssueService.mark_pr_submitted（IN_REVIEW → PR_SUBMITTED）
- [x] review_worker APPROVED 路径：调 PRService → 写 `pull_requests` + `pr_outcomes(submitted)` → Issue 转 PR_SUBMITTED
- [x] 异常处理：无 dev_token / 未 push / 重复 / 创建失败时日志 + 保持 IN_REVIEW（不破坏退回链路）
- [x] dev_worker 把 branch_pushed 状态持久化到 DB
- [x] 单测：7 项 test_pr_service.py 全绿（happy + 422 dup + branch missing + 500 + no_token + default_branch）；总 85 单测 + 21 integration 全绿

#### 1.5e — Webhook 接收 + PRTracker + 看板 ✅ 已完成 2026-06-18

- [x] POST `/api/v1/webhooks/github` 端点 + HMAC-SHA256 验签（`verify_signature` 用 hmac.compare_digest 防时序攻击）
- [x] 事件路由：`pull_request.closed` / `pull_request_review.submitted` / `issue_comment.created` (仅 PR 评论) / `ping`
- [x] PRTracker Service：`find_by_url` + `record_event` + `on_pr_closed` / `on_review_received` / `on_comment_received`
- [x] PR closed 写 `pull_requests.status` + `final_outcome`（merged → MERGED + MERGED_CLEAN；closed → CLOSED + CLOSED_BY_MAINTAINER）
- [x] IssueService.mark_pr_merged / mark_pr_closed（PR_SUBMITTED → PR_MERGED / PR_CLOSED）
- [x] 未追踪的 PR / 非 closed action / 非 PR comment：200 OK + skip（不让 GitHub 重试）
- [x] IssueListItem 返回最新 `pull_request`（number/url/status/final_outcome/title/submitted_at）；issues API 批量 join 查询
- [x] 前端 PR 卡片（`PRPanel`）+ final_outcome 着色 badge + PR 链接
- [x] 单测：5 项 test_webhook_signature.py 全绿（HMAC valid / missing / wrong prefix / tampered / wrong secret）
- [x] 集成测试：7 项 test_webhook_api.py（401 invalid sig / ping / merged → PR_MERGED / closed → PR_CLOSED / untracked / review_received / issue_comment 仅 PR）—— 需 host 端 docker exec 跑（同 decide_api 范式）
- [x] 端到端 curl 验证：未配置 secret → 500 WEBHOOK_NOT_CONFIGURED；路由 + 序列化 + Settings 链路通

#### 1.5 完成标志 ✅

选择一个真实 Python 仓库 Issue（或粘贴 URL）→ Crawler 抓取 + 异步入 profile_queue → Agent A 评估 → 用户决策 → Agent B 沙箱开发 + push → Agent C 评审（注入 profile）→ APPROVED → PR 创建 + 入库 → Webhook 接收 merge/close 事件 → Issue 终态 PR_MERGED/PR_CLOSED + 看板 PR 卡片正确着色。

#### Phase 1 端到端 dry run 验收 ✅ 2026-06-18

详见 `docs/DRY_RUN_PHASE1_REPORT.md`。

- 仓库 `wanxb/c-drive-cleaner` Issue #1，13 段流程 11 段通过验证；剩 2 段（PR 真实创建 / GitHub Webhook 真实推送）受用户 dev_token fork 权限限制无法走通，由 `review_worker.pr_skip_no_fork` 兜底路径正确触发
- 实测：6m 40s / $0.96（Agent B 26 turns / $0.92 / 3.7KB diff，对模糊 "优化UI细节" issue 仍输出 5 处实质改动）
- Provider fallback 在真实 Anthropic 503 下完整 work（→ DeepSeek）
- 暴露并修复 3 个真 bug（commit `21f0fcf`）：(a) asyncpg 跨 loop 复用 → worker NullPool；(b) 1.5a 的 lowercase enum 与 SQLAlchemy `.name` 序列化不匹配 → `pg_enum()` helper；(c) Claude Code CLI 2.1.179 要求 `--verbose`
- 补 PR 真实创建 / Webhook 真实接收：升级 dev_token 为 classic PAT with `repo` scope，或写 `scripts/replay_github_webhook.py` 本地模拟（Phase 2 边界处理时再做）

**Phase 1 至此完结，整个 MVP 主干流程跑通。**

**Phase 1 完成标志：** 选择一个真实 Python 仓库 Issue（或粘贴 URL 手动触发），系统能自动完成"profile 生成 → 评估 → 开发（注入 profile）→ 评审（对比 profile）→ 提交 PR"全流程，PR 链路结果与 Agent C 退回原因结构化入库。

---

## Phase 2 — 完善（稳定性 + 可观测性）

### 里程碑 2.1 — 多语言沙箱

- [ ] agent-sandbox-node（JavaScript/TypeScript）
- [ ] agent-sandbox-go
- [ ] agent-sandbox-rust
- [ ] agent-sandbox-java
- [ ] 语言自动检测（GitHub API languages 字段）
- [ ] 共享依赖缓存 volume（npm/pip/go mod）

### 里程碑 2.2 — 抓取配置管理

- [ ] crawl_targets 表 + CLI 管理脚本
- [ ] GitHub Trending 支持
- [ ] APScheduler 定时任务（可配置 cron）
- [ ] 抓取日志页（看板）

### 里程碑 2.3 — 边界处理 + 完整兜底 + PR 学习数据延展

- [x] **Webhook 本地回放工具**（2026-06-22）：`scripts/replay_github_webhook.py` 支持 merged / closed / review / comment / ping，可选 `--from-github` 拉真实 PR payload；端到端验证 PR_MERGED + PR_CLOSED + review_received + comment_received 4 条路径全跑通；`docs/PR_CREATION_TROUBLESHOOTING.md` 提供 fine-grained vs classic PAT、手动 fork 兜底、`pr_skip_no_fork` 触发条件说明
- [x] **Agent B 重试机制**（2026-06-22，max_dev_retry 默认 2）：dev_worker 失败路径在转 DEV_FAILED 后按 `decide_retry_or_archive(attempt, max_dev_retry)` 决定是否建新 DevTask(attempt_number+1, review_context=`build_failure_review_context(...)`) + `re_queue_dev`（DEV_FAILED → QUEUED_DEV，已在白名单）+ 入 `dev_queue`。复用 review_worker 的 decide 函数避免规则漂移。`build_failure_review_context` 把 prev failure_reason / failure_detail 写成 Agent B 可读 prompt 块 + 加 "若仍解不开请直接 report_failure 不要凑半成品" 的劝阻句。8 项 test_dev_retry.py（4 模板 + 4 边界）
- [x] **Agent C → Agent B 退回循环**（2026-06-22，最多 `max_review_retry=3` 次，超限 ARCHIVED）：`review_worker` REJECTED 路径分叉为 retry / archive；retry 时建新 `dev_task(attempt_number+1, review_context=...)` + `IssueService.re_queue_dev` + 入 `dev_queue`；超限时 `IssueService.mark_archived`。`build_review_context()` 把 5 维评分 + 失败维度评语 + rejection_reason 包成结构化 text 注入 Agent B prompt（已存在的 `<repo_profile>` + review_context 字段）。`_ALLOWED_FROM[ARCHIVED]` 新增 `REVIEW_REJECTED` 出口；15 项 `test_review_retry.py` 覆盖边界 + 模板 + 状态机白名单
- [x] **task 级 fallback**（2026-06-22）：`dev_tasks.use_fallback_provider Boolean` 列（migration `f5g9h5c9e5f5`）；触发：(a) dev_worker 整 task 失败 → 新 DevTask 总置 true；(b) review_worker C→B retry 时 `review_task.attempt_number >= 2` 才置 true（第 1 次只换 prompt，第 2 次才换模型）。`build_sandbox_llm_env(settings, use_fallback)` 读 `models.yaml.agent_b.fallback` 覆盖沙箱 Claude Code CLI 的 BASE_URL / AUTH_TOKEN / MODEL；fallback 配置或凭证缺失时优雅回落 primary（warning + 不阻塞任务）。6 项 test_task_fallback.py
- [x] **RejectionClassifier Agent**（2026-06-22，Haiku 4.5 / temperature 0 / Single-Shot + Tool Use）：消费 `classified_by IS NULL` 占位行，把 maintainer review/close 自由文本分类为 (category, severity, dimension, agent_b_attribution) 四元组 + 摘要理由。新建 `app/agents/rejection_classifier.py` harness + `prompts/rejection_classifier.py` + `tools.py::CLASSIFY_REJECTION_TOOL` + `models.yaml::rejection_classifier`。`classify_worker` Celery 任务 + 独立 `classify_queue`；webhook handler 在 commit 后批量 `send_task`。9 项 test_rejection_classifier.py（6 schema + 3 harness）+ 2 项 test_pr_tracker_rejection.py 新增 `pending_classify_ids` 收集校验；e2e replay 中文 review "请使用 snake_case" → `style_mismatch / major / code_style / yes` 正确标注（Anthropic 503 → DeepSeek fallback 全程自动）
- [x] **Webhook → `rejection_reasons` 占位入库**（2026-06-22）：PRTracker.on_review_received（changes_requested / commented + 非空 body）+ on_comment_received（非空 body）+ on_pr_closed(unmerged) 写一行 RejectionReason(source=MAINTAINER_REVIEW / MAINTAINER_CLOSE)，category=OTHER / severity=MINOR / attribution=UNCLEAR / classified_by=null 作占位，等 RejectionClassifier 二次分类。approved review 不写。6 项 test_pr_tracker_rejection.py + replay 4 事件端到端 3 行入库验证
- [ ] Revert 巡检：APScheduler 任务每日扫描 merged PR 是否被 revert，写 `pr_outcomes` (event_type=reverted_detected) 和 `pull_requests.final_outcome=REVERTED`
- [ ] STALE 自动归档（30 天无动作）
- [x] **PR 关闭后人工审核流程**（2026-06-22）：POST `/api/v1/issues/{id}/pr-closed-decide` 接 `{action: "restart_dev" | "archive"}`。restart_dev → 新 DevTask(review_context="Previous PR was closed by maintainer...") + `re_queue_dev`（PR_CLOSED → QUEUED_DEV）+ 入 dev_queue；archive → `mark_archived`（PR_CLOSED → ARCHIVED）。前端 PRPanel 在 PR_CLOSED 状态下展示「重新开发 / 归档」两按钮 + ApiError 行内显示。e2e curl 验证 restart_dev / archive / invalid_action / wrong_state 4 条路径
- [ ] 卡死检测完整实现（repeated_read / no_write 等模式）
- [ ] 上下文压缩（Agent B 长 loop 时触发）
- [x] **Schema 校验失败重试**（2026-06-22，Agent A / C / RejectionClassifier 三处）：抽出 `app/agents/_schema_retry.py::call_with_schema_retry`；第 1 次 schema fail 时第 2 次 call 追加 correction hint（"your previous output failed: ... re-call tool"）；两次仍失败抛 SchemaValidationError，all_attempts 合并供 persist_attempts 一次入库。ToolNotCalledError 不重试（深层不合作，再调徒劳）。4 项 test_schema_retry.py 覆盖：valid 不 retry / invalid→valid retry 一次 / 两次 invalid 抛错 / tool_not_called 立刻判错

### 里程碑 2.4 — 看板完善

- [ ] Issue 列表筛选 / 排序 / 搜索
- [ ] Issue 详情页（评估报告全视图）
- [ ] 开发进度步骤可视化（ANALYZE → PLAN → IMPLEMENT → TEST → COMMIT）
- [ ] 历次开发 / 评审历史记录
- [ ] PR 列表页（含 `final_outcome` 着色 + `rejection_reasons` 摘要面板）
- [ ] **PR 失败复盘视图**：按 category × Agent B 归因 聚合，Top N 失败模式可点击下钻到原始 PR
- [ ] 统计概览（各状态数量、今日新增、本周 fallback 触发率）

### 里程碑 2.5 — 多厂商模型支持

- [ ] OpenAI Client 实现
- [ ] DeepSeek Client 实现（OpenAI 兼容）
- [ ] 模型配置文件热加载
- [ ] 成本统计（token 用量 + 费用估算）

---

## Phase 3 — 优化（质量 + 扩展）

### 里程碑 3.1 — Prompt 质量提升（基于 PR 学习闭环）

- [ ] Agent A 离线评估框架（ground truth 对比）
- [ ] Agent B 成功率分析（按语言、按错误类型、按 fallback 触发与否）
- [ ] Agent C 误拒率分析（对比 `agent_c` 退回与最终 maintainer 判定的吻合度）
- [ ] Prompt A/B 测试流程
- [ ] **从 `rejection_reasons` 构建 Eval Golden Set**：
   - MERGED_CLEAN 案例 → 正样本
   - `agent_b_attribution=yes` + `severity=blocker` 案例 → Agent B 反例 few-shot
   - 高频 `category=style_mismatch` → Agent B "学习 CONTRIBUTING.md" 步骤强化
- [ ] 周报：Top 3 失败模式 + 每类型成本 + Agent B 归因比例

### 里程碑 3.2 — Agent B 能力增强

- [ ] devcontainer.json 支持（使用仓库自定义开发环境）
- [ ] 多步测试策略（unit → integration → e2e 按需运行）
- [ ] 修改影响范围分析（避免过宽修改）
- [ ] Extended Thinking 支持（复杂问题启用）
- [ ] **repo_profile 智能刷新**：基于 `rejection_reasons` 中 style_mismatch 频率自动触发强制刷新（已在 1.5 上数据基础，本期上策略）

### 里程碑 3.3 — 系统扩展

- [ ] E2B 沙箱切换（替代本地 Docker，提升启动速度）
- [ ] 多 GitHub 账号支持（隔离不同项目的 PR 来源）
- [ ] Issue 黑名单 / 仓库黑名单
- [ ] 导出报告（周报：本周评估/开发/PR 汇总）

---

## 技术债追踪

| 项目 | 优先级 | 说明 |
|------|--------|------|
| Agent B 测试 mock | 中 | 当前 Agent B 集成测试需要真实 Docker，未来补充 mock 层 |
| Prompt 版本迁移 | 低 | 历史评估数据的 prompt_version 字段为空，需要回填工具 |
| WebSocket 重连 | 中 | 前端 WebSocket 断开重连逻辑待完善 |
| Celery beat 高可用 | 低 | 单机部署暂用 APScheduler，规模扩大后迁移到 Celery beat |
| GitHub Rate Limit | 中 | 抓取量大时需要实现 rate limit 感知的请求节流 |

---

## 已知限制（长期不解决）

- 不支持需要本地数据库 / 第三方服务的 Issue（Agent B 无法搭建完整测试环境）
- PR 是否被 merge 取决于 maintainer，系统无法控制
- 极度复杂的重构类 Issue 成功率低，评估时 feasibility 维度会反映
