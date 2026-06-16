# 数据模型设计

> 数据库表的 DDL 在 `backend/alembic/versions/` 中维护，本文档只记录概念模型和状态机。

---

## 1. 实体关系图

```
repositories ──< issues ──< evaluations     (1:1，每 Issue 一次 Agent A 评估)
                    │
                    ├──< dev_tasks ──< dev_logs   (1:N，每次开发尝试一条记录)
                    │
                    ├──< review_tasks             (1:N，每次评审一条记录)
                    │
                    └──< pull_requests            (1:1，通过评审后创建)

crawl_jobs
crawl_targets                                     (抓取目标配置，独立管理)
```

---

## 2. Issue 状态机

```
                    ┌─────────────┐
                    │  DISCOVERED  │ ← Crawler 写入
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  ANALYZING  │ ← Agent A 消费
                    └──────┬──────┘
                           │
                    ┌──────▼──────────┐
                    │ PENDING_DECISION │ ← 等待用户操作
                    └──────┬──────────┘
              ┌────────────┤
              │            │
       ┌──────▼─────┐  ┌───▼────────┐
       │   IGNORED  │  │ QUEUED_DEV │ ← 用户选择开发
       └────────────┘  └─────┬──────┘
                             │
                       ┌─────▼──────┐
                       │   IN_DEV   │ ← Agent B 领取
                       └─────┬──────┘
                             │
                       ┌─────▼──────────┐
                       │  DEV_TESTING   │
                       └─────┬──────────┘
                      ┌──────┤
              失败     │      │ 通过
        ┌─────────────▼─┐  ┌─▼────────────┐
        │   DEV_FAILED  │  │ QUEUED_REVIEW │
        │ (重入 QUEUED) │  └──────┬────────┘
        └───────────────┘        │
                             ┌───▼────────┐
                             │ IN_REVIEW  │ ← Agent C 领取
                             └───┬────────┘
                        ┌────────┤
                        │        │ 通过
              ┌─────────▼────┐  ┌▼─────────────┐
              │REVIEW_REJECTED│  │ PR_SUBMITTED  │
              │→ QUEUED_DEV  │  └──────┬────────┘
              └──────────────┘  ┌──────┤
                                │      │
                         ┌──────▼─┐  ┌─▼──────────┐
                         │MERGED  │  │  PR_CLOSED  │
                         └────────┘  └──────┬──────┘
                                            │ 人工决策
                                    ┌───────┤
                                    │       │
                             ┌──────▼─┐  ┌──▼────────┐
                             │ARCHIVED│  │ QUEUED_DEV│
                             └────────┘  └───────────┘
```

---

## 3. 数据表说明

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `repositories` | 仓库元数据缓存 | `full_name`, `language`, `stars`, `last_crawled_at` |
| `issues` | Issue 主表，持有状态机 | `status`, `retry_count`, `source`, `crawl_job_id` |
| `evaluations` | Agent A 评估结果，1:1 关联 Issue | `score`, `difficulty`, `estimated_hours`, `dimensions` (JSONB), `prompt_tokens` |
| `dev_tasks` | 每次 Agent B 开发尝试 | `attempt_number`, `status`, `container_id`, `forked_repo`, `branch_name`, `review_context`, `test_result` (JSONB) |
| `dev_logs` | Agent B 实时执行日志 | `level`, `step`, `message`, `created_at` |
| `review_tasks` | 每次 Agent C 评审尝试 | `attempt_number`, `verdict`, `dimensions` (JSONB), `rejection_reason` |
| `pull_requests` | 已提交的 PR 记录 | `github_pr_url`, `status`, `close_reason` |
| `crawl_jobs` | 每次抓取任务的执行记录 | `trigger`, `status`, `stats` (JSONB) |
| `crawl_targets` | 抓取目标配置 | `type` (repo/topic/trending), `value`, `filters` (JSONB), `is_active` |

---

## 4. 状态流转规则

| 状态 | 允许的前置状态 | 触发方 |
|------|----------------|--------|
| `DISCOVERED` | 初始 | Crawler |
| `ANALYZING` | `DISCOVERED` | analyze_worker |
| `PENDING_DECISION` | `ANALYZING` | analyze_worker |
| `IGNORED` | `PENDING_DECISION` | 用户 |
| `QUEUED_DEV` | `PENDING_DECISION`, `REVIEW_REJECTED`, `PR_CLOSED` | 用户 / review_worker / 用户 |
| `IN_DEV` | `QUEUED_DEV` | dev_worker |
| `DEV_TESTING` | `IN_DEV` | dev_worker |
| `DEV_FAILED` | `DEV_TESTING` | dev_worker（自动重入 `QUEUED_DEV`，retry_count+1） |
| `QUEUED_REVIEW` | `DEV_TESTING` | dev_worker |
| `IN_REVIEW` | `QUEUED_REVIEW` | review_worker |
| `REVIEW_REJECTED` | `IN_REVIEW` | review_worker（携带意见重入 `QUEUED_DEV`） |
| `PR_SUBMITTED` | `IN_REVIEW` | PRService |
| `PR_MERGED` | `PR_SUBMITTED` | GitHub Webhook |
| `PR_CLOSED` | `PR_SUBMITTED` | GitHub Webhook |
| `ARCHIVED` | `IGNORED`, `PR_CLOSED`, `PR_MERGED` | 用户 |

**规则：** 状态只能通过 `IssueService` 变更，不得在其他层直接更新 `status` 字段。
