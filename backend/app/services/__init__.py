"""业务逻辑层（Service）。

约定（见 CLAUDE.md 关键约束 1）：
    - Issue.status 只能通过 IssueService 变更
    - 任何其他层不得直接 INSERT/UPDATE issues.status 字段
"""
