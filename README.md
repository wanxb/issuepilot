# IssuePilot

多智能体 GitHub Issue 自动化开发流水线。

每天自动抓取 GitHub Issue → AI 评估价值 → 你选择开发 → AI 完成代码开发、自测、评审 → 自动提交 PR。人工只做两件事：**决定开发哪个 Issue**，**处理 PR 被关闭的情况**。

---

## 工作流程

```
Crawler（每日定时）→ Agent A 价值评估 → 看板展示
  → [你] 选择 Issue 开发
  → Agent B 在 Docker 沙箱中开发 + 自测
  → Agent C 代码评审
  → 通过：自动提交 PR
  → 未通过：退回 Agent B（最多 3 次循环）
  → PR 被关闭：[你] 决定是否重新开发
```

## 三个 Agent

| Agent | 职责 | 运行方式 |
|-------|------|----------|
| **Agent A** | Issue 价值评估（5 维度评分） | Celery Worker + LLM |
| **Agent B** | 代码开发 + 自测（Clone→实现→测试→Commit） | Docker 沙箱 + Claude Code CLI |
| **Agent C** | 代码评审 + PR 生成 | Celery Worker + LLM |

---

## 快速开始

```bash
# 配置
cp .env.example .env                    # 填写 GitHub Token 和 LLM API Key
cp backend/config/models.example.yaml backend/config/models.yaml

# 启动基础服务
docker compose up postgres redis -d

# 初始化数据库
cd backend && uv sync && uv run alembic upgrade head

# 构建 Agent B 沙箱镜像
./scripts/build_sandboxes.sh

# 启动（三个终端）
uv run uvicorn app.main:app --reload
uv run celery -A app.workers.celery_app worker --loglevel=info
cd frontend && npm install && npm run dev
```

访问 `http://localhost:3000`。

---

## 技术栈

- **后端：** Python 3.11 / FastAPI / Celery / PostgreSQL / Redis
- **前端：** Next.js 14 / TypeScript / shadcn/ui / Tailwind
- **AI：** 多厂商 LLM（统一接口层，支持 Anthropic/OpenAI/DeepSeek 等）
- **沙箱：** Docker（python / node / go / rust / java 镜像）

---

## 文档

| 文档 | 说明 |
|------|------|
| [PRD](docs/PRD.md) | 产品需求与功能定义 |
| [架构设计](docs/ARCHITECTURE.md) | 系统架构、技术选型、Observability |
| [Agent 设计](docs/AGENT_DESIGN.md) | 接口契约、Tool Definition、评分维度 |
| [Agent 运行时](docs/AGENT_RUNTIME.md) | Loop 设计、卡死检测、重试策略 |
| [Evals](docs/EVALS.md) | 评估指标、Golden Dataset、CI 集成 |
| [数据模型](docs/DATA_MODEL.md) | 状态机、实体关系 |
| [API 规范](docs/API_SPEC.md) | REST 端点、WebSocket 事件 |
| [开发指南](docs/DEVELOPMENT.md) | 本地环境、编码规范 |
| [Roadmap](ROADMAP.md) | 迭代计划（含 Agent B PoC 前置验证） |

---

当前阶段：**文档完成，待开发 — 从 ROADMAP 里程碑 1.0（Agent B PoC）开始**
