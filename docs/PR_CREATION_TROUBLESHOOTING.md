# PR 真实创建 + Webhook 本地排障

> 写给：跑 Phase 1 dry run 想真的把 PR 走通的人。
> 关联：`docs/DRY_RUN_PHASE1_REPORT.md` §6 未验证项。
> 涉及代码：`backend/app/services/pr_service.py` / `backend/app/workers/review_worker.py` / `scripts/replay_github_webhook.py`。

## TL;DR

Phase 1 dry run 留了两段没真跑通：

1. **PR 真实创建**：`GITHUB_DEV_TOKEN` 是 fine-grained PAT，缺 Administration 权限 → fork 接口 403 → `forked_repo` 兜底为 `base_repo` → review_worker APPROVED 路径走到 `pr_skip_no_fork` 出口，未触发 `PRService.create_pr`。
2. **Webhook 真实接收**：没有真 PR 可点 close/merge，GitHub 不会推。

补救：换 token + 用 `scripts/replay_github_webhook.py` 在本地回放。

---

## 1. PR 真实创建：unblock 路径

### 1.1 推荐：换 classic PAT with `repo` scope

GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token：

- 勾 `repo`（全选；含 fork + push + PR create）
- 过期时间按需

替换 `.env`：

```env
GITHUB_DEV_TOKEN=ghp_<新 token>
```

重启 worker（让 Settings 缓存刷新）：

```bash
docker compose restart worker
```

然后重跑：crawler manual → start_dev → 等 review_worker。如果 token 没问题，`pull_requests` 表会出现新行，前端看板的 PR 卡片会亮。

### 1.2 fine-grained PAT 替代路径

如果坚持用 fine-grained：

- repository access：勾上你的 dev 账号下的所有相关仓库（或 All repositories）
- permissions：
  - **Administration → Read and write**（关键，fork 接口要它）
  - Contents → Read and write
  - Pull requests → Read and write
  - Metadata → Read

### 1.3 不换 token 的兜底：手动 fork

GitHub Web UI 把 `base_repo` fork 到你的 dev 账号 → 之后 `GitHubService._upsert_fork` 调 fork API 时 GitHub 直接返回 200（fork 接口幂等，已有 fork 不会重复创建）。

适用：临时跑一次端到端验证；不适合每个新仓库都手动操作。

### 1.4 兜底链路（设计）

`review_worker._create_pr_if_eligible` 在以下情况会跳过 PR 创建（不退回 Issue 状态、不抛错）：

| 条件 | 日志 key | 出口 |
|------|---------|------|
| `head_repo == base_repo`（没 fork） | `review_worker.pr_skip_no_fork` | `{"pr_skipped": "no_fork"}` |
| `dev_task.branch_pushed == False`（push 没成功） | `review_worker.pr_skip_not_pushed` | `{"pr_skipped": "not_pushed"}` |
| 无 `GITHUB_DEV_TOKEN` | `NoDevTokenError` → catch | `{"pr_skipped": "no_token"}` |

被跳过时 Issue 留在 `IN_REVIEW`，不破坏 Agent C 退回链路，也不会 false-positive 转 `PR_SUBMITTED`。

---

## 2. Webhook 本地回放：`scripts/replay_github_webhook.py`

无需 ngrok / smee。脚本手工构造 GitHub 真实字段结构的 payload，HMAC-SHA256 签好后 POST 到 `http://localhost:8000/api/v1/webhooks/github`。

### 2.1 前置

- `.env` 里 `GITHUB_WEBHOOK_SECRET` 已配置（任意字符串，与脚本对齐即可）
- 后端跑着：`docker compose up api worker postgres redis`
- 表里有至少一行 `pull_requests`（要么真跑通了 §1，要么用 §2.3 手工塞）

### 2.2 用法

列出本地有 PR 的 issue：

```bash
python scripts/replay_github_webhook.py list
```

回放 PR merged：

```bash
python scripts/replay_github_webhook.py merged \
    --pr-url https://github.com/owner/repo/pull/1 \
    --actor maintainer-alice
```

预期：HTTP 200 + `{"handled": true, "merged": true}`；`issues.status` → `PR_MERGED`；`pull_requests.final_outcome` → `MERGED_CLEAN`；`pr_outcomes` 新增 `event_type=merged` 行。

其他事件：

```bash
# closed（未 merge）
python scripts/replay_github_webhook.py closed --pr-url ... --actor bob

# review submitted（state: approved / changes_requested / commented）
python scripts/replay_github_webhook.py review --pr-url ... \
    --reviewer carol --state changes_requested --body "Please add tests"

# PR comment（issue_comment 事件，但仅 PR 上的）
python scripts/replay_github_webhook.py comment --pr-url ... \
    --commenter dave --body "LGTM"

# ping（GitHub admin "Test connectivity" 等价）
python scripts/replay_github_webhook.py ping
```

加 `--from-github` 会从 GitHub API 拉真实 PR 数据当 payload 主体（保真度最高，需要 `GITHUB_TOKEN`）：

```bash
python scripts/replay_github_webhook.py merged \
    --pr-url https://github.com/owner/repo/pull/1 --from-github
```

### 2.3 手工塞一行 pull_requests 用于回放

如果暂时没真 PR，可以用任意 `IN_REVIEW` issue 模拟（先强转到 `PR_SUBMITTED`）：

```bash
docker exec issuepilot-postgres-1 psql -U issuepilot -d issuepilot -c "
  UPDATE issues SET status='PR_SUBMITTED' WHERE id='<issue_uuid>';
  INSERT INTO pull_requests
    (id, issue_id, github_pr_number, github_pr_url, title, body,
     head_repo, head_branch, base_repo, base_branch, status,
     submitted_at, created_at, updated_at)
  VALUES
    (gen_random_uuid(), '<issue_uuid>', 1,
     'https://github.com/owner/repo/pull/1', 't', 'b',
     'dev/repo', 'feat', 'owner/repo', 'main', 'OPEN',
     now(), now(), now());
"
```

### 2.4 已知不覆盖

- `RejectionClassifier`（2.3 后续子项）：review/comment 事件目前只写 `pr_outcomes`，不分类 category。
- 同 webhook 重复投递的幂等性：当前实现按 PR-by-URL 单向更新，重复回放会重写 `pull_requests` 字段但不重复转 Issue 状态（`mark_pr_merged` 在已是 `PR_MERGED` 时抛 `InvalidTransitionError`，但被 catch + log）。
- Webhook 真实 GitHub 推送的边界（IP 白名单、超时重试）：production 部署时再处理。

---

## 3. 验证清单

跑完上面任意一条路径，检查：

| 项 | 命令 |
|---|---|
| Issue 状态对 | `SELECT status FROM issues WHERE id=...;` |
| PR 字段对 | `SELECT status, final_outcome, merger_login, merge_commit_sha FROM pull_requests WHERE github_pr_url=...;` |
| pr_outcomes 有事件 | `SELECT event_type, actor, occurred_at FROM pr_outcomes WHERE pr_id=...;` |
| 前端看板 PR 卡片着色 | 打开 `http://localhost:3000`，找到对应 issue，看 PR 卡片 |
| 接口返回新状态 | `curl http://localhost:8000/api/v1/issues` |

---

## 4. 进一步阅读

- `backend/app/api/webhooks.py` — HMAC 验签 + 事件路由
- `backend/app/services/pr_tracker.py` — 事件 → 持久化
- `backend/app/services/pr_service.py` — PR 创建主流程 + 异常分类
- `backend/tests/integration/test_webhook_api.py` — 7 项集成测试（同一套 fixture 模式）
