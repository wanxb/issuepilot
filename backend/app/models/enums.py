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


# ---------------------------------------------------------------------------
# 1.5a — Agent C / Agent D / PR 链路新增 enum
# ---------------------------------------------------------------------------


class ReviewTaskStatus(str, enum.Enum):
    """Agent C 单次评审尝试的状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"       # Agent C 给出 verdict（APPROVED 或 REJECTED）
    FAILED = "failed"             # tool 未调 / schema 失败 / 系统异常


class ReviewVerdict(str, enum.Enum):
    """Agent C 评审结论（AGENT_DESIGN.md §Agent C）。"""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PullRequestStatus(str, enum.Enum):
    """`pull_requests.status` —— PR 当前生命周期状态。"""

    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class PRFinalOutcome(str, enum.Enum):
    """`pull_requests.final_outcome` —— 学习闭环核心字段。

    详见 DATA_MODEL.md §5.1。仅在 PR 关闭/合并后写入。
    """

    MERGED_CLEAN = "MERGED_CLEAN"
    MERGED_WITH_CHANGES = "MERGED_WITH_CHANGES"
    CLOSED_BY_MAINTAINER = "CLOSED_BY_MAINTAINER"
    CLOSED_BY_US = "CLOSED_BY_US"
    REVERTED = "REVERTED"
    STALE = "STALE"


class PROutcomeEventType(str, enum.Enum):
    """`pr_outcomes.event_type` —— PR 生命周期事件类型。

    详见 DATA_MODEL.md §5.3。事件日志而非状态机。
    """

    SUBMITTED = "submitted"
    REVIEW_RECEIVED = "review_received"
    COMMENT_RECEIVED = "comment_received"
    PUSHED_BY_US = "pushed_by_us"
    PUSHED_BY_MAINTAINER = "pushed_by_maintainer"
    MERGED = "merged"
    CLOSED = "closed"
    REVERTED_DETECTED = "reverted_detected"


class RejectionSource(str, enum.Enum):
    """`rejection_reasons.source` —— 退回信号来源。"""

    AGENT_C = "agent_c"
    MAINTAINER_REVIEW = "maintainer_review"
    MAINTAINER_CLOSE = "maintainer_close"


class RejectionCategory(str, enum.Enum):
    """`rejection_reasons.category` —— 学习闭环聚类维度。"""

    WRONG_ROOT_CAUSE = "wrong_root_cause"
    INCOMPLETE_FIX = "incomplete_fix"
    BROKE_OTHER_TESTS = "broke_other_tests"
    STYLE_MISMATCH = "style_mismatch"
    SECURITY_CONCERN = "security_concern"
    SCOPE_CREEP = "scope_creep"
    NEEDS_DESIGN_DISCUSSION = "needs_design_discussion"
    DUPLICATE = "duplicate"
    OUT_OF_SCOPE = "out_of_scope"
    OTHER = "other"


class RejectionSeverity(str, enum.Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class RejectionDimension(str, enum.Enum):
    """对应 Agent C 评审维度；maintainer source 时由 RejectionClassifier 推断。"""

    CORRECTNESS = "correctness"
    TEST_COVERAGE = "test_coverage"
    CODE_STYLE = "code_style"
    SECURITY = "security"
    PR_DESCRIPTION = "pr_description"


class AgentBAttribution(str, enum.Enum):
    """是否归因 Agent B 的失误。学习闭环关键信号。"""

    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class ProfileQuality(str, enum.Enum):
    """`repo_profiles.profile_quality` —— Agent D 输出的画像质量自评。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
