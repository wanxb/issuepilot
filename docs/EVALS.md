# Evals 规范

> Eval-first 原则：在修改 Prompt 之前先运行 Eval，用数据驱动 Prompt 迭代，而非凭感觉调整。

---

## 哲学

AI 系统的质量不能只靠人工感知。IssuePilot 的三个 Agent 各自有明确的可量化目标，需要在开发过程中持续测量：

- **Agent A**：我推荐的 Issue，用户真的会选吗？
- **Agent B**：写出来的代码，测试能过吗？
- **Agent C**：我通过的代码，maintainer 会 merge 吗？

Eval 不是项目后期才做的事，是和 Prompt 一起设计的。

---

## Agent A — 价值评估 Evals

### 指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **用户采纳率** | 用户选择开发的 Issue 数 / Agent A 推荐开发（score ≥ 6.5）的 Issue 数 | ≥ 50% |
| **误推率** | 用户忽略的 Issue 中，score ≥ 8.0 的比例 | ≤ 20% |
| **漏推率** | 用户手动加入开发的 Issue 中，score < 6.5 的比例 | ≤ 10% |
| **评分校准** | 最终 PR 被 merge 的 Issue，其 Agent A 评分均值 vs 未 merge 的均值 | merge 均值 > 未 merge 均值 |

### Golden Dataset

- **来源**：从已运行的历史数据中，抽取用户做过决策的 Issue（忽略或开发）
- **规模**：≥ 50 条（初期可用手工标注样本启动）
- **格式**：`{issue_input, user_decision, eventual_pr_outcome}`
- **路径**：`backend/evals/data/agent_a_golden.jsonl`

### 运行方式

```bash
uv run python -m evals.run_agent_a \
  --dataset evals/data/agent_a_golden.jsonl \
  --model claude-sonnet-4-6 \
  --output evals/results/agent_a_{timestamp}.json
```

### Regression Gate

Prompt 变更前后对比，以下任一指标回退超过阈值则拒绝：
- 用户采纳率下降 > 10 个百分点
- 误推率上升 > 10 个百分点

---

## Agent B — 开发 Evals

### 指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **首次自测通过率** | 第一次尝试即测试全部通过的 Issue 占比 | ≥ 70% |
| **平均迭代次数** | 完成一个 Issue 平均的 Loop 迭代数 | ≤ 25 |
| **卡死率** | 触发卡死检测或超时终止的比例 | ≤ 15% |
| **按语言通过率** | 各语言的首次通过率，用于发现短板 | Python ≥ 75%，Go ≥ 65% |

### Golden Dataset

- **来源**：精选有明确可验证修复方案的 Issue（有已知 PR 可对比）
- **规模**：≥ 20 条，覆盖 Python / TypeScript / Go 各 ≥ 5 条
- **格式**：`{issue_input, repo_snapshot, expected_files_changed, expected_test_pass}`
- **路径**：`backend/evals/data/agent_b_golden.jsonl`
- **注意**：每条 eval 需要启动一个 Docker 容器，运行成本较高，不在每次 CI 中全量跑

### 运行方式

```bash
# 全量（耗时，手动触发）
uv run python -m evals.run_agent_b \
  --dataset evals/data/agent_b_golden.jsonl \
  --concurrency 2 \
  --output evals/results/agent_b_{timestamp}.json

# 抽样（CI 用，取 5 条代表性样本）
uv run python -m evals.run_agent_b --sample 5
```

### Regression Gate

- 首次自测通过率下降 > 10 个百分点
- 卡死率上升 > 10 个百分点

---

## Agent C — 评审 Evals

### 指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **误拒率** | 人工复查认为"应通过"但 Agent C 拒绝的比例 | ≤ 20% |
| **误放率** | Agent C 通过但人工认为"不应通过"的比例 | ≤ 15% |
| **PR 接受率** | Agent C 通过并提交的 PR，被 maintainer merge 的比例 | ≥ 20%（受外部因素） |
| **退回有效率** | Agent C 退回的意见，Agent B 在下次尝试中被解决的比例 | ≥ 70% |

### Golden Dataset

- **来源**：从已评审的历史记录中，提取人工复查过的 (diff, verdict) 对
- **规模**：≥ 30 条，APPROVED/REJECTED 各占约一半
- **格式**：`{review_input, human_verdict, human_notes}`
- **路径**：`backend/evals/data/agent_c_golden.jsonl`

### 运行方式

```bash
uv run python -m evals.run_agent_c \
  --dataset evals/data/agent_c_golden.jsonl \
  --output evals/results/agent_c_{timestamp}.json
```

### Regression Gate

- 误拒率上升 > 10 个百分点
- 误放率上升 > 10 个百分点

---

## CI 集成

### 触发时机

| 场景 | Eval 内容 | 方式 |
|------|-----------|------|
| Prompt 文件变更 PR | Agent A/C 全量 + Agent B 抽样 | CI 自动触发 |
| 每周定时 | 三个 Agent 全量 | Cron 定时任务 |
| 发布前 | 三个 Agent 全量 | 手动触发 |

### 结果存储

Eval 结果写入 `evals/results/`，同时写入数据库 `eval_runs` 表，供 Dashboard 展示历史趋势。

### 数据积累策略

系统每次运行后，将真实运行数据（Issue 输入、Agent 输出、最终结果）自动写入待审池。定期人工抽样标注后，补充进 Golden Dataset，让 Eval 覆盖越来越多的真实分布。

---

## Eval 与 Prompt 迭代流程

```
观察到问题（误推、卡死、误拒等）
  → 在 Golden Dataset 中找或新增对应样本
  → 运行 Eval，确认问题可被量化复现
  → 修改 Prompt（在代码中）
  → 再次运行 Eval，确认指标改善
  → 确认无 Regression → 合并
```

禁止：没有 Eval 数据支撑，仅凭主观感受修改 Prompt。
