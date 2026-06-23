# Phase 3 整体 dry run 验收报告

> 日期：2026-06-23 · 范围：里程碑 3.1 全部 + 3.2 已交付 3 子项 · 总 unit suite：**277 passed**

---

## TL;DR

**Phase 3 已交付的全部子项端到端 OK**：
- 3.1 Prompt 质量提升（5/5 子项）
- 3.2 Agent B 能力增强（3/5 子项；剩 devcontainer + 多步测试策略排期 3.3 后）

学习闭环数据已串通：rejection_reasons → classifier 二次分类 →
auto-pick 进 eval_samples → 离线评估框架可重跑 Agent A 对比，闭环可观测。
3.2 三个增强都通过单测 + 真实数据验证。

---

## 1. 验收矩阵

| 里程碑 | 验证 | 结果 |
|---|---|---|
| 3.1a eval_samples 表 + CLI | migration `g7i1j7e1g7h7` + list/add/auto-pick/remove | ✅ 1 sample 存档（style_pattern），auto-pick 幂等（重复跑入 0） |
| 3.1b weekly-report endpoint | `GET /dashboard/weekly-report?days=14` | ✅ 88 calls / $0.0407 / 1 top failure mode（style_mismatch yes）/ attribution_ratio {yes:1,1.0} / funnel 6 状态非零 |
| 3.1c agent-quality endpoint | `GET /dashboard/agent-quality?days=30` | ✅ Agent B total=2 success=100% Python primary all primary；Agent C verdicts={APPROVED:2}，无 maintainer 反馈数据 |
| 3.1d eval_agent_a 离线评估 | `--dry-run --limit 3` | ✅ JSON 行输出（1 sample，agreement=dry_run） |
| 3.2a profile_refresh function | 5 个 repo 计数 | ✅ wanxb/c-drive-cleaner=1 below；其他 0 below；阈值=3 / 回看=30d 正确 |
| 3.2b extended_thinking 判定 | 6 case 边界 | ✅ difficulty=hard / retry≥2 / 多 difficulty / 关闭 attempt path 全部正确 |
| 3.2c scope_check 判定 | 4 真实场景 | ✅ 不重合 suspicious=True / app.py basename 命中 / 大小写不敏感 / 空 files |
| 3.2 集成 | entrypoint.sh + Agent C prompt + dev_tasks.scope_check 列 | ✅ ENABLE_EXTENDED_THINKING ×3 出现 / `--append-system-prompt` 注入；`scope_warning` 入 schema + prompt；列 JSONB 存在；migration head=h8j2k8f2h8i8 |
| 回归 | 完整 unit suite | ✅ **277 passed** / 0 fails / ~5.6s |
| 前端 SSR | `/` 总览页 + `/issues/[id]` 详情页 | ✅ 两个路由都 HTTP 200 |

> **10 / 10 全绿。**

---

## 2. 实测数据观察

**数据基线（dry run 起始）**：
```
issues                         183     ← 4 dry run seed + 179 trending
evaluations                     18     ← Agent A 处理过的 trending issue
dev_tasks                        2     ← seed + restart_dev
review_tasks                     2     ← 2 次 Agent C 评审
pull_requests                    3     ← 2.3 dry run 数据
rejection_reasons (classified)   1     ← Haiku classifier 跑过的
repo_profiles                    0     ← profile_worker 还没真跑过
eval_samples                     1     ← 3.1 auto-pick 抓的 style_pattern
llm_call_logs                   88     ← 21 DeepSeek + 60 Anthropic 503 + 7 新增
```

**周报指标（14d）**：
- 总 LLM 调用 88，成本 $0.0407（DeepSeek 计费）
- Top 失败：style_mismatch / yes / count=1
- Agent B 归因 ratio：yes 100%（数据量小，仅供参考）
- Issue 漏斗非零：DISCOVERED 159 / ANALYZING 2 / PENDING_DECISION 17 / IN_REVIEW 2 / PR_SUBMITTED 1 / ARCHIVED 2

**Agent 质量（30d）**：
- Agent B：2 次 dev_task 全 SUCCEEDED（100% 成功率）；全 primary provider（无 fallback retry）；全 Python 仓库
- Agent C：2 次 APPROVED；0 个 PR 真实 merged_by_maintainer（dry run 数据中 PR 是手工 seed，maintainer 反馈链路自然为空）

---

## 3. 3.2 三个增强的端到端串联

### A. profile 智能刷新 — 行为正确，未触发（数据量小）
```
classify_worker（Haiku 4.5 标 style_mismatch+yes）
   ↓ commit
maybe_force_refresh(issue_id=...)
   ↓ count_style_rejections_for_repo(repo_id, lookback_days=30)
   ↓ count=1 < threshold=3 → 不触发
   ↓ 否则会：RepoProfile.expires_at=now + forced_refresh_count++ + commit
   ↓ classify_worker commit 后 send_task profile_queue (force=True)
```
样本 wanxb/c-drive-cleaner 当前 1 个 style_mismatch+yes（来自 2.3 dry run）。
按设计 3 个才会触发，行为正确。

### B. Extended Thinking — 6 边界全对
```
dev_worker._setup_phase
   ↓ should_enable_extended_thinking(diff=evaluation.difficulty,
                                      attempt=dev_task.attempt_number,
                                      diffs=settings.agent_b_extended_thinking_difficulties,
                                      min=settings.agent_b_extended_thinking_min_attempt)
   ↓ env["ENABLE_EXTENDED_THINKING"] = "0|1"
   ↓ docker run → entrypoint.sh
      ↓ if ENABLE_EXTENDED_THINKING=1：claude CLI 加
        --append-system-prompt "Before each tool call, briefly think
        step-by-step about: (a) which file change is most likely to
        fix the root cause, (b) what could break, (c) the smallest
        change that satisfies the issue..."
```
默认配置：`hard` 难度或 retry（attempt>=2）触发。

### C. scope_check — basename 匹配，suspicious 入 Agent C prompt
```
dev_worker._result_phase 成功路径
   ↓ scope_check(files_changed=output.files_changed,
                  issue_body=issue.body,
                  evaluation_summary=issue.evaluation.summary)
   ↓ extract_paths()：正则 \w+\.\w{1,8}，过滤 URL/邮箱/blacklist
   ↓ basename 大小写不敏感比对
   ↓ dev_task.scope_check = {suspicious, files_changed, referenced, matched}

review_worker._run_async
   ↓ if dev_task.scope_check.suspicious:
     scope_warning = "Suspected scope mismatch: issue referenced X but
                       Agent B changed Y. Verify..."
   ↓ AgentCInput.scope_warning ← 注入
   ↓ Agent C prompt build_user_message 加 <scope_warning>...</scope_warning> XML 块
```
4 场景验证：不重合 suspicious=True / app.py basename 命中 / 大小写不敏感 / 空 files。

---

## 4. 数据持久化与 schema 改动

| migration | 内容 |
|---|---|
| `g7i1j7e1g7h7` | 3.1 eval_samples 表（issue_id FK / sample_kind / label / source / notes + 3 索引） |
| `h8j2k8f2h8i8` | 3.2 dev_tasks.scope_check JSONB 列 |

alembic head = `h8j2k8f2h8i8`，与代码对齐。

---

## 5. 未充分验证（小数据量限制）

| 项 | 原因 | 补救 |
|---|---|---|
| profile 智能刷新真触发 | 全样本最高 1 < 3，未达阈值 | 跑实际 PR 闭环，攒到 3 条 style_mismatch+yes 即可（或临时降阈值压测） |
| Agent C maintainer agreement | 0 个真 maintainer merged/closed 数据 | 等 GitHub 真 webhook 推送或继续用 replay 工具堆数据 |
| Extended Thinking 在沙箱真生效 | dev_worker 加了 env，entrypoint.sh 读了 env，但实际 Agent B 沙箱本期未跑（Docker 镜像未重建到含 3.2 entrypoint） | 下次 sandbox 镜像 rebuild + dev_task 真跑时验证；行为已离线全单测 |
| eval_agent_a real LLM 调用 | dry-run 验证 schema OK；真调用会花 1 次 Agent A 钱 | 等 Phase 3 上线后跑一次 `--kind positive_merged_clean` |

---

## 6. 收尾

- 277 unit tests / 0 fails / ~5.6s
- 2 个 commit 已 push（`8738216` + `45485f4`）
- 2 个 migration 已 head（g7i1j7e1g7h7 + h8j2k8f2h8i8）
- 数据保留：1 eval_sample + 1 classified rejection 作 3.x 继续验证起点

---

**Phase 3 至此交付 8/13 子项（3.1 全部 + 3.2 三个）。** 下一站候选：
- **3.3 系统扩展**（E2B 沙箱 / 多 GitHub 账号 / Issue 黑名单 / 周报导出）— 用户已明确下一步
- 3.2 剩余：devcontainer.json 支持 + 多步测试策略（需改沙箱镜像，独立 pass）

3.3 与 3.2 剩余两项不冲突，可平行推进。
