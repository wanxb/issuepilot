# CLAUDE.md — IssuePilot 项目上下文

## 项目简介

IssuePilot 是多智能体 GitHub Issue 自动化开发流水线。

**自动入口**：每天定时抓取 GitHub Issue。
**手动入口**：用户在看板粘贴 repo URL 或 issue URL 立即评估。

**Agent 流水线**：
- **Agent A** 评估 Issue 价值（per-issue，高频）
- **Agent D** 异步生成 repo profile（per-repo，90 天缓存，供 B/C 注入）
- 用户决策后 **Agent B** 在 Docker 沙箱中开发+自测（注入 profile，无 profile 时降级自学习）
- **Agent C** 评审（对比 profile 评 code_style）通过后自动提交 PR
- 辅助 **RejectionClassifier**（Haiku）把 maintainer 自由文本分类为结构化退回原因

**LLM 兜底**：单次调用级 retry + provider 切换；task 级在 Agent B 整 task 失败后切到 DeepSeek 重跑（DeepSeek-V4-Pro 已 PoC 验证可用）。

**学习闭环**：所有 PR 结果（merged / closed / reverted）和退回原因结构化存储，Phase 3 喂回 Prompt 和 Eval Golden Set。

人工只在两个节点介入：选择开发哪个 Issue、处理 PR 被关闭的情况。

## 文档导航

**产品与架构**
- `docs/PRD.md` — 功能范围、用户故事、Issue 状态定义、成功指标
- `docs/ARCHITECTURE.md` — 系统架构图、技术栈选型、数据流、沙箱设计、Observability、Prompt Injection 防御
- `docs/DATA_MODEL.md` — 实体关系、状态机、表说明（DDL 在 alembic/）
- `docs/API_SPEC.md` — 端点契约、WebSocket 事件、Webhook
- `docs/DEVELOPMENT.md` — 目录结构、本地启动、环境变量、编码规范

**AI 工程（核心）**
- `docs/AGENT_DESIGN.md` — Agent A/B/C 接口契约（输入/输出 Schema、Tool Definition、评分维度）、设计决策
- `docs/AGENT_RUNTIME.md` — 执行引擎、Single-Shot vs ReAct Loop、终止条件、卡死检测、Context 管理、重试策略
- `docs/EVALS.md` — 各 Agent 评估指标、Golden Dataset、Regression Gate、CI 集成

**规划**
- `ROADMAP.md` — 里程碑（含 Agent B PoC 前置验证）、技术债

## 架构关键点

- **后端：** FastAPI + Celery + PostgreSQL + Redis（Python 3.11）
- **前端：** Next.js 14 App Router + shadcn/ui（TypeScript）
- **Agent B 沙箱：** Docker，按语言选镜像，Claude Code CLI headless 执行
- **多模型：** 统一 LLMClient 抽象层，配置在 `backend/config/models.yaml`
- **结构化输出：** 所有 Agent 通过 Tool Use 强制结构化，不解析自由文本

## 关键约束

1. **状态只能通过 `IssueService` 变更**，不得在其他层直接写 `status` 字段
2. **Prompt 是代码**，存在 `backend/app/agents/prompts/`，不在文档中维护全文
3. **Agent B PoC 必须先跑通**（见 ROADMAP 里程碑 1.0），再开始其他开发
4. **GitHub Webhook 必须验证 HMAC-SHA256 签名**，不得跳过
5. **Issue body 必须用 XML 标签包裹**传给 Agent，防止 Prompt Injection

## 快速启动

```bash
cp .env.example .env
cp backend/config/models.example.yaml backend/config/models.yaml
docker compose up postgres redis -d
cd backend && uv run alembic upgrade head
```
