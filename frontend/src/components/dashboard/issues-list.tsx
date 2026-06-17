"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api } from "@/lib/api";
import type { DecideAction, IssueListItem, IssueListResponse } from "@/lib/types";

const POLL_MS = 5_000;

export function IssuesList({ refreshKey }: { refreshKey?: number }) {
  const [data, setData] = useState<IssueListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchIssues = useCallback(async () => {
    try {
      const resp = await api.listIssues({ page: 1, page_size: 20 });
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
  }, []);

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

  if (loading && !data) {
    return <p className="text-sm text-muted-foreground">加载中...</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data || data.total === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        还没有 Issue。在上方粘贴一个 GitHub URL 开始。
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        共 {data.total} 条 · 第 {data.page} 页 / 每页 {data.page_size}
      </p>
      <ul className="space-y-3">
        {data.items.map((item) => (
          <IssueRow key={item.id} item={item} onDecided={replaceItem} />
        ))}
      </ul>
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
              <a
                href={item.github_url}
                target="_blank"
                rel="noreferrer"
                className="hover:underline"
              >
                {item.title}
              </a>
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
      </Card>
    </li>
  );
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

function StatusBadge({ status }: { status: string }) {
  const variant: "default" | "success" | "warning" | "secondary" | "destructive" =
    status === "PR_MERGED" ? "success"
    : status === "PR_CLOSED" || status === "DEV_FAILED" ? "destructive"
    : status === "ANALYZING" || status === "IN_DEV" || status === "IN_REVIEW" ? "warning"
    : status === "PENDING_DECISION" ? "default"
    : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}
