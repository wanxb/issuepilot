# 数据模型设计

> 数据库表的 DDL 在 `backend/alembic/versions/` 中维护，本文档只记录概念模型和状态机。

---

## 1. 实体关系图

```
repositories ──< issues ──< evaluations     (1:1，每 Issue 一次 Agent A 评估)
       │            │
       │            ├──< dev_tasks ──< dev_logs   (1:N，每次开发尝试一条记录)
       │            │
       │            ├──< review_tasks             (1:N，每次评审一条记录)
       │            │
       │            └──< pull_requests            (1:1，通过评审后创建)
       │                       │
       │                       ├──< pr_outcomes        (1:N，PR 生命周期事件流水)
       │                       └──< rejection_reasons  (1:N，退回/关闭的结构化原因)
       │
       └──< repo_profiles                         (1:1，Agent D 生成，90 天 TTL)

crawl_jobs
crawl_targets                                     (抓取目标配置，独立管理)
llm_call_logs                                     (所有 LLM 调用链路，跨 Agent 共享)
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
| `issues` | Issue 主表，持有状态机 | `status`, `retry_count`, `source` (`crawl`/`manual`/`reopen`), `crawl_job_id` |
| `evaluations` | Agent A 评估结果，1:1 关联 Issue | `score`, `difficulty`, `estimated_hours`, `dimensions` (JSONB), `prompt_tokens` |
| `dev_tasks` | 每次 Agent B 开发尝试 | `attempt_number`, `status`, `container_id`, `forked_repo`, `branch_name`, `review_context`, `test_result` (JSONB) |
| `dev_logs` | Agent B 实时执行日志 | `level`, `step`, `message`, `created_at` |
| `review_tasks` | 每次 Agent C 评审尝试 | `attempt_number`, `verdict`, `dimensions` (JSONB), `rejection_reason` |
| `pull_requests` | 已提交的 PR 记录 | `github_pr_url`, `status`, `close_reason`, `final_outcome` |
| `pr_outcomes` | PR 生命周期事件流水（含 review comments / push events / 关闭事件） | `pr_id`, `event_type`, `actor`, `payload` (JSONB), `occurred_at` |
| `rejection_reasons` | PR 被退回 / 关闭 / revert 的结构化原因（核心学习数据） | `pr_id`, `source`, `category`, `severity`, `dimension`, `agent_b_attribution`, `detail`, `raw_text` |
| `crawl_jobs` | 每次抓取任务的执行记录 | `trigger`, `status`, `stats` (JSONB) |
| `crawl_targets` | 抓取目标配置 | `type` (repo/topic/trending), `value`, `filters` (JSONB), `is_active` |
| `llm_call_logs` | 所有 LLM 调用的统一日志（用于成本、兜底切换分析、Eval Golden Set 抽样） | `agent_kind`, `provider`, `model`, `is_fallback`, `success`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `error_code` |
| `repo_profiles` | Agent D 生成的仓库画像，1:1 关联 repository，TTL 90 天 | `repo_id`, `test_command`, `install_command`, `lint_command`, `code_style_notes`, `contributing_summary`, `forbidden_patterns` (jsonb array), `pr_title_convention`, `merged_pr_examples` (jsonb), `profile_quality`, `quality_reason`, `generated_at`, `expires_at`, `forced_refresh_count` |

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

---

## 5. PR 结果跟踪与退回原因结构化

> **目的：** 把每个 PR 的最终命运（merged / closed / merged-then-reverted）以及原因结构化存储，作为 Prompt 迭代和 Eval Golden Set 的核心数据来源。
> **优先级：** Phase 1 上数据层（`pr_outcomes` + `rejection_reasons`）；Phase 3 接 Golden Set。

### 5.1 PR 最终结果分类（`pull_requests.final_outcome`）

| 取值 | 含义 | 数据来源 |
|------|------|----------|
| `MERGED_CLEAN` | 被 maintainer 接受并 merge，无 review comment 要求改动 | GitHub PR API |
| `MERGED_WITH_CHANGES` | maintainer 要求改动后 merge（含 PR review change request） | PR review API |
| `CLOSED_BY_MAINTAINER` | maintainer 主动关闭 PR，未 merge | webhook close 事件 |
| `CLOSED_BY_US` | 我们因为 Agent C 退回超限 / 抓取错误等主动关闭 | 内部触发 |
| `REVERTED` | 被 merge 但事后被 revert | 后台 30 天巡检 |
| `STALE` | 30 天无任何动作，自动归档 | 后台巡检 |

### 5.2 退回原因 `rejection_reasons` 表

每条记录代表一次"退回信号"，可来自三个 source：

| `source` | 何时产生 | 关键字段 |
|----------|----------|----------|
| `agent_c` | Agent C 评审给 REJECTED 时（每次循环 0-1 条） | `dimension`, `detail` 来自 Agent C 输出 |
| `maintainer_review` | maintainer 在 PR 上留 review comment 要求改动 | `raw_text` 是评论原文，`category` 由后处理 LLM 分类 |
| `maintainer_close` | maintainer 关闭 PR 时的 close comment | 同上 |

**核心字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | enum | 见上表 |
| `category` | enum | `wrong_root_cause`/`incomplete_fix`/`broke_other_tests`/`style_mismatch`/`security_concern`/`scope_creep`/`needs_design_discussion`/`duplicate`/`out_of_scope`/`other` |
| `severity` | enum | `blocker`/`major`/`minor` |
| `dimension` | enum nullable | 对应 Agent C 评审维度（correctness/test/style/security/pr_description）；非 agent_c source 由后处理 LLM 推断 |
| `agent_b_attribution` | enum nullable | 是否归因 Agent B 的失误：`yes`/`no`/`unclear`。**关键学习信号** |
| `detail` | text | 结构化短描述（≤200 字） |
| `raw_text` | text | 原始文本（maintainer 评论原文 / Agent C 完整意见） |
| `referenced_files` | text[] | 提到的文件路径 |
| `referenced_lines` | jsonb | `[{file, start, end}]` |

### 5.3 PR 事件流水 `pr_outcomes` 表

不是状态机，而是事件日志。用于复盘 PR 完整生命周期：

| `event_type` | 触发 | payload |
|--------------|------|---------|
| `submitted` | 我们 PRService 创建 PR | `{pr_url, title, body}` |
| `review_received` | webhook：pull_request_review | `{reviewer, state, body}` |
| `comment_received` | webhook：issue_comment | `{commenter, body}` |
| `pushed_by_us` | dev_worker 重新推 commit | `{commit_sha}` |
| `pushed_by_maintainer` | webhook：push to PR branch | `{commit_sha, author}` |
| `merged` | webhook：pull_request closed+merged | `{merger, sha}` |
| `closed` | webhook：pull_request closed | `{closer, comment}` |
| `reverted_detected` | 巡检发现 revert PR | `{revert_pr_url}` |

### 5.4 repo_profiles 表（Agent D 产出）

详细 schema 与生成时机见 `docs/AGENT_DESIGN.md` §Agent D。要点：

- `repo_id` 唯一索引：一个仓库最多一条有效 profile
- `expires_at` = `generated_at + 90 days`，超时下次入 profile 队列
- `forced_refresh_count`：被 `rejection_reasons.category=style_mismatch` 触发强制刷新的次数（>5 时告警，说明 profile 质量持续不好）
- profile 不存在 / 失效时 Agent B 仍可降级运行（自学习），不阻塞主流程

### 5.5 学习闭环（Phase 3 接入）

`rejection_reasons` 表是 ROADMAP 3.1 "Prompt 质量提升"的核心输入：

- `agent_b_attribution = yes` 且 `severity = blocker` 的案例 → Agent B Prompt 反例 few-shot
- `category = wrong_root_cause` 高频聚类 → Agent A "feasibility" 评分维度调整
- maintainer review 中 `category = style_mismatch` 高频 → Agent B 加强 "学习仓库 CONTRIBUTING.md" 步骤
- 全量 `MERGED_CLEAN` 案例 → Eval Golden Set 正样本

具体管道见 `docs/AGENT_RUNTIME.md` §学习闭环（待补）。
