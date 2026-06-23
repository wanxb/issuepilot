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

### 里程碑 2.1 — 多语言沙箱 ✅ 已完成 2026-06-23

- [x] **重构 sandbox/shared/**：entrypoint.sh + mcp_report_server.py 抽到公共目录；5 个语言 Dockerfile 都 `COPY shared/...`，build context 改 `sandbox/`，scripts/build_sandbox_images.sh 一键构建
- [x] **agent-sandbox-node**（JavaScript/TypeScript）：node:20-bookworm-slim 基底 + Claude Code CLI + pnpm/yarn + Python venv (MCP server)；缓存 /cache/{npm,pnpm,yarn}
- [x] **agent-sandbox-go**：golang:1.23-bookworm + Node20 + Python venv；缓存 GOPATH/GOMODCACHE/GOCACHE 全分离
- [x] **agent-sandbox-rust**：rust:1.83-bookworm + Node20 + Python venv；缓存 CARGO_HOME/RUSTUP_HOME/CARGO_TARGET_DIR；额外装 pkg-config + libssl-dev 支持 openssl crate
- [x] **agent-sandbox-java**：eclipse-temurin:21-jdk + Maven (apt) + Gradle 8.10.2 (官方 zip) + Node20 + Python venv；缓存 MAVEN_OPTS/GRADLE_USER_HOME
- [x] **语言自动检测（GitHub API languages 字段）**：`app/sandbox/language.py::resolve_sandbox_lang` 映射 GitHub primary_language（JS/TS/Vue/Svelte→node；Go→go；Rust→rust；Java/Kotlin/Scala/Groovy→java；Python/Cython→python；其他→python fallback）；`sandbox_image_for_language` 双重 fallback（镜像不存在或检查抛异常 → python）
- [x] **共享依赖缓存 volume**（npm/pip/go mod/cargo/maven）：每个 Dockerfile 在 /cache/ 下创建对应子目录 + 设环境变量；运行时由 dev_worker 容器挂载（后续可配置 host volume 跨任务复用）
- [x] dev_worker._setup_phase 接入 `sandbox_image_for_language`（替代原 lower() + image_exists 内联逻辑）
- [x] 28 项 test_sandbox_language.py（12 语言映射 + 5 image 函数边界 + 5 supported 集合校验 + 6 其他边界）；总 unit suite 245 passed
- [x] **未实地构建镜像**：Docker Desktop 本日不可用；构建路径已在 scripts/build_sandbox_images.sh 中固化，下次 docker 可用时 `bash scripts/build_sandbox_images.sh` 一键产出所有 5 镜像。dev_worker 在缺镜像时自动回落 python，不阻塞 Phase 1+2.3 已验证的功能

### 里程碑 2.2 — 抓取配置管理 ✅ 已完成 2026-06-22

- [x] **crawl_targets 表**：新表 `crawl_targets`（name / source / spec JSONB / cron / enabled / last_run_at / last_status / last_error）+ alembic migration `f6h0i6d0f6g6`
- [x] **CLI 管理脚本**：`scripts/manage_crawl_targets.py` 5 子命令（list / add / enable / disable / delete / run-now）；docker-compose 加 `./scripts:/scripts:ro` 让容器内可用；脚本兼容 host 与容器布局
- [x] **GitHub Trending source**：`app/services/crawl_sources.py` 解析 `https://github.com/trending/{lang}?since=daily|weekly|monthly` 拿 owner/repo 列表；支持 `github_trending` + `explicit_repos` 两种 source；spec schema 校验在 service 层
- [x] **APScheduler 定时任务（可配置 cron）**：扩展 `app/scheduler.py`，启动后异步加载所有 enabled targets 注册 cron job（`crawl_target:<uuid>` 命名）；`POST /api/v1/admin/scheduler-reload` 热刷新（外部修改 DB 后调）；`scheduled_crawl_worker.run_target` Celery 任务在 worker 容器执行实际抓取，updates `last_run_at / last_status`
- [x] **抓取日志页（看板）**：`GET /api/v1/crawl-jobs?limit=N` 返回最近 N 条；前端 `CrawlJobsLog` 组件 8s 轮询，按 trigger / status badge / 来源 / 入队数 / 复用数 / 耗时 / 时间列展示
- [x] **修了一个真 bug**：`_upsert_issues` 在 commit 前就 send_task 到 analyze_queue 导致 analyze_worker 比 outer commit 更快读到 issue → `issue_missing` 失败。新增 `enqueue_now: bool` 参数 + `TargetCrawlOutcome.pending_analyze_ids` 让 scheduled_crawl_worker 在 commit 后批量入队（manual URL 路径保留原行为，单 issue 不易触发 race）
- [x] **e2e 验证**：disable→enable→run-now → 11 trending Python 仓库 → 179 issue 入库 → 16 已被 Agent A 评估（DeepSeek fallback 全程接管，Anthropic 中转此时仍 503）；CLI list 查到 last_status=succeeded，attempted=11/ok=11/enqueued=179；前端抓取日志页正常显示
- [x] 23 项单测（15 sources + 8 service）；总 unit suite 198 passed

### 里程碑 2.3 — 边界处理 + 完整兜底 + PR 学习数据延展 ✅ 已完成 2026-06-22

> **学习闭环自洽 + 验收 dry run 通过。** 13 个子项全部交付 + 15 项端到端验收矩阵全绿，详见 `docs/DRY_RUN_PHASE2_3_REPORT.md`。9 个 commit 完成；总 unit suite 175 passed。

- [x] **Webhook 本地回放工具**（2026-06-22）：`scripts/replay_github_webhook.py` 支持 merged / closed / review / comment / ping，可选 `--from-github` 拉真实 PR payload；端到端验证 PR_MERGED + PR_CLOSED + review_received + comment_received 4 条路径全跑通；`docs/PR_CREATION_TROUBLESHOOTING.md` 提供 fine-grained vs classic PAT、手动 fork 兜底、`pr_skip_no_fork` 触发条件说明
- [x] **Agent B 重试机制**（2026-06-22，max_dev_retry 默认 2）：dev_worker 失败路径在转 DEV_FAILED 后按 `decide_retry_or_archive(attempt, max_dev_retry)` 决定是否建新 DevTask(attempt_number+1, review_context=`build_failure_review_context(...)`) + `re_queue_dev`（DEV_FAILED → QUEUED_DEV，已在白名单）+ 入 `dev_queue`。复用 review_worker 的 decide 函数避免规则漂移。`build_failure_review_context` 把 prev failure_reason / failure_detail 写成 Agent B 可读 prompt 块 + 加 "若仍解不开请直接 report_failure 不要凑半成品" 的劝阻句。8 项 test_dev_retry.py（4 模板 + 4 边界）
- [x] **Agent C → Agent B 退回循环**（2026-06-22，最多 `max_review_retry=3` 次，超限 ARCHIVED）：`review_worker` REJECTED 路径分叉为 retry / archive；retry 时建新 `dev_task(attempt_number+1, review_context=...)` + `IssueService.re_queue_dev` + 入 `dev_queue`；超限时 `IssueService.mark_archived`。`build_review_context()` 把 5 维评分 + 失败维度评语 + rejection_reason 包成结构化 text 注入 Agent B prompt（已存在的 `<repo_profile>` + review_context 字段）。`_ALLOWED_FROM[ARCHIVED]` 新增 `REVIEW_REJECTED` 出口；15 项 `test_review_retry.py` 覆盖边界 + 模板 + 状态机白名单
- [x] **task 级 fallback**（2026-06-22）：`dev_tasks.use_fallback_provider Boolean` 列（migration `f5g9h5c9e5f5`）；触发：(a) dev_worker 整 task 失败 → 新 DevTask 总置 true；(b) review_worker C→B retry 时 `review_task.attempt_number >= 2` 才置 true（第 1 次只换 prompt，第 2 次才换模型）。`build_sandbox_llm_env(settings, use_fallback)` 读 `models.yaml.agent_b.fallback` 覆盖沙箱 Claude Code CLI 的 BASE_URL / AUTH_TOKEN / MODEL；fallback 配置或凭证缺失时优雅回落 primary（warning + 不阻塞任务）。6 项 test_task_fallback.py
- [x] **RejectionClassifier Agent**（2026-06-22，Haiku 4.5 / temperature 0 / Single-Shot + Tool Use）：消费 `classified_by IS NULL` 占位行，把 maintainer review/close 自由文本分类为 (category, severity, dimension, agent_b_attribution) 四元组 + 摘要理由。新建 `app/agents/rejection_classifier.py` harness + `prompts/rejection_classifier.py` + `tools.py::CLASSIFY_REJECTION_TOOL` + `models.yaml::rejection_classifier`。`classify_worker` Celery 任务 + 独立 `classify_queue`；webhook handler 在 commit 后批量 `send_task`。9 项 test_rejection_classifier.py（6 schema + 3 harness）+ 2 项 test_pr_tracker_rejection.py 新增 `pending_classify_ids` 收集校验；e2e replay 中文 review "请使用 snake_case" → `style_mismatch / major / code_style / yes` 正确标注（Anthropic 503 → DeepSeek fallback 全程自动）
- [x] **Webhook → `rejection_reasons` 占位入库**（2026-06-22）：PRTracker.on_review_received（changes_requested / commented + 非空 body）+ on_comment_received（非空 body）+ on_pr_closed(unmerged) 写一行 RejectionReason(source=MAINTAINER_REVIEW / MAINTAINER_CLOSE)，category=OTHER / severity=MINOR / attribution=UNCLEAR / classified_by=null 作占位，等 RejectionClassifier 二次分类。approved review 不写。6 项 test_pr_tracker_rejection.py + replay 4 事件端到端 3 行入库验证
- [x] **Revert 巡检**（2026-06-22，daily 03:17 UTC）：扫 lookback 30d 内 MERGED PR，GitHub commits API since merged_at 找 `^Revert\b` 标题 + 引用 merge_sha[:7] 或 `#pr_number` 的提交；命中则 `pull_requests.final_outcome=REVERTED` + `pr_outcomes(event_type=reverted_detected, payload={revert_sha, message})`。漏检 / 误检（跨 100+ commit、自定义模板）留给 3.x
- [x] **STALE 自动归档**（2026-06-22，daily 03:07 UTC）：扫 `status=IGNORED AND updated_at < now - 30d` → `mark_archived(reason=stale_ignored_over_30_days)`
- [x] **APScheduler infra**（2026-06-22）：`app/scheduler.py::AsyncIOScheduler` 在 FastAPI lifespan 启动；`app/workers/maintenance_worker.py` 提供 `stale_archive_scan` / `revert_scan` Celery 任务（跑在 worker 容器，复用 classify_queue）；scheduler 在 API 容器 send_task 触发。`/api/v1/admin/run-maintenance?job=...` + `/scheduler-jobs` 用于手动触发与诊断。10 项 test_maintenance.py + e2e: stale 1 row → ARCHIVED，revert checked=1 reverted=0（dry run PR 未被实际 revert）
- [x] **PR 关闭后人工审核流程**（2026-06-22）：POST `/api/v1/issues/{id}/pr-closed-decide` 接 `{action: "restart_dev" | "archive"}`。restart_dev → 新 DevTask(review_context="Previous PR was closed by maintainer...") + `re_queue_dev`（PR_CLOSED → QUEUED_DEV）+ 入 dev_queue；archive → `mark_archived`（PR_CLOSED → ARCHIVED）。前端 PRPanel 在 PR_CLOSED 状态下展示「重新开发 / 归档」两按钮 + ApiError 行内显示。e2e curl 验证 restart_dev / archive / invalid_action / wrong_state 4 条路径
- [x] **卡死检测**（2026-06-22）：`app/agents/stuck_detector.py::StuckDetector` 滑动窗口（`agent_b_stuck_window` 默认 12）；窗口被 READ_ONLY_TOOLS{Read/Glob/Grep/WebFetch/WebSearch/TodoWrite} 填满且无 MUTATING_TOOLS{Write/Edit/NotebookEdit/Bash/MultiEdit} 时判定 stuck。dev_worker sandbox_phase 监测 `parsed.tool_name` 触发；命中时 `_sandbox.stop` + DevLog ERROR + `failure_reason=stuck`，复用现有 retry 链路。`AgentB.parse_stream_line` 新增 `tool_name` 字段，自动去掉 MCP 前缀且过滤 report_* 终止工具。16 项 test_stuck_detector.py（窗口边界 + 工具识别 + MCP 前缀 + report 工具过滤）
- [x] **上下文压缩**（2026-06-22）：交给 Claude Code CLI 内置 auto-compact，不自建。决策记录到 `docs/AGENT_RUNTIME.md::"Agent B 上下文压缩（2.3）"`。三道防线：`--max-turns 25` 硬上限 + CLI 自压缩 + 卡死检测主动 kill
- [x] **Schema 校验失败重试**（2026-06-22，Agent A / C / RejectionClassifier 三处）：抽出 `app/agents/_schema_retry.py::call_with_schema_retry`；第 1 次 schema fail 时第 2 次 call 追加 correction hint（"your previous output failed: ... re-call tool"）；两次仍失败抛 SchemaValidationError，all_attempts 合并供 persist_attempts 一次入库。ToolNotCalledError 不重试（深层不合作，再调徒劳）。4 项 test_schema_retry.py 覆盖：valid 不 retry / invalid→valid retry 一次 / 两次 invalid 抛错 / tool_not_called 立刻判错

### 里程碑 2.4 — 看板完善 ✅ 已完成 2026-06-22

- [x] **Issue 列表筛选 / 排序 / 搜索**：后端 `GET /api/v1/issues` 加 `q` 参数（title ILIKE）；前端 IssuesList 加状态分组按钮（待评估/待决策/开发中/评审中/PR中/已完成 6 组多选）+ 标题搜索 + 语言 + 最低分 + 排序下拉 + 分页（上/下一页）
- [x] **Issue 详情页（/issues/[id]）**：后端 `GET /api/v1/issues/{id}/detail` 返回 issue + repo + evaluation + dev_tasks[] + review_tasks[] + pull_request + rejection_reasons[] 一次性完整 payload；前端 5 个 panel
- [x] **开发进度步骤可视化（ANALYZE → PLAN → IMPLEMENT → TEST → COMMIT）**：详情页 `DevProgress` 组件按 dev_task.status 推断当前 phase，5 段进度条 emerald 标已完成、red 标失败
- [x] **历次开发 / 评审历史记录**：详情页 `DevTaskRow` + `ReviewTaskRow` 列出所有 attempt，含 fallback / push / loop_iterations / 5 维评分小卡 / 退回意见 / 失败原因
- [x] **PR 失败复盘视图**（dashboard 顶部 section）：`GET /api/v1/dashboard/pr-failures?days=N` 按 (category × agent_b_attribution) 聚合 + by_source / by_dimension / Top 10 失败模式 + Top 5 模式的样本 PR；前端 `PRFailuresPanel` 7d/30d/90d 切换 + 折叠展开样本
- [x] **统计概览**（dashboard 顶部 section）：`GET /api/v1/dashboard/stats` 含各状态计数、今日新增、本周 PR_MERGED/PR_CLOSED、本周 LLM 调用 / 成本 / fallback 率 / 失败率；前端 `StatsOverview` 6 状态分组卡 + 4 统计卡
- [x] PR 列表页：未单独建页（合并进 PR 失败复盘视图 + 详情页 PR panel；专门 PR 视图后续需要时再加）
- [x] 3 项 test_dashboard_routes.py（路由注册 + endpoint shape）；总 unit suite 217 passed

### 里程碑 2.5 — 多厂商模型支持 ✅ 已完成 2026-06-22

- [x] **OpenAI Client 实现**：`app/llm/openai_client.py` 用 httpx（不引 openai SDK 避免大包）实现 Chat Completions + tools；翻译层把 Anthropic 的 `ToolDefinition` 转 OpenAI function schema，把 OpenAI `tool_calls` 反译为我们的 `type=tool_use` content blocks；finish_reason 映射；错误分类（408/429/5xx → retriable）
- [x] **DeepSeek Client 实现（OpenAI 兼容）**：复用 OpenAIClient，factory 加 `deepseek_openai` provider；models.yaml 用 `provider: deepseek_openai` + `provider_label: deepseek`（让 llm_call_logs 写 `deepseek` 与 Anthropic-compat 路径合并统计）
- [x] **模型配置文件热加载**：`POST /api/v1/admin/reload-models` 清 `_load_yaml.lru_cache` 后下一次 `build_client` 读新文件；返回 agent 列表确认
- [x] **成本统计（token 用量 + 费用估算）**：DeepSeek 价目（V4-Pro / V3 / chat / reasoner）补入 `_PRICING_PER_MILLION`；历史 21 条 DeepSeek 日志 UPDATE 回填（$0.0383 total）；`GET /api/v1/admin/cost-stats?hours=N` 按 agent_kind × provider × model 聚合 + fallback / failure 计数；前端 `CostStatsPanel` 30s 轮询 + 24h/7d/30d 切换
- [x] 16 项 test_openai_client.py（tool 翻译 + 响应反译 + finish_reason 映射 + factory dispatch）；总 unit suite 214 passed

---

## Phase 4 — 抓取算法升级（按领域定向）

### 里程碑 4.3 — 低分自动归档 ✅ 已完成 2026-06-23

- [x] **Settings.auto_ignore_low_score**（默认 true，env `AUTO_IGNORE_LOW_SCORE=false` 可关）
- [x] **analyze_worker 评估完追加判定**：`finish_analyzing` 写 evaluation + 转 PENDING_DECISION 后，若 `is_worth_developing=false`（即 total_score < 6.5）→ 立即 `svc.decide(action="ignore")` → IGNORED，不再卡看板
- [x] **历史 backfill 一次性**：85 个旧 PENDING_DECISION + worth=false 一并清成 IGNORED；终态 PENDING_DECISION 留下 114 个真值得做的 issue（avg score 7.64）
- [x] **3 项 test_auto_ignore.py**：settings 默认 + env override + state machine 允许 PENDING_DECISION → IGNORED；总 unit suite 365 passed

### 里程碑 4.2 — 同 provider 多 model 轮换（穷尽再换厂商）✅ 已完成 2026-06-23

- [x] **FallbackLLMClient 接受 primary_chain**：`__init__` 同时支持 `primary: LLMClient`（向后兼容）和 `primary: list[LLMClient]`（新）；`primary_chain` 属性始终是 list 形式
- [x] **call() 三段编排**：①按 chain 顺序逐 model；②每 model 内部按 RetryConfig 重试；③该 model 全部 retry 用完 → log `llm.model_rotation` → 切下一个 model；④整条 chain 用完 → log `llm.fallback_switching` → 跨 provider fallback；⑤永久错误（401/400/422）立刻 raise，不轮换不 fallback
- [x] **attempt_number 全局递增**：跨 chain + fallback 严格单调，避免落 llm_call_logs 主键冲突
- [x] **factory 读 `model_fallbacks`**：models.yaml 新增 list[str] 字段（同 provider 多 model）；`_build_primary_chain` 复用 cfg 的 auth/base_url 只换 model 名构造
- [x] **5 个 agent 默认链**（写入 models.yaml + models.example.yaml）：
  - agent_a/b/c/d: `claude-sonnet-4-6 → claude-opus-4-7 → claude-haiku-4-5-20251001 → [fallback] DeepSeek-V4-Pro`
  - rejection_classifier: `claude-haiku-4-5 → claude-sonnet-4-6 → [fallback] DeepSeek-V4-Pro`
- [x] **8 项 test_model_rotation.py**：rotate / 全 chain → fallback / permanent 立刻挂 / 首次成功 / chain exhausted 无 fallback / 向后兼容 / attempt_number 单调 / factory chain 构造；总 unit suite 361 passed

### 里程碑 4.1 — Domain 白名单（AI Agent / LLM / 机器人）✅ 已完成 2026-06-23

- [x] **3 个领域 registry**：`app/services/crawl_domain.py::DOMAIN_REGISTRY`
  - `ai_agent`：MCP / Agent 框架 / 多 agent / 工具调用 / LangChain/LangGraph/AutoGen 等 25 个 topics + 关键词正则
  - `llm`：LLM / RAG / fine-tune / embedding / vector DB / 主流模型厂商 30 个 topics + 关键词正则
  - `robotics`：ROS/ROS2 / 自动驾驶 / SLAM / 具身智能 / humanoid / 强化学习 25 个 topics + 关键词正则
- [x] **match_repo 双重命中**：topic 命中（高置信度）优先；description/name keyword regex 兜底；`signal` 字段返回是哪条规则触发
- [x] **GitHub Search API source**（`github_search`）：用 `_FLAGSHIP_TOPICS`（每 domain 4 个旗舰 topic）单独 query + 合并去重；规避 GitHub Search 的"qualifier 之间不支持 OR"和"5 OR 上限"限制；qualifiers 强制 `stars:>=N archived:false is:public pushed:>D`
- [x] **CrawlerService.from_target 后置过滤**：`spec.domains` 非空时，每个 repo fetch 后调 match_repo，未命中 → `outcome.failures["off_topic:..."]` 跳过 + log
- [x] **历史数据清洗**：旧 trending-python-daily 留下的 159 个 DISCOVERED → match 后 54 OFF-TOPIC 直接 ARCHIVED / 105 in-scope 重派 analyze_issue
- [x] **新 in-domain target seed**：`ai-agent-llm-search`（domains: ai_agent+llm, sort:stars, min_stars:500, pushed_within_days:90）+ `robotics-search`（domains: robotics, min_stars:300, pushed_within_days:120），cron 05:00/05:15 UTC
- [x] **CLI 支持新 source**：scripts/manage_crawl_targets.py choices 加 github_search
- [x] **23 项单测**：domain registry 边界 + match topic/keyword/scope + validate_domains + build_search_query + existing test_crawl_sources 兼容更新；总 unit suite 353 passed
- [x] **质量飞跃验证**：新 search 命中 elizaOS/eliza, google/adk-python, google-gemini/gemini-cli, n8n-io/n8n, bytedance/deer-flow, NousResearch/hermes-agent 等真高质量 in-domain 仓库；evaluation top-10 全是 8.0+ 真实 bug/feature（hermes auth / gateway agent cache / model picker / SSH backend 等）

---

## Phase 3 — 优化（质量 + 扩展）

### 里程碑 3.1 — Prompt 质量提升（基于 PR 学习闭环）✅ 已完成 2026-06-23

- [x] **Eval Golden Set 表 + CLI**：新表 `eval_samples`（issue_id, sample_kind, label, source, notes）+ migration `g7i1j7e1g7h7`；`scripts/manage_eval_samples.py` 5 子命令（list / add / auto-pick / remove）；auto-pick 支持 3 个 kind：positive_merged_clean（取 MERGED_CLEAN PR）/ negative_agent_b_fault（agent_b_attribution=yes + severity=blocker）/ style_pattern（category=STYLE_MISMATCH）
- [x] **周报 endpoint + 前端 widget**：`GET /api/v1/dashboard/weekly-report?days=N` 返 Top 3 失败模式 + 每 agent 类型成本（calls / tokens / cost / fallback）+ Agent B 归因比例（含进度条数据）+ Issue 漏斗（DISCOVERED→PR_MERGED 10 阶段）+ PR 终态。`WeeklyReportPanel` 前端组件 7d/14d/30d 切换 + 5 section（3 列网格 + 2 全宽 section）
- [x] **Agent B 成功率 / Agent C 误拒率分析**：`GET /api/v1/dashboard/agent-quality?days=N` 返：
  - agent_b：by_status / fallback_split（primary vs fallback 分桶）/ by_language（join repositories）/ by_failure_reason
  - agent_c：by_verdict + approved_merged_clean / approved_closed_by_maintainer 交叉计数（Maintainer agreement）
- [x] **Agent A 离线评估框架**：`scripts/eval_agent_a.py run --kind X --limit N [--dry-run]`；从 eval_samples 拉样本，重跑 Agent A，与历史 evaluation 对比 score delta 与 recommend agreement（match / diverge_pos / diverge_neg / no_old）；JSON 行流出 stdout 便于 jq / 二次分析
- [x] **Prompt A/B 测试流程**（轻量）：复用既有 `Evaluation.prompt_version` + `LLMCallLog.prompt_version` 字段；离线评估脚本天然支持版本对比（改 prompt → rerun → diff agreement 列）
- [x] **从 `rejection_reasons` 构建 Eval Golden Set** 自动化：`auto-pick --kind` 三条路径已实装。dry run 时：0 negative_agent_b_fault（无 blocker） / 1 style_pattern 自动收录
- [x] 4 项 test_eval_routes.py（router 注册 + EvalSample 模型 + 字段构造 + 路径覆盖）；总 unit suite 249 passed
- [x] e2e: weekly-report 实拉 84 calls / $0.038346 / 1 top failure mode；agent-quality 实拉 1 dev_task SUCCEEDED Python primary / 1 APPROVED review；dry-run eval_agent_a 输出 1 行 JSON

### 里程碑 3.2 — Agent B 能力增强 ✅ 已完成 2026-06-23

- [x] **devcontainer.json 支持**（2026-06-23）：`sandbox/shared/apply_devcontainer.py` 用纯 stdlib 解析 JSONC（剥行注释/块注释/末逗号、保留字符串内 `//`、转义引号）；支持 `name / containerEnv / postCreateCommand / postStartCommand`（string/list/dict 三种形态扁平化），不支持的 `image / dockerfile / build / features / forwardPorts` 输出 stderr warning 但不阻塞。entrypoint.sh 在 clone 后 `eval` 脚本输出注入 env + 顺序执行 postCreateCommand/postStartCommand（best-effort，失败不杀 task），并 `export AGENT_B_HAS_DEVCONTAINER=1` 让 Agent B 系统提示能感知。5 个 Dockerfile 都加 `COPY shared/apply_devcontainer.py`；docker-compose 在 api 容器挂 `./sandbox:/sandbox:ro` 让单测能 import 脚本验证。10 项 test_devcontainer.py（5 JSONC + 5 flatten_cmd）
- [x] **多步测试策略（unit → integration → e2e 按需运行）**（2026-06-23）：Agent B SYSTEM_PROMPT 升级到 v1.1，TEST 阶段从"一刀切 `python -m pytest -q`"改成 Tier 1/2/3 分级策略（Tier 1：仅 touch 模块的 unit；Tier 2：变更跨模块时跑完整包 unit；Tier 3：tests/integration/ 或 tests/e2e/ 存在且 Tier 2 过时选择性跑，外部服务依赖时 SKIP 并在 test_output_snippet 注明）。`PROMPT_VERSION="1.0"→"1.1"`。6 项 test_agent_b_prompt_3_2.py（版本 bump + devcontainer env 提示 + Tier 1/2/3 + SKIP 指导 + 旧版本被替换 + build_prompt 不挂）
- [x] **修改影响范围分析**（2026-06-23）：`app/sandbox/scope_check.py` 用启发式正则从 issue body + evaluation summary 抽看似文件路径的 token，与 dev_task.files_changed 做 basename 大小写不敏感比对；suspicious 触发 = 都非空 + 完全无 basename 重叠。dev_worker 成功路径写 `dev_tasks.scope_check` JSONB；review_worker 把警告注入 AgentCInput.scope_warning → Agent C prompt `<scope_warning>` XML 块。migration `h8j2k8f2h8i8`。13 项 test_scope_check.py
- [x] **Extended Thinking 支持**（2026-06-23）：`app/sandbox/extended_thinking.py::should_enable_extended_thinking` 按 (evaluation.difficulty 命中 `agent_b_extended_thinking_difficulties` 默认 "hard") 或 (attempt_number >= `agent_b_extended_thinking_min_attempt` 默认 2) 判定；dev_worker setup_phase 写 ENABLE_EXTENDED_THINKING env 到沙箱；entrypoint.sh 读 env 后用 `--append-system-prompt` 注入 "Before each tool call, briefly think step-by-step" 指令（Claude Code CLI 暂未暴露原生 thinking budget flag，先 prompt 层引导，模型 native thinking 后续 CLI 支持时切换）。7 项 test_extended_thinking.py
- [x] **repo_profile 智能刷新**（2026-06-23）：`app/services/profile_refresh.py::maybe_force_refresh` 在 classify_worker 写完 style_mismatch + agent_b_attribution=yes 标签后调；近 30 天该 repo 同类计数 >= 3 → 设 RepoProfile.expires_at=now + forced_refresh_count++ + commit 后 send_task profile_queue（force=True）。E2E 实测：wanxb/c-drive-cleaner 当前 1 < 3 不触发，行为正确。3 项 test_profile_refresh.py

### 里程碑 3.3 — 系统扩展 ✅ 已完成 2026-06-23（含 E2B deferred 决策）

- [deferred] **E2B 沙箱切换**：决策文档 `docs/SANDBOX_E2B_EVAL.md` 记录暂不切换的 4 条理由（启动速度占比 <2% 不是瓶颈 / 5 镜像构建已就位 / vendor lock-in / 月度成本）+ 4 个未来触发条件（task 量 >1000/月 / GUI 自动化 / SaaS 多租户 / E2B 50% 降价）+ 工作量评估（≈1 周）
- [x] **多 GitHub 账号支持**（2026-06-23）：新表 `github_accounts`（name / role(crawler|dev|webhook) / token / username / enabled / last_used_at）+ migration `j0l4m0h4j0k0`；`GitHubAccountService` 提供 LRU 选择（无 sticky）或 sha1-modulo sticky-by-repo 选择；`scripts/manage_github_accounts.py` list/add/pick/enable/disable/delete 6 子命令 + token mask 显示。e2e 实测：sticky `owner/repo1` 确定性落到 dev-bot-2；LRU 在 2 账号下交替；5 项单测
- [x] **Issue 黑名单 / 仓库黑名单**（2026-06-23）：新表 `blacklist`（entity_type repo|issue / pattern / reason / enabled）+ migration `i9k3l9g3i9j9`；`BlacklistService` 用 fnmatch glob 支持 `owner/*` `*/repo` `owner/repo#42` 等模式；CrawlerService 在 `_handle_repo_url` / `_handle_issue_url` / `from_target` 三个入口前置检查（manual 命中抛 Blacklisted 403，cron 命中 outcome.failures 记录跳过）；admin API `GET/POST/DELETE /api/v1/admin/blacklist` + CLI `scripts/manage_blacklist.py`。e2e 实测 `spammer/*` 命中 spammer/foo+spammer/bar，repo 黑等价 issue 黑（spammer/foo#1 也阻断）；16 项单测
- [x] **导出报告**（2026-06-23）：`app/services/report_export.py::render_weekly_report_markdown` 把 `/dashboard/weekly-report` payload 渲染成 GitHub-flavor Markdown（总览 / Top 3 失败 / Agent 成本表 / 归因比例 / Issue 漏斗按 status 顺序 / PR 终态 6 个 section）；`GET /api/v1/dashboard/weekly-report.md` 返 PlainTextResponse；CLI `scripts/export_weekly_report.py [--days 7] [--out reports/] [--stdout]` 落盘 `reports/weekly-YYYY-MM-DD.md`。e2e 实测产出 ~1.5KB 完整 Markdown；7 项单测

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
