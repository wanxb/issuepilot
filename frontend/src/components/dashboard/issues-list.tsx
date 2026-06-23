"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DevLogStream } from "@/components/dashboard/dev-log-stream";
import { ApiError, api } from "@/lib/api";
import type {
  DecideAction,
  IssueListItem,
  IssueListResponse,
  PRClosedAction,
  PRFinalOutcome,
  PullRequestView,
} from "@/lib/types";

const POLL_MS = 5_000;

// 2.4c：按生命周期阶段分组，方便多选过滤
const STATUS_PRESETS: Array<{ label: string; values: string[] }> = [
  { label: "待评估", values: ["DISCOVERED", "ANALYZING"] },
  { label: "待决策", values: ["PENDING_DECISION"] },
  { label: "开发中", values: ["QUEUED_DEV", "IN_DEV", "DEV_TESTING", "DEV_FAILED"] },
  { label: "评审中", values: ["QUEUED_REVIEW", "IN_REVIEW", "REVIEW_REJECTED"] },
  { label: "PR 中", values: ["PR_SUBMITTED"] },
  { label: "已完成", values: ["PR_MERGED", "PR_CLOSED", "ARCHIVED", "IGNORED"] },
];

const SORT_OPTIONS = [
  { value: "-score", label: "评分 ↓" },
  { value: "score", label: "评分 ↑" },
  { value: "-created_at", label: "新到旧" },
  { value: "created_at", label: "旧到新" },
  { value: "-stars", label: "stars ↓" },
];

interface Filters {
  statuses: string[];
  q: string;
  language: string;
  minScore: string;
  sort: string;
  page: number;
}

const DEFAULT_FILTERS: Filters = {
  statuses: [],
  q: "",
  language: "",
  minScore: "",
  sort: "-score",
  page: 1,
};

// 持久化键：导航到详情页再回来时复用 filters + 滚动位置
const FILTERS_STORAGE_KEY = "issuepilot:issues-list:filters";
const SCROLL_STORAGE_KEY = "issuepilot:issues-list:scrollY";

function loadFiltersFromStorage(): Filters {
  if (typeof window === "undefined") return DEFAULT_FILTERS;
  try {
    const raw = window.sessionStorage.getItem(FILTERS_STORAGE_KEY);
    if (!raw) return DEFAULT_FILTERS;
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_FILTERS, ...parsed };
  } catch {
    return DEFAULT_FILTERS;
  }
}

function saveFiltersToStorage(f: Filters): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(f));
  } catch {
    /* ignore quota / private mode */
  }
}

export function IssuesList({ refreshKey }: { refreshKey?: number }) {
  const [data, setData] = useState<IssueListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // SSR 阶段一律用 DEFAULT_FILTERS 避免 hydration mismatch；mount 后 useEffect 从
  // sessionStorage 回填用户上次的 filters（详情页返回时不丢上下文）。
  const [filters, setFiltersInternal] = useState<Filters>(DEFAULT_FILTERS);
  const filtersHydratedRef = useRef(false);

  // 首次 mount 从 sessionStorage 恢复一次
  useEffect(() => {
    if (filtersHydratedRef.current) return;
    filtersHydratedRef.current = true;
    const saved = loadFiltersFromStorage();
    // 浅比较默认值，避免无意义 setState
    if (JSON.stringify(saved) !== JSON.stringify(DEFAULT_FILTERS)) {
      setFiltersInternal(saved);
    }
  }, []);

  // 包装 setFilters 让每次更改自动落盘
  const setFilters = useCallback(
    (updater: ((prev: Filters) => Filters) | Filters) => {
      setFiltersInternal((prev) => {
        const next = typeof updater === "function" ? updater(prev) : updater;
        saveFiltersToStorage(next);
        return next;
      });
    },
    [],
  );

  const fetchIssues = useCallback(async () => {
    try {
      const resp = await api.listIssues({
        page: filters.page,
        page_size: 20,
        status: filters.statuses.length > 0 ? filters.statuses : undefined,
        q: filters.q.trim() || undefined,
        language: filters.language.trim() || undefined,
        min_score: filters.minScore ? Number(filters.minScore) : undefined,
        sort: filters.sort,
      });
      setData(resp);
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `[${err.detail.code}] ${err.detail.message}`
          : err instanceof Error
            ? err.message
            : "加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const replaceItem = useCallback((updated: IssueListItem) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((i) => (i.id === updated.id ? updated : i)),
          }
        : prev,
    );
  }, []);

  useEffect(() => {
    void fetchIssues();
    const id = setInterval(() => void fetchIssues(), POLL_MS);
    return () => clearInterval(id);
  }, [fetchIssues, refreshKey]);

  // 从详情页返回时恢复滚动位置（数据 mount 完后再 scroll，避免高度未撑开）
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    if (!data || typeof window === "undefined") return;
    try {
      const raw = window.sessionStorage.getItem(SCROLL_STORAGE_KEY);
      if (raw) {
        const y = parseInt(raw, 10);
        if (!Number.isNaN(y) && y > 0) {
          // 等下一帧让浏览器排完布局
          requestAnimationFrame(() => window.scrollTo(0, y));
        }
        window.sessionStorage.removeItem(SCROLL_STORAGE_KEY);
      }
    } catch {
      /* ignore */
    }
    restoredRef.current = true;
  }, [data]);

  const togglePreset = (values: string[]) => {
    setFilters((f) => {
      const overlap = values.every((v) => f.statuses.includes(v));
      const next = overlap
        ? f.statuses.filter((v) => !values.includes(v))
        : Array.from(new Set([...f.statuses, ...values]));
      return { ...f, statuses: next, page: 1 };
    });
  };

  const hasActiveFilter =
    filters.statuses.length > 0 ||
    !!filters.q ||
    !!filters.language ||
    !!filters.minScore;

  return (
    <div className="space-y-4">
      <FilterBar
        filters={filters}
        setFilters={setFilters}
        onTogglePreset={togglePreset}
        onReset={() => setFilters(DEFAULT_FILTERS)}
        hasActive={hasActiveFilter}
      />
      {loading && !data ? (
        <p className="text-sm text-muted-foreground">加载中...</p>
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : !data || data.total === 0 ? (
        <p className="text-sm text-muted-foreground">
          {hasActiveFilter
            ? "没有匹配的 Issue（点重置看全部）"
            : "还没有 Issue。在上方粘贴一个 GitHub URL 开始。"}
        </p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            共 {data.total} 条 · 第 {data.page} 页 / 每页 {data.page_size}
          </p>
          <ul className="space-y-3">
            {data.items.map((item) => (
              <IssueRow key={item.id} item={item} onDecided={replaceItem} />
            ))}
          </ul>
          {data.total > data.page_size && (
            <div className="flex items-center justify-center gap-3 pt-2 text-sm">
              <Button
                size="sm"
                variant="outline"
                disabled={data.page <= 1}
                onClick={() => setFilters((f) => ({ ...f, page: f.page - 1 }))}
              >
                上一页
              </Button>
              <span className="text-muted-foreground">
                {data.page} / {Math.ceil(data.total / data.page_size)}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={data.page * data.page_size >= data.total}
                onClick={() => setFilters((f) => ({ ...f, page: f.page + 1 }))}
              >
                下一页
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}


function FilterBar({
  filters, setFilters, onTogglePreset, onReset, hasActive,
}: {
  filters: Filters;
  setFilters: (f: (prev: Filters) => Filters) => void;
  onTogglePreset: (values: string[]) => void;
  onReset: () => void;
  hasActive: boolean;
}) {
  return (
    <div className="space-y-2 rounded border border-border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">状态：</span>
        {STATUS_PRESETS.map((p) => {
          const active = p.values.every((v) => filters.statuses.includes(v));
          return (
            <button
              key={p.label}
              type="button"
              onClick={() => onTogglePreset(p.values)}
              className={
                "rounded border px-2 py-1 " +
                (active
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground")
              }
            >
              {p.label}
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <input
          type="text"
          placeholder="标题搜索"
          value={filters.q}
          onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value, page: 1 }))}
          className="rounded border border-border bg-background px-2 py-1 text-xs min-w-[160px]"
        />
        <input
          type="text"
          placeholder="语言（python / go / ...）"
          value={filters.language}
          onChange={(e) => setFilters((f) => ({ ...f, language: e.target.value, page: 1 }))}
          className="rounded border border-border bg-background px-2 py-1 text-xs min-w-[140px]"
        />
        <input
          type="number"
          min={0} max={10} step={0.5}
          placeholder="最低分"
          value={filters.minScore}
          onChange={(e) => setFilters((f) => ({ ...f, minScore: e.target.value, page: 1 }))}
          className="rounded border border-border bg-background px-2 py-1 text-xs w-[88px]"
        />
        <select
          value={filters.sort}
          onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value, page: 1 }))}
          className="rounded border border-border bg-background px-2 py-1 text-xs"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        {hasActive && (
          <Button size="sm" variant="ghost" onClick={onReset}>
            重置
          </Button>
        )}
      </div>
    </div>
  );
}

interface IssueRowProps {
  item: IssueListItem;
  onDecided?: (updated: IssueListItem) => void;
}

function IssueRow({ item, onDecided }: IssueRowProps) {
  const ev = item.evaluation;
  return (
    <li>
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3">
            <CardTitle className="flex-1 leading-snug">
              <Link
                href={`/issues/${item.id}`}
                onClick={() => {
                  // 4.x: 详情页返回时按这个位置 scroll 回来
                  if (typeof window !== "undefined") {
                    try {
                      window.sessionStorage.setItem(
                        "issuepilot:issues-list:scrollY",
                        String(window.scrollY),
                      );
                    } catch {
                      /* ignore */
                    }
                  }
                }}
                className="hover:underline"
              >
                {item.title}
              </Link>
            </CardTitle>
            <StatusBadge status={item.status} />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{item.repository.full_name}</span>
            {item.repository.primary_language && (
              <span>· {item.repository.primary_language}</span>
            )}
            <span>· ★ {item.repository.stars}</span>
            <span>· {item.source}</span>
            <a
              href={item.github_url}
              target="_blank"
              rel="noreferrer"
              className="ml-auto hover:underline"
            >
              GitHub ↗
            </a>
          </div>
        </CardHeader>
        {ev && (
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant={ev.is_worth_developing ? "success" : "warning"}>
                评分 {ev.total_score.toFixed(1)}
              </Badge>
              <Badge variant="outline">{ev.difficulty}</Badge>
              <Badge variant="outline">{ev.estimated_hours.toFixed(1)}h</Badge>
              {ev.is_fallback && (
                <Badge variant="secondary">fallback</Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground">{ev.summary}</p>
            {item.status === "PENDING_DECISION" && (
              <DecideActions item={item} onDecided={onDecided} />
            )}
          </CardContent>
        )}
        {/* 开发进度日志（IN_DEV / DEV_TESTING 状态时展示） */}
        {(item.status === "IN_DEV" || item.status === "DEV_TESTING") &&
          item.active_dev_task_id && (
            <CardContent>
              <DevLogStream devTaskId={item.active_dev_task_id} />
            </CardContent>
          )}
        {/* PR 卡片（PR_SUBMITTED / PR_MERGED / PR_CLOSED 状态时展示） */}
        {item.pull_request && (
          <CardContent>
            <PRPanel pr={item.pull_request} />
            {item.status === "PR_CLOSED" && (
              <PRClosedActions item={item} onDecided={onDecided} />
            )}
          </CardContent>
        )}
      </Card>
    </li>
  );
}

function PRPanel({ pr }: { pr: PullRequestView }) {
  return (
    <div className="space-y-2 rounded border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={prStatusVariant(pr.status, pr.final_outcome)}>
          {pr.final_outcome ?? pr.status}
        </Badge>
        <a
          href={pr.github_pr_url}
          target="_blank"
          rel="noreferrer"
          className="text-sm font-medium hover:underline"
        >
          PR #{pr.github_pr_number}
        </a>
        <span className="text-xs text-muted-foreground">
          · {new Date(pr.submitted_at).toLocaleString()}
        </span>
      </div>
      <p className="line-clamp-2 text-xs text-muted-foreground">{pr.title}</p>
    </div>
  );
}

function prStatusVariant(
  status: PullRequestView["status"],
  outcome: PRFinalOutcome | null,
): "default" | "success" | "warning" | "secondary" | "destructive" | "outline" {
  if (outcome === "MERGED_CLEAN" || outcome === "MERGED_WITH_CHANGES") return "success";
  if (
    outcome === "CLOSED_BY_MAINTAINER" ||
    outcome === "CLOSED_BY_US" ||
    outcome === "REVERTED"
  ) {
    return "destructive";
  }
  if (outcome === "STALE") return "secondary";
  return status === "OPEN" ? "warning" : status === "MERGED" ? "success" : "destructive";
}

function DecideActions({ item, onDecided }: IssueRowProps) {
  const [submitting, setSubmitting] = useState<DecideAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function go(action: DecideAction) {
    setSubmitting(action);
    setError(null);
    try {
      const updated = await api.decide(item.id, action);
      onDecided?.(updated);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `[${err.detail.code}] ${err.detail.message}`
          : err instanceof Error
            ? err.message
            : "操作失败",
      );
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <Button
        size="sm"
        onClick={() => void go("start_dev")}
        disabled={submitting !== null}
      >
        {submitting === "start_dev" ? "提交中..." : "加入开发"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => void go("ignore")}
        disabled={submitting !== null}
      >
        {submitting === "ignore" ? "提交中..." : "忽略"}
      </Button>
      {error && (
        <span className="text-xs text-destructive">{error}</span>
      )}
    </div>
  );
}

function PRClosedActions({ item, onDecided }: IssueRowProps) {
  const [submitting, setSubmitting] = useState<PRClosedAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function go(action: PRClosedAction) {
    setSubmitting(action);
    setError(null);
    try {
      const updated = await api.prClosedDecide(item.id, action);
      onDecided?.(updated);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `[${err.detail.code}] ${err.detail.message}`
          : err instanceof Error
            ? err.message
            : "操作失败",
      );
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
      <span className="text-xs text-muted-foreground">PR 已关闭，下一步：</span>
      <Button
        size="sm"
        variant="outline"
        onClick={() => void go("restart_dev")}
        disabled={submitting !== null}
      >
        {submitting === "restart_dev" ? "提交中..." : "重新开发"}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => void go("archive")}
        disabled={submitting !== null}
      >
        {submitting === "archive" ? "提交中..." : "归档"}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}


function StatusBadge({ status }: { status: string }) {
  const variant: "default" | "success" | "warning" | "secondary" | "destructive" =
    status === "PR_MERGED" ? "success"
    : status === "PR_CLOSED" || status === "DEV_FAILED" ? "destructive"
    : status === "ANALYZING" || status === "IN_DEV" || status === "IN_REVIEW" ? "warning"
    : status === "PENDING_DECISION" ? "default"
    : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}
