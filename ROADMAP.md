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

#### 1.5d — PR 创建 + PR_SUBMITTED 链路

- [ ] PR Service（GitHub API 创建 PR；处理 head 推送 + base_branch 解析）
- [ ] dev_worker / review_worker 接力：APPROVED → 推送 fork 分支 → 创建 PR → 写 `pull_requests` + pr_outcomes(submitted)
- [ ] Issue 状态流转：IN_REVIEW → PR_SUBMITTED

#### 1.5e — Webhook 接收 + PRTracker + 看板

- [ ] GitHub Webhook 端点（HMAC-SHA256 验签）+ pull_request / pull_request_review / issue_comment 事件路由
- [ ] PRTracker Service：写 pr_outcomes + 更新 `pull_requests.status` / `final_outcome`
- [ ] Issue 状态流转：PR_SUBMITTED → PR_MERGED / PR_CLOSED
- [ ] 看板 PR 状态卡片（final_outcome 着色 + PR 链接）
- [ ] **rejection_reasons 表**：Agent C 退回时写 source=agent_c 记录（含 dimension + agent_b_attribution）
- [ ] Issue 状态流转（PR_SUBMITTED → PR_MERGED / PR_CLOSED）
- [ ] `pull_requests.final_outcome` 字段写入（MERGED_CLEAN / CLOSED_BY_MAINTAINER / ...）
- [ ] 看板 PR 状态显示

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

- [ ] Agent B 重试机制（携带失败原因重入，最多 2 次）
- [ ] Agent C → Agent B 退回循环（最多 3 次，超限 ARCHIVED）
- [ ] **task 级 fallback**：Agent B 整 task 失败 / Agent C 退回 ≥2 次时，下次 dev 切换 fallback provider（混合策略剩余部分）
- [ ] **RejectionClassifier Agent**（Haiku 4.5）：把 maintainer review/close 自由文本分类为 category/severity/dimension/agent_b_attribution
- [ ] Webhook：pull_request_review + issue_comment 写入 `rejection_reasons` (source=maintainer_review/maintainer_close)
- [ ] Revert 巡检：APScheduler 任务每日扫描 merged PR 是否被 revert，写 `pr_outcomes` (event_type=reverted_detected) 和 `pull_requests.final_outcome=REVERTED`
- [ ] STALE 自动归档（30 天无动作）
- [ ] PR 关闭后人工审核流程（重新开发 / 归档）
- [ ] 卡死检测完整实现（repeated_read / no_write 等模式）
- [ ] 上下文压缩（Agent B 长 loop 时触发）
- [ ] Schema 校验失败重试（Agent A / C）

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
