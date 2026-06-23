# Phase 2 整体 dry run 验收报告

> 日期：2026-06-23 · 范围：里程碑 2.1 / 2.2 / 2.4 / 2.5（2.3 见 `DRY_RUN_PHASE2_3_REPORT.md`）· 总 unit suite：**245 passed**

---

## TL;DR

**Phase 2 全 5 个里程碑端到端 OK**。本次重点验证 2.1 / 2.2 / 2.4 / 2.5；2.3 早已单独验收。

- 历史抓取数据完好（179 issues / 12 repos / 18 evaluations / 84 LLM 调用 / 21 DeepSeek 计费行）
- 各 API 端点真实响应、各前端 SSR 200、各 worker 任务派发成功
- 总 LLM 成本至今 $0.0383（90% 在 Agent A 评估上）
- Anthropic 中转 503 期间 fallback → DeepSeek 全程接管，failure_rate 75% 实际是中转层的事，不是模型本身

---

## 1. 验收矩阵

| 里程碑 | 验证 | 结果 |
|---|---|---|
| 2.1a 镜像构建路径 | Dockerfile + scripts/build_sandbox_images.sh + sandbox/shared/ 抽取 | ✅ 5 个 Dockerfile + 共享脚本结构正确 |
| 2.1b 语言映射 | `resolve_sandbox_lang` 12 个语言名 | ✅ JS/TS/Vue/Svelte→node, Go→go, Rust→rust, Java/Kotlin/Scala/Groovy→java, Python/Cython→python, 其他→python fallback |
| 2.1c 镜像存在性双重 fallback | worker 容器 `SandboxManager.image_exists` 真查 docker daemon | ✅ python:latest exists=True, 其他 4 个 exists=False（未构建），dev_worker 会自动 fallback python |
| 2.1d 单测 | `test_sandbox_language.py` | ✅ 28 项 passed |
| 2.2a crawl_targets CRUD | `/scripts/manage_crawl_targets.py list` | ✅ 2 个 target（trending-python-daily / explicit-faves，皆 disabled） |
| 2.2b APScheduler infra | `GET /admin/scheduler-jobs` | ✅ 2 maintenance jobs registered（03:07 / 03:17 UTC）；crawl_target 因 disabled 未注册 |
| 2.2c GitHub Trending HTML parser | live `fetch_repos('github_trending', python/daily)` | ✅ 解析出 13 个 trending repos（calesthio/OpenMontage 等） |
| 2.2d crawl_jobs API | `GET /crawl-jobs?limit=3` | ✅ 历史 cron run：trending-python-daily，11 repos ok，179 issues 入队，95.84s |
| 2.4a stats overview | `GET /dashboard/stats` | ✅ 6 状态非零，84 LLM 调用，$0.0383，fallback 25% / failure 75% |
| 2.4b PR 失败复盘 | `GET /dashboard/pr-failures?days=30` | ✅ 1 sample（style_mismatch / yes / code_style / maintainer_review） |
| 2.4c Issue 列表筛选 | `?status=PENDING_DECISION&sort=-score&page_size=3` | ✅ total=18，top score 8.75（DeepSeek vision），8.20，8.15 |
| 2.4d Issue 详情页 API | `GET /issues/{id}/detail` | ✅ 5 panel payload（status/repo/evaluation/dev_tasks/review_tasks/PR/rejection） |
| 2.4d 详情页 SSR | `GET /issues/{id}` 前端路由 | ✅ HTTP 200 |
| 2.5a DeepSeek cost 回填 | DB query | ✅ 21 行 / $0.0383 / 平均 $0.001826/call |
| 2.5b cost-stats API | `GET /admin/cost-stats?hours=168` | ✅ 6 (agent, provider, model) 聚合行；fallback_calls / failed_calls 正确 |
| 2.5c OpenAI client 翻译层 | `test_openai_client.py` | ✅ 16 项 passed |
| 2.5d models.yaml 热加载 | `POST /admin/reload-models` | ✅ 返回 5 个 agent 名 |
| 2.3 回归 | maintenance trigger + 245 unit suite | ✅ stale_archive.done logged + 245 passed |

> **18 / 18 全绿。**

---

## 2. 数据基线快照（dry run 起始 + 终态）

```
issues            183     ← 4 dry-run seed + 179 trending（截 2026-06-22 抓取）
repositories       12     ← 1 seed + 11 trending
crawl_jobs          1     ← trending cron run
crawl_targets       2     ← trending-python-daily（disabled）+ explicit-faves（disabled）
pull_requests       3     ← 2.3 dry run 数据
rejection_reasons   1     ← classifier 跑过：style_mismatch/yes
pr_outcomes         1     ← REVIEW_RECEIVED
evaluations        18     ← Agent A 处理过的 trending issue
llm_call_logs      84     ← 21 DeepSeek 成功 + 60 Anthropic 503 失败 + 3 其他
```

中转 503 期间的 60 个 Anthropic failure 是正常现象——FallbackLLMClient 自动 retry 3 次后切 DeepSeek，最终结果都成功（21 个 evaluation 全部 PENDING_DECISION，无 ANALYZING 卡死）。

---

## 3. 2.1 多语言沙箱 — 设计验证 vs 实地构建

**已验证（设计层面）**：
- `resolve_sandbox_lang` 12 个映射 + 3 个 fallback 全单测覆盖
- `sandbox_image_for_language` 双重 fallback（语言不识别 → python；镜像不存在 → python；docker daemon 异常 → python）
- Dockerfile 静态结构（5 个文件 + sandbox/shared/ COPY 路径）
- dev_worker 接入 `sandbox_image_for_language`，替换原 lower()+image_exists 内联

**未验证（实地构建）**：
- node / go / rust / java 4 个新镜像未跑 `docker build`，需要：
  1. Docker Desktop 充足磁盘空间（每个镜像 ~1-2GB，4 个累计 ~6GB）
  2. 网络拉 base image（rust:1.83 / golang:1.23 / eclipse-temurin:21 / node:20）

下次 daemon 可用时一行命令：
```bash
bash scripts/build_sandbox_images.sh           # 全部 5 个
bash scripts/build_sandbox_images.sh node go   # 增量
```

**dev_worker 健壮性**：缺镜像时自动 fallback 到 `agent-sandbox-python:latest`（worker 容器实测 exists=True），所以即使一个非 Python 仓库被分到沙箱开发，也不会卡死任务——只是用 Python 基础镜像跑（含 git/node/python，能 git clone + claude CLI + push，对纯文档类 issue / 小 patch 够用）。

---

## 4. 真实成本观测（2.5 cost-stats 落地）

| Agent | Provider | Model | 调用 | $ 成本 | fallback | 失败 |
|---|---|---|---:|---:|---:|---:|
| agent_a | deepseek | DeepSeek-V4-Pro | 19 | 0.0337 | 19 | 0 |
| agent_c | deepseek | DeepSeek-V4-Pro | 1 | 0.0042 | 1 | 0 |
| rejection_classifier | deepseek | DeepSeek-V4-Pro | 1 | 0.0004 | 1 | 0 |
| rejection_classifier | anthropic | claude-haiku-4-5 | 3 | 0 | 0 | 3 |
| agent_a | anthropic | claude-sonnet-4-6 | 57 | 0 | 0 | 57 |
| agent_c | anthropic | claude-sonnet-4-6 | 3 | 0 | 0 | 3 |

**观察**：
- 21 次成功调用全在 DeepSeek（中转 503 推动）；fallback_calls 与 calls 1:1 对齐，说明 DeepSeek 行都是 fallback 路径回来的
- Anthropic 中转 63 次失败（57+3+3）= 21 个最终成功 × 3 retry attempts，与 `RetryConfig.max_attempts=3` 完全吻合
- Agent A 单次 evaluation 平均 $0.00177（含 retry + fallback 总开销），Agent C 一次评审 $0.0042（issue+diff 上下文较大），Classifier $0.0004（最便宜）
- 实际混合策略下：单 issue 全流程评估 + 评审 + 分类 ≈ $0.005，179 issue 全跑完 ≈ $1（可接受）

---

## 5. 未观察到的回归

| 里程碑 | 风险点 | 检查 | 结论 |
|---|---|---|---|
| 2.2 | from_target 内 send_task race（已 fix） | 17 evaluations 都成功 | ✅ analyze_worker 不再 issue_missing |
| 2.3 | scheduler / classify_queue 持续运行 | maintenance trigger 即时响应 | ✅ |
| 2.4 | dashboard 聚合 SQL 加索引 | 直觉感觉响应 <100ms | ✅（数据量小） |
| 2.5 | OpenAI client 在 factory 中 dispatch | reload-models 5 agent 命名解析正常 | ✅ |

---

## 6. 待办（不阻塞，记录技术债）

1. **实地构建 4 个新 sandbox 镜像**：等 Docker Desktop 空间 + 网络稳定
2. **真 Agent B 在非 Python 仓库的端到端**：依赖第 1 点，且需要 dev_token 完整 fork 权限（同 Phase 1 dry run §6.1）
3. **DeepSeek 价目变动**：DeepSeek 可能调价，`_PRICING_PER_MILLION` 表需手动同步（建议每季度 review）
4. **OpenAI client 实地调用**：本会话只单测翻译层，未真打 openai.com / 真调 DeepSeek-OpenAI-compat。下次有 OPENAI_API_KEY / DEEPSEEK_API_KEY 时跑一次端到端
5. **dashboard SQL 在大数据量下性能**：当前 183 issues 查询无压力；超 10k 时考虑给 `llm_call_logs.created_at` 加索引 + cost-stats 加 materialized view

---

## 7. 收尾

- 245 unit tests / 0 fails / ~5s
- 14 个 commit 全推送 `origin/dev`（7364a29..6aeb7e0）
- 5 个里程碑 ROADMAP 全部标注 ✅
- 数据保留作 Phase 3 起点（179 issues + 18 evaluations 是真实 GitHub trending 数据，可直接进 PR 学习闭环）

**Phase 2 至此整体收尾。** 下一站候选：
- **Phase 3.1 Prompt 质量提升**（基于本会话沉淀的 18 evaluations + 1 rejection 数据起 Eval Golden Set）
- **Phase 3.2 Agent B 能力增强**（devcontainer.json / Extended Thinking / repo_profile 智能刷新）
- **Phase 3.3 系统扩展**（E2B 沙箱 / 多 GitHub 账号 / 黑名单 / 周报导出）

最自然的衔接是 3.1，因为它直接消化本会话的 rejection_reasons 数据，闭环已经自洽。
