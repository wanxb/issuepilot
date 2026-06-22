/**
 * 与 backend/app/schemas + backend/app/models/enums 对齐的 TypeScript 类型。
 * 改动时需要同步双方。
 */

export type IssueStatus =
  | "DISCOVERED"
  | "ANALYZING"
  | "PENDING_DECISION"
  | "IGNORED"
  | "QUEUED_DEV"
  | "IN_DEV"
  | "DEV_TESTING"
  | "DEV_FAILED"
  | "QUEUED_REVIEW"
  | "IN_REVIEW"
  | "REVIEW_REJECTED"
  | "PR_SUBMITTED"
  | "PR_MERGED"
  | "PR_CLOSED"
  | "ARCHIVED";

export type IssueSource = "crawl" | "manual" | "reopen";
export type IssueDifficulty = "easy" | "medium" | "hard";

export interface RepoView {
  full_name: string;
  primary_language: string | null;
  stars: number;
}

export interface EvaluationView {
  total_score: number;
  difficulty: IssueDifficulty;
  estimated_hours: number;
  summary: string;
  is_worth_developing: boolean;
  is_fallback: boolean;
}

export type PullRequestStatus = "OPEN" | "MERGED" | "CLOSED";
export type PRFinalOutcome =
  | "MERGED_CLEAN"
  | "MERGED_WITH_CHANGES"
  | "CLOSED_BY_MAINTAINER"
  | "CLOSED_BY_US"
  | "REVERTED"
  | "STALE";

export interface PullRequestView {
  github_pr_number: number;
  github_pr_url: string;
  status: PullRequestStatus;
  final_outcome: PRFinalOutcome | null;
  title: string;
  submitted_at: string;
}

export interface IssueListItem {
  id: string;
  status: IssueStatus;
  source: IssueSource;
  github_url: string;
  title: string;
  created_at: string;
  repository: RepoView;
  evaluation: EvaluationView | null;
  active_dev_task_id: string | null;
  pull_request: PullRequestView | null;
}

export type DevLogLevel = "debug" | "info" | "warning" | "error";
export type DevLogStep = "setup" | "analyze" | "plan" | "implement" | "test" | "commit" | "system";

export interface DevLogEntry {
  id: string;
  level: DevLogLevel;
  step: DevLogStep;
  message: string;
  created_at: string;
}

export interface IssueListResponse {
  items: IssueListItem[];
  page: number;
  page_size: number;
  total: number;
}

export type ManualMode = "repo" | "issue";

export interface ManualSubmitRequest {
  url: string;
  max_issues?: number;
  force_confirm?: boolean;
}

export interface ManualSubmitResponse {
  job_id: string;
  mode: ManualMode;
  repo_full_name: string;
  issues_enqueued: number;
  issues_reused: number;
  issues_skipped_reason: Record<string, number>;
  needs_confirmation: boolean;
  open_count_total: number | null;
}

export type DecideAction = "ignore" | "start_dev";
export type PRClosedAction = "restart_dev" | "archive";

export interface IssueDetailDevTask {
  id: string;
  attempt_number: number;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  forked_repo: string | null;
  branch_name: string | null;
  branch_pushed: boolean | null;
  use_fallback_provider: boolean;
  files_changed: string[];
  diff_summary: string | null;
  loop_iterations: number;
  total_cost_usd: number;
  failure_reason: string | null;
  failure_detail: string | null;
  test_result: Record<string, unknown> | null;
  review_context: string | null;
}

export interface IssueDetailReviewTask {
  id: string;
  dev_task_id: string | null;
  attempt_number: number;
  status: string;
  verdict: string | null;
  overall_score: number | null;
  dimensions: Record<string, { score: number; passed: boolean; comment: string }> | null;
  rejection_reason: string | null;
  overall_comment: string | null;
  pr_title: string | null;
  pr_body: string | null;
  provider: string | null;
  model: string | null;
  is_fallback: boolean | null;
  cost_usd: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface IssueDetailRejection {
  id: string;
  source: string;
  category: string;
  severity: string;
  dimension: string | null;
  agent_b_attribution: string | null;
  detail: string;
  classified_by: string | null;
  created_at: string;
}

export interface IssueDetail {
  id: string;
  status: string;
  github_url: string;
  github_number: number;
  title: string;
  body: string | null;
  labels: string[];
  author: string | null;
  source: string;
  created_at: string;
  updated_at: string;
  repository: {
    full_name: string;
    primary_language: string | null;
    stars: number;
  };
  evaluation: {
    total_score: number;
    difficulty: string;
    estimated_hours: number;
    summary: string;
    recommendation: string;
    is_worth_developing: boolean;
    dimensions: Record<string, { score: number; comment: string }>;
    model: string | null;
    provider: string | null;
    is_fallback: boolean | null;
    created_at: string;
  } | null;
  dev_tasks: IssueDetailDevTask[];
  review_tasks: IssueDetailReviewTask[];
  pull_request: {
    github_pr_number: number;
    github_pr_url: string;
    title: string;
    base_repo: string;
    base_branch: string;
    head_repo: string;
    head_branch: string;
    status: string;
    final_outcome: string | null;
    submitted_at: string | null;
    merged_at: string | null;
    closed_at: string | null;
    merger_login: string | null;
    closer_login: string | null;
  } | null;
  rejection_reasons: IssueDetailRejection[];
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  open_count_total?: number;
  max_issues?: number;
  repo_full_name?: string;
  retry_after_seconds?: number | null;
}
