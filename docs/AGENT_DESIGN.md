# Agent 设计规范

> 本文档定义三个 Agent 的职责边界、接口契约（输入/输出 Schema、Tool Definition）、评分标准和设计决策。
> Prompt 全文是代码，存放在 `backend/app/agents/prompts/`，不在本文档中重复。

---

## 设计决策

**为什么 Agent A/C 用 Single-Shot，Agent B 用 ReAct Loop？**

- Agent A/C 的任务是分析后输出一次性判断，无需与外部环境交互，Single-Shot 更可预测、成本更低。
- Agent B 需要探索代码库、修改文件、运行测试，必须与文件系统和 shell 多轮交互，ReAct 是唯一合理选择。

**为什么用 Tool Use 做结构化输出，而非 JSON 模式或 Instructor？**

Tool Use 的 Schema 在模型调用侧强制校验，不依赖后处理解析，失败时有明确的重试语义。JSON 模式没有字段级约束，Instructor 增加额外依赖。所有 Agent 均使用 Tool Use。

**Prompt 设计核心原则：**
- 角色和能力边界在 System Prompt 第一段定义
- 负例约束（禁止做什么）比正例更有效
- 只传入完成任务必要的信息，减少噪声
- 每个 Agent 有且只有一个终止工具

---

## Agent A — Issue 价值评估

### 职责

分析 GitHub Issue，输出多维度评分和结构化报告，帮助用户决策是否投入开发。不修改任何代码，不做开发建议。

### 输入

```python
class AgentAInput(BaseModel):
    issue_title: str
    issue_body: str                  # 超过 2000 字时截取：前 1500 + 后 300
    issue_labels: list[str]
    issue_url: str
    repo_full_name: str
    repo_description: str | None
    repo_language: str | None
    repo_stars: int
    repo_topics: list[str]
    repo_last_commit_days: int
    repo_open_prs_count: int
    repo_merged_prs_last_30d: int
```

### 输出（通过 Tool Use 强制）

```python
class AgentAOutput(BaseModel):
    total_score: float               # 0.0–10.0，按维度权重加权
    difficulty: Literal["easy", "medium", "hard"]
    estimated_hours: float           # AI 自动化开发预估小时数
    summary: str                     # 中文，100–150 字，给用户看
    recommendation: str              # 中文，50 字内
    is_worth_developing: bool        # total_score >= 6.5 时为 True，不可主观覆盖
    dimensions: dict[str, Dimension]

class Dimension(BaseModel):
    score: float                     # 0.0–10.0
    comment: str                     # 50 字内
```

### 评分维度与权重

| 维度 | 权重 | 高分条件 | 直接 0 分条件 |
|------|------|----------|---------------|
| `clarity` | 25% | 有复现步骤、预期/实际行为、错误日志 | 描述矛盾或无法定位 |
| `feasibility` | 25% | 有明确代码路径，不需理解整个系统 | 纯文档讨论、需专有账号、maintainer 明确拒绝 |
| `value` | 20% | 影响用户多、常见痛点 | — |
| `repo_activity` | 20% | 近 30 天有 PR merge，maintainer 回复 Issue | 超过 180 天无 commit 则上限 3 分 |
| `context_sufficiency` | 10% | 有测试、有注释、有 CONTRIBUTING.md | — |

### Tool Definition（`evaluate_issue`）

```json
{
  "name": "evaluate_issue",
  "description": "提交 Issue 价值评估结果",
  "input_schema": {
    "type": "object",
    "required": ["total_score", "difficulty", "estimated_hours", "summary",
                 "recommendation", "is_worth_developing", "dimensions"],
    "properties": {
      "total_score":           { "type": "number", "minimum": 0, "maximum": 10 },
      "difficulty":            { "type": "string", "enum": ["easy", "medium", "hard"] },
      "estimated_hours":       { "type": "number" },
      "summary":               { "type": "string" },
      "recommendation":        { "type": "string" },
      "is_worth_developing":   { "type": "boolean" },
      "dimensions": {
        "type": "object",
        "required": ["clarity", "feasibility", "value", "repo_activity", "context_sufficiency"],
        "additionalProperties": {
          "type": "object",
          "required": ["score", "comment"],
          "properties": {
            "score":   { "type": "number", "minimum": 0, "maximum": 10 },
            "comment": { "type": "string" }
          }
        }
      }
    }
  }
}
```

---

## Agent B — 代码开发

### 职责

在 Docker 隔离沙箱内，基于 Issue 完成代码修改、测试编写、自测，输出可评审的 git diff。通过 Claude Code CLI headless 模式执行，具备完整的文件系统和 shell 工具访问能力。

### 执行阶段

| 阶段 | 描述 | 终止条件 |
|------|------|----------|
| ANALYZE | 读取仓库结构，定位相关文件 | 明确知道要改什么 |
| PLAN | 制定修改方案 | 方案明确，无歧义 |
| IMPLEMENT | 执行代码修改 | 所有计划修改完成 |
| TEST | 运行原有测试套件 + 新增测试 | 全部通过 |
| COMMIT | git commit | commit 完成 |

### 输入

```python
class AgentBInput(BaseModel):
    issue_title: str
    issue_body: str
    issue_url: str
    repo_full_name: str
    repo_language: str
    evaluation_summary: str          # Agent A 的评估摘要，提供背景
    review_context: str | None       # Agent C 退回时的评审意见（重试时传入）
    attempt_number: int
    branch_name: str                 # 预创建的分支名
    forked_repo: str                 # Fork 后的仓库 full_name
```

### 输出（通过 Tool Use 强制）

```python
class AgentBOutput(BaseModel):
    success: bool
    files_changed: list[str]
    diff_summary: str                # 200 字内，给 Agent C 和 PR body 使用
    test_passed: bool
    total_tests: int
    failed_tests: int
    new_tests_added: int
    test_output_snippet: str         # 最后 500 字符

class AgentBFailure(BaseModel):
    reason: Literal[
        "cannot_locate_issue",       # 无法定位问题根源
        "requires_external_deps",    # 需要外部服务/数据库
        "issue_is_invalid",          # Issue 描述与实际不符
        "test_infrastructure",       # 测试环境问题
        "out_of_scope"               # 超出能力边界
    ]
    detail: str                      # 100 字以上
```

### 关键约束（注入 System Prompt）

- 只修改与 Issue 直接相关的代码，不做无关重构
- 严格遵循原仓库代码风格（缩进、命名、注释语言）
- 不引入新的外部依赖（除非 Issue 明确要求）
- Commit message 格式：`fix: {描述}` 或 `feat: {描述}`
- 原有测试全部通过后，才能进入 COMMIT 阶段
- 遇到无法解决的障碍，立即调用 `report_failure`，不无限循环尝试

### Tool Definitions

**`report_completion`** — 任务完成时调用（唯一正常终止路径）

```json
{
  "name": "report_completion",
  "input_schema": {
    "type": "object",
    "required": ["success", "files_changed", "diff_summary",
                 "test_passed", "total_tests", "failed_tests", "new_tests_added"],
    "properties": {
      "success":             { "type": "boolean" },
      "files_changed":       { "type": "array", "items": { "type": "string" } },
      "diff_summary":        { "type": "string" },
      "test_passed":         { "type": "boolean" },
      "total_tests":         { "type": "integer" },
      "failed_tests":        { "type": "integer" },
      "new_tests_added":     { "type": "integer" },
      "test_output_snippet": { "type": "string" }
    }
  }
}
```

**`report_failure`** — 遇到无法解决的障碍时调用

```json
{
  "name": "report_failure",
  "input_schema": {
    "type": "object",
    "required": ["reason", "detail"],
    "properties": {
      "reason": {
        "type": "string",
        "enum": ["cannot_locate_issue", "requires_external_deps",
                 "issue_is_invalid", "test_infrastructure", "out_of_scope"]
      },
      "detail": { "type": "string" }
    }
  }
}
```

---

## Agent C — 代码评审

### 职责

评审 Agent B 的代码修改。通过则生成 PR 标题和正文，触发 PR 提交；不通过则附具体修改意见退回 Agent B。

### 输入

```python
class AgentCInput(BaseModel):
    issue_title: str
    issue_body: str
    repo_full_name: str
    repo_language: str
    diff_content: str                # git diff，超过 6000 tokens 时按文件重要性截取
    diff_summary: str                # Agent B 的修改摘要
    test_result: TestResult
    files_changed: list[str]
    attempt_number: int
    previous_rejections: list[str]   # 历次退回原因，供评审参考
```

### 输出（通过 Tool Use 强制）

```python
class AgentCOutput(BaseModel):
    verdict: Literal["APPROVED", "REJECTED"]
    overall_score: float
    dimensions: dict[str, ReviewDimension]
    rejection_reason: str | None     # REJECTED 时必填，含文件名+行号+修改建议
    pr_title: str | None             # APPROVED 时必填，英文
    pr_body: str | None              # APPROVED 时必填，英文 Markdown
    overall_comment: str

class ReviewDimension(BaseModel):
    score: int                       # 1–10
    passed: bool
    comment: str
```

### 评审维度与通过标准

| 维度 | 权重 | 通过条件 |
|------|------|----------|
| `correctness` | 35% | score ≥ 7，逻辑正确，解决 Issue 描述的问题 |
| `test_coverage` | 25% | score ≥ 6，且 `failed_tests == 0`（硬性） |
| `code_style` | 20% | score ≥ 6，与原仓库风格一致 |
| `security` | 10% | score ≥ 7，无注入漏洞、无硬编码敏感信息 |
| `pr_description` | 10% | score ≥ 5，diff_summary 清晰 |

**总体通过条件：** `overall_score ≥ 7.0` 且 `correctness.passed = true` 且 `test_coverage.passed = true`

**退回意见规范：** `rejection_reason` 必须包含具体文件名和行号，以及明确的修改建议。不允许写"代码有问题"这类模糊表述。

### Tool Definition（`submit_review`）

```json
{
  "name": "submit_review",
  "input_schema": {
    "type": "object",
    "required": ["verdict", "overall_score", "dimensions", "overall_comment"],
    "properties": {
      "verdict":          { "type": "string", "enum": ["APPROVED", "REJECTED"] },
      "overall_score":    { "type": "number", "minimum": 0, "maximum": 10 },
      "dimensions": {
        "type": "object",
        "required": ["correctness", "test_coverage", "code_style", "security", "pr_description"],
        "additionalProperties": {
          "type": "object",
          "required": ["score", "passed", "comment"],
          "properties": {
            "score":   { "type": "integer", "minimum": 1, "maximum": 10 },
            "passed":  { "type": "boolean" },
            "comment": { "type": "string" }
          }
        }
      },
      "rejection_reason": { "type": ["string", "null"] },
      "pr_title":         { "type": ["string", "null"] },
      "pr_body":          { "type": ["string", "null"] },
      "overall_comment":  { "type": "string" }
    }
  }
}
```

---

## 多厂商模型配置

配置文件位置：`backend/config/models.yaml`（纳入 `.gitignore`，通过 `models.example.yaml` 提供模板）

```yaml
agents:
  agent_a:
    provider: anthropic          # anthropic | openai | deepseek | qwen
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 4096
    temperature: 0.3

  agent_b:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 8192
    temperature: 0.2

  agent_c:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 4096
    temperature: 0.1
```

支持的厂商实现：`AnthropicClient`、`OpenAIClient`（含 DeepSeek/Qwen 兼容接口）。所有实现继承 `LLMClient` 抽象基类，上层代码不感知厂商差异。
