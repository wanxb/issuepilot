"""跨模型共用的 Python enum 定义。

约定：
    - 所有 enum 以字符串形式存库（PostgreSQL ENUM 类型），便于 psql 直接读
    - enum 名 = 表中 column 名（小写）
    - 新增取值时 schema 必须有迁移
"""
from __future__ import annotations

import enum


class IssueStatus(str, enum.Enum):
    """Issue 状态机（详见 docs/DATA_MODEL.md §2）。

    状态只能通过 IssueService 变更，不得在其他层直接写。
    """

    DISCOVERED = "DISCOVERED"
    ANALYZING = "ANALYZING"
    PENDING_DECISION = "PENDING_DECISION"
    IGNORED = "IGNORED"
    QUEUED_DEV = "QUEUED_DEV"
    IN_DEV = "IN_DEV"
    DEV_TESTING = "DEV_TESTING"
    DEV_FAILED = "DEV_FAILED"
    QUEUED_REVIEW = "QUEUED_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    PR_SUBMITTED = "PR_SUBMITTED"
    PR_MERGED = "PR_MERGED"
    PR_CLOSED = "PR_CLOSED"
    ARCHIVED = "ARCHIVED"


class IssueSource(str, enum.Enum):
    """Issue 进入系统的入口。"""

    CRAWL = "crawl"           # 定时抓取
    MANUAL = "manual"         # 手动 URL 输入
    REOPEN = "reopen"         # PR 关闭后人工重新开发


class CrawlTrigger(str, enum.Enum):
    """抓取任务触发方式。"""

    CRON = "cron"                    # APScheduler 定时
    MANUAL_TARGET = "manual_target"  # 用户在配置中手动加 target 后触发
    MANUAL_URL = "manual_url"        # 看板输入框粘贴 URL


class CrawlStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"   # 部分成功，例如 50 个 issue 中 47 个写入成功


class AgentKind(str, enum.Enum):
    """LLM 调用归属的 Agent 种类。用于成本与质量归因。"""

    AGENT_A = "agent_a"
    AGENT_B = "agent_b"
    AGENT_C = "agent_c"
    AGENT_D = "agent_d"
    REJECTION_CLASSIFIER = "rejection_classifier"
    OTHER = "other"


class IssueDifficulty(str, enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class DevTaskStatus(str, enum.Enum):
    """Agent B 单次开发尝试的状态。"""

    PENDING = "pending"           # 入队等待 worker
    RUNNING = "running"           # 沙箱中执行 ANALYZE/PLAN/IMPLEMENT/TEST
    SUCCEEDED = "succeeded"       # report_completion 收到 + 测试全绿
    FAILED = "failed"             # report_failure 收到 / max_turns / 测试失败
    TIMEOUT = "timeout"           # 沙箱执行超时被强制 kill


class DevLogLevel(str, enum.Enum):
    """dev_logs.level —— 用于前端着色。"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DevLogStep(str, enum.Enum):
    """Agent B 5 阶段（AGENT_DESIGN.md §Agent B 执行阶段）+ 系统事件。"""

    SETUP = "setup"               # 系统：clone、安装依赖
    ANALYZE = "analyze"
    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"
    COMMIT = "commit"
    SYSTEM = "system"             # 系统：超时、强制终止、stuck 检测
