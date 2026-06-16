# Roadmap

## 总体策略

分三个阶段，每阶段交付可独立运行的产物：

- **Phase 1（MVP）**：跑通主干流程，人工干预点明确，可用但粗糙
- **Phase 2（完善）**：补全边界处理，提升稳定性和可观测性
- **Phase 3（优化）**：提升 Agent 质量，降低成本，支持扩展

---

## Phase 1 — MVP（主干流程跑通）

**目标：** 能从 Issue 发现到 PR 提交完整跑通一个真实案例

### 里程碑 1.0 — Agent B PoC 验证（开发前置）

> **必须在其他里程碑开始前完成。** Agent B 使用 Claude Code CLI headless 模式是整个架构最核心的假设，MVP 能否成立取决于此。

- [ ] 验证 Claude Code CLI headless 模式的基本调用方式（`--print` / `--output-format json`）
- [ ] 验证 `--allowedTools` 参数是否能限制 Agent B 的工具访问范围
- [ ] 验证 Docker 容器内 Claude Code CLI 能否正常运行（网络、认证）
- [ ] 验证 Issue body 含 Prompt Injection 内容时，Tool Use 结构化输出是否仍然稳定
- [ ] 验证 `report_completion` / `report_failure` 自定义工具能否注入并被模型调用
- [ ] 输出 PoC 报告，决策是否继续使用该方案或切换（如 Anthropic SDK 直接调用）

**PoC 通过标准：** 在 Docker 容器内，Claude Code CLI 能完整执行"克隆仓库 → 修改文件 → 运行测试 → 调用终止工具"的完整流程。

### 里程碑 1.1 — 基础设施

- [ ] Docker Compose 环境（postgres + redis + api + worker + frontend）
- [ ] PostgreSQL 数据库初始化（alembic migrations）
- [ ] FastAPI 骨架 + 健康检查接口
- [ ] Celery 任务队列接通 Redis
- [ ] Next.js 前端骨架 + API 代理配置

### 里程碑 1.2 — Crawler + Agent A

- [ ] GitHub API 客户端（Issue 抓取、仓库信息）
- [ ] Crawler Service（手动触发 + 去重逻辑）
- [ ] LLMClient 抽象层（Anthropic 实现）
- [ ] Agent A Harness（SingleShotLoop + Tool Use 结构化输出）
- [ ] Agent A Prompt v1.0
- [ ] analyze_worker（Celery）
- [ ] `/api/v1/issues` GET 接口
- [ ] 看板 Issue 列表页（基础版，无筛选）

### 里程碑 1.3 — 用户决策流

- [ ] `/api/v1/issues/{id}/decide` 接口（忽略/开发）
- [ ] Issue 状态流转（PENDING_DECISION → QUEUED_DEV / IGNORED）
- [ ] 看板操作按钮（忽略 / 加入开发）

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

### 里程碑 1.5 — Agent C + PR 提交

- [ ] Agent C Harness（SingleShotLoop）
- [ ] Agent C Prompt v1.0
- [ ] review_worker（Celery）
- [ ] PR Service（GitHub API 创建 PR）
- [ ] GitHub Webhook 接收（PR merge / close）
- [ ] Issue 状态流转（PR_SUBMITTED → PR_MERGED / PR_CLOSED）
- [ ] 看板 PR 状态显示

**Phase 1 完成标志：** 选择一个真实 Python 仓库 Issue，系统能自动完成从评估到提交 PR 的全流程。

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

### 里程碑 2.3 — 边界处理

- [ ] Agent B 重试机制（携带失败原因重入，最多 2 次）
- [ ] Agent C → Agent B 退回循环（最多 3 次，超限 ARCHIVED）
- [ ] PR 关闭后人工审核流程（重新开发 / 归档）
- [ ] 卡死检测完整实现（repeated_read / no_write 等模式）
- [ ] 上下文压缩（Agent B 长 loop 时触发）
- [ ] Schema 校验失败重试（Agent A / C）

### 里程碑 2.4 — 看板完善

- [ ] Issue 列表筛选 / 排序 / 搜索
- [ ] Issue 详情页（评估报告全视图）
- [ ] 开发进度步骤可视化（ANALYZE → PLAN → IMPLEMENT → TEST → COMMIT）
- [ ] 历次开发 / 评审历史记录
- [ ] PR 列表页
- [ ] 统计概览（各状态数量、今日新增）

### 里程碑 2.5 — 多厂商模型支持

- [ ] OpenAI Client 实现
- [ ] DeepSeek Client 实现（OpenAI 兼容）
- [ ] 模型配置文件热加载
- [ ] 成本统计（token 用量 + 费用估算）

---

## Phase 3 — 优化（质量 + 扩展）

### 里程碑 3.1 — Prompt 质量提升

- [ ] Agent A 离线评估框架（ground truth 对比）
- [ ] Agent B 成功率分析（按语言、按错误类型）
- [ ] Agent C 误拒率分析（人工抽样复查）
- [ ] Prompt A/B 测试流程
- [ ] Few-shot 示例注入（成功案例复用）

### 里程碑 3.2 — Agent B 能力增强

- [ ] devcontainer.json 支持（使用仓库自定义开发环境）
- [ ] 多步测试策略（unit → integration → e2e 按需运行）
- [ ] 修改影响范围分析（避免过宽修改）
- [ ] Extended Thinking 支持（复杂问题启用）

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
