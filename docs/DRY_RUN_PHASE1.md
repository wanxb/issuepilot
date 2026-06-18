# Phase 1 端到端 dry run 操作手册

> 一次性运行手册，跑完即归档。承担两个任务：(1) 验收 1.5a-e 改动；(2) 给后续 Phase 2 的边界处理工作摸到真实失败模式。

---

## 0. 前置

- [x] sandbox 镜像 `agent-sandbox-python:latest` 已构建（含 1.5b BASE_SHA + diff capture + 1.5d push 逻辑）
- [x] `.env`：ANTHROPIC、GITHUB_TOKEN、GITHUB_DEV_TOKEN、GITHUB_WEBHOOK_SECRET 已设
- [ ] **公网穿透**：webhook 部分需 GitHub 能 POST 到本机 8000 端口
  ```bash
  ngrok http 8000
  # 记下 https://<random>.ngrok.io
  ```
- [ ] **GitHub Webhook 配置**（测试仓库 Settings → Webhooks → Add webhook）
  - Payload URL: `https://<ngrok>/api/v1/webhooks/github`
  - Content type: `application/json`
  - Secret: 与 `.env` 中 `GITHUB_WEBHOOK_SECRET` 一致
  - Events: pull requests, pull request reviews, issue comments
- [ ] 准备 1 个 sandbox 性质的 Python 仓库 + 至少 1 个 open issue

---

## 1. 触发：manual URL → crawl + Agent A

```bash
# 用整个 repo URL 触发（让 Agent A 从中挑 issue 评估）
curl -s -X POST http://localhost:8000/api/v1/crawl-jobs/manual \
  -H 'content-type: application/json' \
  -d '{"url": "https://github.com/<owner>/<repo>", "max_issues": 5}' | jq
```

**观察**

```bash
# repo / issues / crawl_jobs 入库
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT id, full_name FROM repositories ORDER BY created_at DESC LIMIT 3;"

docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT github_number, title, status FROM issues ORDER BY created_at DESC LIMIT 10;"

# Agent A 评估进度
docker compose logs -f --tail=100 worker | grep -E '(analyze|agent_a)'

# profile_queue 进度（1.5c 异步生成 repo_profile）
docker compose logs -f --tail=100 worker | grep -E '(profile|agent_d)'

# 评估结果 + LLM 调用日志
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT i.github_number, i.title, e.total_score, e.difficulty, e.summary, e.is_fallback
   FROM issues i JOIN evaluations e ON e.issue_id = i.id
   ORDER BY i.created_at DESC LIMIT 5;"

# repo_profile（应在几十秒内入库）
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT profile_quality, quality_reason, test_command, install_command, expires_at::date
   FROM repo_profiles ORDER BY generated_at DESC LIMIT 1;"
```

**期望**：所有 issue 进 `PENDING_DECISION`，至少有一个 evaluation 行；repo_profile 行入库（即使 quality=low）。

---

## 2. 用户决策：start_dev

选一个 `total_score >= 6.5` 的 issue。

```bash
ISSUE_ID="<从上一步查到的 id>"
curl -s -X POST "http://localhost:8000/api/v1/issues/$ISSUE_ID/decide" \
  -H 'content-type: application/json' \
  -d '{"action":"start_dev"}' | jq
```

**观察**

```bash
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT status, attempt_number, sandbox_image, forked_repo, branch_name
   FROM dev_tasks ORDER BY created_at DESC LIMIT 1;"
```

期望：`status=running` / `forked_repo` 已是 `<dev_username>/<repo>` / `sandbox_image=agent-sandbox-python:latest`。

---

## 3. Agent B 沙箱执行

```bash
# worker 实时日志（关注 stream-json 解析事件 + container 启动/退出）
docker compose logs -f --tail=200 worker | grep -E '(dev_worker|agent_b|sandbox)'

# 实时进度（看板已有 WebSocket，但 CLI 也可直接 polling）
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT step, level, substring(message, 1, 100) AS msg, created_at
   FROM dev_logs WHERE dev_task_id = (SELECT id FROM dev_tasks ORDER BY created_at DESC LIMIT 1)
   ORDER BY created_at DESC LIMIT 30;"

# 沙箱内 Docker 容器（应能看到 issuepilot.dev_task_id label）
docker ps --filter label=issuepilot.dev_task_id --format '{{.ID}} {{.Status}} {{.Image}}'
```

**期望**：5 个 ParsedLine 阶段（SETUP/ANALYZE/PLAN/IMPLEMENT/TEST/COMMIT），最后 SYSTEM 输出 `=== SYSTEM: push ok ===`。

完成后查 dev_task 收尾字段：

```bash
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT status, files_changed, diff_summary, test_result,
          length(git_diff) AS diff_bytes, base_sha, branch_pushed,
          total_cost_usd, loop_iterations, failure_reason
   FROM dev_tasks ORDER BY created_at DESC LIMIT 1;"
```

**期望**：`status=succeeded`，`branch_pushed=t`，`diff_bytes` 非零，`test_result.test_passed=true`。

---

## 4. Agent C 评审

dev_worker 成功后会自动 chain `review_worker.review_dev_task`。

```bash
# review_worker 日志
docker compose logs -f --tail=100 worker | grep -E '(review_worker|agent_c)'

# review_task 行
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT verdict, overall_score, dimensions->'correctness' AS correctness,
          dimensions->'code_style' AS code_style, pr_title,
          provider, is_fallback, cost_usd
   FROM review_tasks ORDER BY created_at DESC LIMIT 1;"
```

**期望**（APPROVED 路径）：`verdict=APPROVED`，`pr_title` 非空，5 维全部 passed=true。

REJECTED 路径若发生：

```bash
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT source, category, severity, dimension, agent_b_attribution,
          substring(detail, 1, 200) AS detail
   FROM rejection_reasons ORDER BY created_at DESC LIMIT 1;"
```

`source=agent_c`，category 根据最低未通过维度填充。Issue 转 `REVIEW_REJECTED`。dry run 可手动重 decide_start_dev 或暂停 — 主线测 APPROVED 即可。

---

## 5. PR 创建

APPROVED 时 review_worker 自动调 PRService。

```bash
# review_worker 日志里关注 "review_worker.pr_submitted"
docker compose logs --tail=200 worker | grep -E '(pr_service|pr_submitted|pr_skipped)'

# pull_requests + pr_outcomes
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT github_pr_number, github_pr_url, status, final_outcome,
          head_repo, base_branch, submitted_at
   FROM pull_requests ORDER BY created_at DESC LIMIT 1;"

docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT event_type, actor, occurred_at FROM pr_outcomes
   ORDER BY occurred_at DESC LIMIT 5;"

# Issue 终态
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT github_number, title, status FROM issues
   ORDER BY created_at DESC LIMIT 1;"
```

**期望**：`pull_requests.status=OPEN`，`pr_outcomes` 有一行 `submitted`，Issue → `PR_SUBMITTED`。

去 GitHub 上确认 PR 真的创建了（fork → 原仓库），title/body 是 Agent C 输出。

---

## 6. Webhook：人为 close（merged 或 closed）

**前置**：ngrok 已挂起，GitHub Webhook 已配。

### 6a. merged 路径

去 GitHub PR 页面点 **Merge pull request**。

```bash
# api 日志
docker compose logs -f --tail=100 api | grep -E '(webhook|pr_tracker)'

# pr_outcomes 应多一行 "merged"
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT event_type, actor, occurred_at FROM pr_outcomes ORDER BY occurred_at DESC LIMIT 5;"

# pull_requests
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT status, final_outcome, merged_at, merger_login, merge_commit_sha
   FROM pull_requests ORDER BY created_at DESC LIMIT 1;"

# Issue
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT status FROM issues ORDER BY created_at DESC LIMIT 1;"
```

**期望**：PR `status=MERGED, final_outcome=MERGED_CLEAN`；Issue `status=PR_MERGED`。

### 6b. 备选：closed without merge

去 GitHub PR 页面点 **Close pull request**（不合并）。

```bash
# pull_requests final_outcome 应为 CLOSED_BY_MAINTAINER
# Issue 应为 PR_CLOSED
```

---

## 7. 看板 PR 卡片

打开 http://localhost:3000，找到该 issue 的卡片。

**期望**：底部 `PRPanel` 出现，badge 颜色按 `final_outcome` 着色（MERGED_CLEAN=绿；CLOSED_BY_MAINTAINER=红）；PR 链接可点。

---

## 8. 成本与时长汇总

```bash
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "SELECT agent_kind, count(*), sum(cost_usd) AS cost, sum(latency_ms)/1000.0 AS sec
   FROM llm_call_logs GROUP BY agent_kind ORDER BY agent_kind;"
```

期望 PoC 数字（参考 1.0 报告）：Agent B 一次成功 ~$0.22 / ~50s。Agent A/C/D 各 ~$0.02-0.05。

---

## 9. dry run 退场

如果只是验收，留下数据无大碍。要彻底清理：

```bash
docker compose exec -T postgres psql -U issuepilot -d issuepilot -c \
  "TRUNCATE TABLE llm_call_logs, pr_outcomes, rejection_reasons,
   pull_requests, review_tasks, dev_logs, dev_tasks,
   evaluations, issues, crawl_jobs, repo_profiles, repositories
   RESTART IDENTITY CASCADE;"
```

> GitHub 端的 fork / PR 需要人工去 GitHub 上手动清理。
