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

### 里程碑 1.4 — Agent B（Docker 沙箱 + 开发）

- [ ] Docker 沙箱管理器（启动/停止/超时）
- [ ] agent-sandbox-python 镜像（首个语言，用于验证）
- [ ] GitHub Fork + Clone 逻辑
- [ ] Agent B Harness（ReAct Loop，含卡死检测基础版）
- [ ] Claude Code CLI headless 模式集成
- [ ] dev_worker（Celery）
- [ ] dev_logs 实时写入
- [ ] WebSocket 日志推送（Redis PubSub → 前端）
- [ ] 看板开发进度展示（基础日志流）

### 里程碑 1.5 — Agent C + Agent D（RepoOnboarding）+ PR 提交 + PR 链路数据采集

- [ ] Agent C Harness（SingleShotLoop）
- [ ] Agent C Prompt v1.0（system prompt 注入 `repo_profile.code_style_notes`，`code_style` 评分直接对比 profile）
- [ ] review_worker（Celery）
- [ ] **Agent D Harness**（SingleShotLoop，复用 agent-sandbox-{lang} 镜像，窄 allowedTools）
- [ ] **Agent D Prompt v1.0**
- [ ] **`repo_profiles` 表 + profile_queue + profile_worker**（异步生成，TTL 90 天）
- [ ] **CrawlerService 在 upsert 新 repo 时入 profile_queue**（不阻塞 analyze_queue）
- [ ] **Agent B system prompt 注入 profile**（profile 缺失时降级自学习，PoC 流程不变）
- [ ] PR Service（GitHub API 创建 PR）
- [ ] GitHub Webhook 接收（PR merge / close / review）
- [ ] **PRTracker Service**——把 PR 生命周期事件写入 `pr_outcomes` 表
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
