/**
 * 简易 API client。
 * 走 next.config.mjs rewrites，由 Next 在服务端代理到 api 容器；前端只 fetch
 * 相对路径，避免硬编码 host。
 */
import type {
  ApiErrorDetail,
  DecideAction,
  DevLogEntry,
  IssueDetail,
  IssueListItem,
  IssueListResponse,
  ManualSubmitRequest,
  ManualSubmitResponse,
  PRClosedAction,
} from "@/lib/types";

export class ApiError extends Error {
  status: number;
  detail: ApiErrorDetail;

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    let detail: ApiErrorDetail;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      detail = { code: "UNKNOWN", message: res.statusText };
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listIssues(params?: {
    page?: number;
    page_size?: number;
    min_score?: number;
    status?: string[];
    language?: string;
    repo?: string;
    source?: string;
    q?: string;
    sort?: string;
  }): Promise<IssueListResponse> {
    const search = new URLSearchParams();
    if (params?.page) search.set("page", String(params.page));
    if (params?.page_size) search.set("page_size", String(params.page_size));
    if (params?.min_score !== undefined)
      search.set("min_score", String(params.min_score));
    for (const s of params?.status ?? []) search.append("status", s);
    if (params?.language) search.set("language", params.language);
    if (params?.repo) search.set("repo", params.repo);
    if (params?.source) search.set("source", params.source);
    if (params?.q) search.set("q", params.q);
    if (params?.sort) search.set("sort", params.sort);
    const qs = search.toString();
    return request<IssueListResponse>(
      `/api/v1/issues${qs ? `?${qs}` : ""}`,
    );
  },

  submitManual(body: ManualSubmitRequest): Promise<ManualSubmitResponse> {
    return request<ManualSubmitResponse>("/api/v1/crawl-jobs/manual", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getIssueDetail(issueId: string): Promise<IssueDetail> {
    return request<IssueDetail>(`/api/v1/issues/${issueId}/detail`);
  },

  decide(issueId: string, action: DecideAction): Promise<IssueListItem> {
    return request<IssueListItem>(`/api/v1/issues/${issueId}/decide`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
  },

  prClosedDecide(
    issueId: string,
    action: PRClosedAction,
  ): Promise<IssueListItem> {
    return request<IssueListItem>(
      `/api/v1/issues/${issueId}/pr-closed-decide`,
      {
        method: "POST",
        body: JSON.stringify({ action }),
      },
    );
  },

  getDevLogs(devTaskId: string, limit = 200): Promise<DevLogEntry[]> {
    return request<DevLogEntry[]>(
      `/api/v1/dev-tasks/${devTaskId}/logs?limit=${limit}`,
    );
  },
};
