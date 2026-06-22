/**
 * 简易 API client。
 * 走 next.config.mjs rewrites，由 Next 在服务端代理到 api 容器；前端只 fetch
 * 相对路径，避免硬编码 host。
 */
import type {
  ApiErrorDetail,
  DecideAction,
  DevLogEntry,
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
  }): Promise<IssueListResponse> {
    const search = new URLSearchParams();
    if (params?.page) search.set("page", String(params.page));
    if (params?.page_size) search.set("page_size", String(params.page_size));
    if (params?.min_score !== undefined)
      search.set("min_score", String(params.min_score));
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
