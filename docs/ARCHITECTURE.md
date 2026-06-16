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
APScheduler → CrawlerTask
  → GitHub API 获取 Issues → bulk_upsert
  → 推入 analyze_queue
  → analyze_worker → Agent A → 写入 evaluations
  → Issue.status = PENDING_DECISION
```

### 开发

```
用户 POST /decide (start_dev)
  → Issue.status = QUEUED_DEV → 推入 dev_queue
  → dev_worker → 启动 Docker 容器
  → Agent B（Claude Code CLI）执行
    → 实时日志 → Redis PubSub → WebSocket → 前端
  → 完成 → Issue.status = QUEUED_REVIEW
```

### 评审 → PR

```
review_worker → Agent C
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

## 7. Prompt Injection 防御

**风险场景：** Agent A/B/C 的输入中包含来自 GitHub 的 Issue 内容（外部用户控制），存在 Prompt Injection 风险。例如 Issue body 中写 "忽略以上指令..."。

**防御策略：**

| 层面 | 措施 |
|------|------|
| 输入边界 | Issue body 明确标记为用户数据，在 Prompt 中用 XML 标签包裹：`<issue_content>...</issue_content>` |
| 结构化输出 | 所有 Agent 输出通过 Tool Use 强制结构化，注入的文字无法影响输出结构 |
| Agent B 约束 | Docker 网络隔离，不能访问内部服务；`allowedTools` 限制工具列表 |
| 日志审计 | 记录所有 Agent 的完整输入输出，异常时可回溯 |
| Agent B PoC 验证 | **在 ROADMAP Phase 1 开始前**，先验证 Claude Code CLI headless 模式在 Issue 含注入内容时的行为 |
