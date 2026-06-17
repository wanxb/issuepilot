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

export interface IssueListItem {
  id: string;
  status: IssueStatus;
  source: IssueSource;
  github_url: string;
  title: string;
  created_at: string;
  repository: RepoView;
  evaluation: EvaluationView | null;
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

export interface ApiErrorDetail {
  code: string;
  message: string;
  open_count_total?: number;
  max_issues?: number;
  repo_full_name?: string;
  retry_after_seconds?: number | null;
}
