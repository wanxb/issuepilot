# API 设计规范

> 完整 OpenAPI schema 由 FastAPI 自动生成（`/docs`），本文档只记录端点契约和关键字段。

## 基础约定

- Base URL：`/api/v1`
- 格式：`application/json`
- 认证：无（本地单用户部署）
- 分页：`?page=1&page_size=20`
- 时间：ISO 8601
- 错误：`{"error": "message", "code": "ERROR_CODE"}`

---

## Issues

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/issues` | Issue 列表，看板主数据源 |
| GET | `/issues/{id}` | Issue 详情（含评估报告、当前开发任务、PR） |
| POST | `/issues/{id}/decide` | 用户决策：`{"action": "ignore" \| "start_dev"}` |
| POST | `/issues/{id}/reopen` | PR 关闭后重新加入开发队列，附 note |
| POST | `/issues/{id}/archive` | 归档 |

**GET /issues 筛选参数：** `status[]`, `min_score`, `language`, `repo`, `source`, `sort`（默认 score desc）

**GET /issues 响应关键字段（每条）：**
```
id, status, github_url, title
repo: { full_name, language, stars }
evaluation: { score, difficulty, estimated_hours, summary, is_worth_developing }
```

**GET /issues/{id} 额外字段：**
```
body, labels
evaluation.dimensions: { clarity, feasibility, value, repo_activity, context_sufficiency }
evaluation.{ recommendation, model_used, evaluated_at }
current_dev_task: { id, status, attempt_number, started_at }
pull_request: { github_pr_url, status }
retry_count
```

---

## Dev Tasks

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/issues/{id}/dev-tasks` | Issue 的所有开发尝试历史 |
| GET | `/dev-tasks/{id}/logs` | 开发任务日志（REST 备用，主要用 WebSocket） |

**dev-tasks 响应关键字段：** `attempt_number`, `status`, `branch_name`, `files_changed`, `diff_summary`, `test_result.{ passed, total, failed, new_tests_added }`

**logs 支持参数：** `since_id`（增量拉取），`step`（步骤筛选）

---

## Review Tasks

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/issues/{id}/review-tasks` | Issue 的所有评审历史 |

**响应关键字段：** `attempt_number`, `verdict`, `overall_score`, `dimensions`, `rejection_reason`

---

## Pull Requests

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/pull-requests` | PR 列表，支持 `status` 筛选 |
| GET | `/pull-requests/{id}` | PR 详情 |

**响应关键字段：** `github_pr_url`, `status`, `close_reason`, `issue.{ id, title, github_url }`

---

## Crawl Jobs

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/crawl-jobs` | 抓取任务历史 |
| POST | `/crawl-jobs` | 手动触发定时抓取（按 crawl_targets 配置） |
| POST | `/crawl-jobs/manual` | **手动指定 URL 入口（看板输入框）** |
| GET | `/crawl-jobs/{id}` | 单次任务详情（含错误信息） |

**stats 字段：** `repos_crawled`, `issues_found`, `issues_new`, `issues_skipped`, `duration_seconds`

### POST /crawl-jobs/manual

**请求：**
```json
{
  "url": "https://github.com/owner/repo" | "https://github.com/owner/repo/issues/42",
  "max_issues": 50,           // 仅 repo 模式有效，默认 50
  "force_confirm": false      // repo 模式下 open_count > max_issues 时需用户二次确认
}
```

**响应：**
```json
{
  "job_id": "uuid",
  "mode": "repo" | "issue",
  "repo_full_name": "owner/repo",
  "issues_enqueued": 12,       // 新入队的（不含 dedup 复用的）
  "issues_reused": 3,          // dedup 命中，直接返回已有评估
  "issues_skipped_reason": {}, // {reason: count}，如 closed/PR-only/超出 max_issues
  "needs_confirmation": false, // true 时前端弹确认窗，用户确认后带 force_confirm=true 重发
  "open_count_total": 15       // 仅 repo 模式返回，用于前端展示
}
```

**错误码：**
- `INVALID_URL`：URL 解析失败
- `REPO_NOT_FOUND`：GitHub API 返回 404
- `REPO_PRIVATE`：仓库私有，token 无权限
- `RATE_LIMITED`：GitHub API 限流，返回 `retry_after_seconds`
- `CONFIRMATION_REQUIRED`：repo 模式且超过 max_issues，前端弹窗后重试

**与定时抓取的关系：**
- 复用 CrawlerService 内部抓取逻辑（去重 / 入库 / 入评估队列）
- `crawl_jobs.trigger = "manual_url"`，可在抓取日志页区分
- `issues.source = "manual"`（与 cron 抓取的 `crawl` 区分）

---

## Stats

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/stats/overview` | 看板顶部：各状态数量 + 今日统计 + 上次抓取时间 |
| GET | `/stats/costs` | Token 用量统计，`?period=day\|week\|month` |

---

## WebSocket 事件

**端点：** `ws://localhost:8000/ws`

| 事件类型 | 触发时机 | 关键 data 字段 |
|----------|----------|----------------|
| `issue:status_changed` | Issue 状态变更 | `issue_id`, `old_status`, `new_status` |
| `dev_task:log` | Agent B 实时日志 | `dev_task_id`, `level`, `step`, `message` |
| `crawl_job:update` | 抓取任务进度 | `job_id`, `status`, `progress` |
| `pr:status_changed` | GitHub Webhook 触发 | `pr_id`, `issue_id`, `old_status`, `new_status`, `github_pr_url` |

---

## GitHub Webhook

**端点：** `POST /webhooks/github`

处理事件：`pull_request.closed`，`pull_request.merged`

验证：`X-Hub-Signature-256` HMAC-SHA256，验签失败返回 403，不得跳过。

处理流程：根据 PR URL 查找记录 → 更新 PR 和 Issue 状态 → 推送 WebSocket 事件。
