# 开发指南

## 1. 项目结构

```
issuepilot/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── api/                # API 路由层
│   │   │   ├── issues.py
│   │   │   ├── crawl_jobs.py
│   │   │   ├── pull_requests.py
│   │   │   ├── stats.py
│   │   │   └── webhooks.py
│   │   ├── services/           # 业务逻辑层
│   │   │   ├── issue_service.py
│   │   │   ├── pr_service.py
│   │   │   └── crawler_service.py
│   │   ├── agents/             # Agent 实现
│   │   │   ├── base.py         # 基础 Agent 类
│   │   │   ├── agent_a.py      # 价值评估 Agent
│   │   │   ├── agent_b.py      # 开发 Agent
│   │   │   └── agent_c.py      # 评审 Agent
│   │   ├── llm/                # LLM 抽象层
│   │   │   ├── base.py
│   │   │   ├── anthropic_client.py
│   │   │   ├── openai_client.py
│   │   │   └── factory.py      # 根据配置创建 client
│   │   ├── workers/            # Celery Workers
│   │   │   ├── celery_app.py
│   │   │   ├── analyze_worker.py
│   │   │   ├── dev_worker.py
│   │   │   └── review_worker.py
│   │   ├── sandbox/            # Docker 沙箱管理
│   │   │   ├── docker_manager.py
│   │   │   └── language_detector.py
│   │   ├── github/             # GitHub 集成
│   │   │   ├── client.py
│   │   │   ├── crawler.py
│   │   │   └── pr_builder.py
│   │   ├── models/             # SQLAlchemy 模型
│   │   │   ├── issue.py
│   │   │   ├── repository.py
│   │   │   ├── evaluation.py
│   │   │   ├── dev_task.py
│   │   │   ├── review_task.py
│   │   │   ├── pull_request.py
│   │   │   └── crawl_job.py
│   │   ├── schemas/            # Pydantic schemas
│   │   │   └── ...
│   │   ├── db/
│   │   │   ├── database.py     # 数据库连接
│   │   │   └── repository.py   # 数据访问层
│   │   └── core/
│   │       ├── config.py       # 配置加载
│   │       ├── scheduler.py    # APScheduler
│   │       └── websocket.py    # WebSocket 管理
│   ├── alembic/                # 数据库迁移
│   ├── tests/
│   ├── config/
│   │   ├── models.yaml         # Agent 模型配置（gitignored）
│   │   └── models.example.yaml
│   ├── pyproject.toml
│   └── Dockerfile
│
├── frontend/                   # Next.js 前端
│   ├── src/
│   │   ├── app/                # Next.js App Router
│   │   │   ├── page.tsx        # 看板主页（Issue 列表）
│   │   │   ├── issues/
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx
│   │   │   ├── pull-requests/
│   │   │   │   └── page.tsx
│   │   │   └── crawl-logs/
│   │   │       └── page.tsx
│   │   ├── components/
│   │   │   ├── dashboard/      # 看板组件
│   │   │   ├── issue/          # Issue 相关组件
│   │   │   └── ui/             # shadcn/ui 组件
│   │   ├── hooks/              # React hooks
│   │   │   ├── useIssues.ts
│   │   │   └── useWebSocket.ts
│   │   ├── lib/
│   │   │   ├── api.ts          # API 客户端
│   │   │   └── types.ts        # TypeScript 类型
│   │   └── store/              # Zustand store
│   ├── package.json
│   └── Dockerfile
│
├── sandbox/                    # Agent B Docker 镜像
│   ├── base/Dockerfile
│   ├── node/Dockerfile
│   ├── python/Dockerfile
│   ├── go/Dockerfile
│   └── rust/Dockerfile
│
├── docs/                       # 项目文档
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── AGENT_SPECS.md
│   ├── DATA_MODEL.md
│   ├── API_SPEC.md
│   └── DEVELOPMENT.md
│
├── docker-compose.yml          # 本地开发环境
├── docker-compose.prod.yml     # 生产部署
├── .env.example                # 环境变量模板
├── .gitignore
└── CLAUDE.md                   # Claude Code 项目上下文
```

---

## 2. 环境要求

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 后端 |
| Node.js | 20+ | 前端 |
| Docker | 24+ | 沙箱 + 服务编排 |
| Docker Compose | 2.x | 本地服务启动 |
| PostgreSQL | 16 | 通过 Docker 启动 |
| Redis | 7 | 通过 Docker 启动 |

---

## 3. 本地启动

### 3.1 首次初始化

```bash
# 克隆后，复制配置模板
cp .env.example .env
cp backend/config/models.example.yaml backend/config/models.yaml

# 编辑 .env，填写以下必填项：
# - GITHUB_TOKEN（需要 repo, fork 权限）
# - GITHUB_USERNAME（提交 PR 的账号）

# 编辑 backend/config/models.yaml，填写 API Key 对应的环境变量
```

### 3.2 启动服务

```bash
# 启动基础设施（postgres + redis）
docker compose up postgres redis -d

# 启动后端开发服务器
cd backend
uv sync
uv run alembic upgrade head          # 初始化数据库
uv run uvicorn app.main:app --reload  # 开发服务器

# 启动 Workers（新终端）
cd backend
uv run celery -A app.workers.celery_app worker --loglevel=info \
  --queues=analyze_queue,dev_queue,review_queue

# 启动前端开发服务器（新终端）
cd frontend
npm install
npm run dev
```

### 3.3 构建 Agent B 沙箱镜像

```bash
# 构建所有语言镜像（首次或更新 Dockerfile 后执行）
./scripts/build_sandboxes.sh

# 或单独构建某个语言
docker build -t agent-sandbox-python sandbox/python/
```

---

## 4. 环境变量说明

```bash
# .env

# 数据库
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/issuepilot
REDIS_URL=redis://localhost:6379/0

# GitHub
GITHUB_TOKEN=ghp_xxxx              # 用于抓取 Issue（只需 public_repo 权限）
GITHUB_DEV_TOKEN=ghp_xxxx          # 用于 Fork 和提交 PR（需要 repo 权限）
GITHUB_USERNAME=your_username       # 提交 PR 的账号
GITHUB_WEBHOOK_SECRET=xxxx          # Webhook 签名验证

# LLM API Keys（根据 models.yaml 中使用的厂商配置）
ANTHROPIC_API_KEY=sk-ant-xxxx
OPENAI_API_KEY=sk-xxxx
DEEPSEEK_API_KEY=sk-xxxx

# 应用配置
CRAWL_SCHEDULE=0 8 * * *           # Cron 表达式，默认每天 8:00
MAX_ANALYZE_WORKERS=3               # Agent A 并发数
MAX_DEV_WORKERS=2                   # Agent B 并发数（每个需要一个 Docker 容器）
MAX_REVIEW_WORKERS=3                # Agent C 并发数
DEV_TASK_TIMEOUT_MINUTES=60         # Agent B 超时时间
MAX_DEV_RETRY=2                     # Agent B 最大重试次数
MAX_REVIEW_RETRY=3                  # Agent C→B 最大退回次数
```

---

## 5. 编码规范

### 5.1 后端（Python）

- 格式化：`ruff format`
- Lint：`ruff check`
- 类型检查：`pyright`
- 测试：`pytest`
- 异步：所有 IO 操作使用 `async/await`
- 错误处理：使用自定义异常类，不吞异常
- 日志：使用 `structlog`，结构化输出

### 5.2 前端（TypeScript）

- 格式化：`prettier`
- Lint：`eslint`
- 类型：严格模式，禁用 `any`
- 组件：函数组件 + hooks，不使用 class components
- CSS：Tailwind utility-first，不写内联 style

### 5.3 Git 规范

```
feat: 新功能
fix: Bug 修复
refactor: 重构
test: 测试
docs: 文档
chore: 构建/依赖
```

---

## 6. 测试策略

| 层级 | 工具 | 覆盖目标 |
|------|------|----------|
| 单元测试 | pytest | Service / Repository 层 |
| 集成测试 | pytest + testcontainers | Agent 流程 / API 端点 |
| E2E 测试 | Playwright | 看板关键操作路径 |

### 运行测试

```bash
# 后端单元测试
cd backend && uv run pytest tests/unit

# 后端集成测试（需要 Docker）
cd backend && uv run pytest tests/integration

# 前端测试
cd frontend && npm test
```

---

## 7. 数据库迁移

```bash
# 生成新迁移
cd backend
uv run alembic revision --autogenerate -m "add_xxx_column"

# 执行迁移
uv run alembic upgrade head

# 回滚一步
uv run alembic downgrade -1
```

---

## 8. 抓取目标配置

抓取目标在数据库 `crawl_targets` 表中管理，通过脚本初始化：

```bash
# 添加目标仓库
uv run python scripts/manage_targets.py add repo owner/repo-name \
  --min-stars 500 \
  --labels "bug,good first issue"

# 添加话题
uv run python scripts/manage_targets.py add topic machine-learning \
  --languages python \
  --min-stars 100

# 启用 GitHub Trending
uv run python scripts/manage_targets.py add trending \
  --languages python,typescript,go

# 列出所有目标
uv run python scripts/manage_targets.py list

# 禁用某个目标
uv run python scripts/manage_targets.py disable {id}
```

---

## 9. 生产部署

```bash
# 使用生产 compose 文件
docker compose -f docker-compose.prod.yml up -d

# 查看服务状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker-dev
```

**注意：** Agent B Worker 容器需要挂载 Docker socket 以启动子容器：

```yaml
# docker-compose.prod.yml 片段
worker-dev:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - /cache/npm:/cache/npm
    - /cache/pip:/cache/pip
    - /cache/go:/cache/go
```
