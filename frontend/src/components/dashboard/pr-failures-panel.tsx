"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";

interface FailureSample {
  rejection_id: string;
  pr_url: string | null;
  pr_number: number | null;
  pr_title: string | null;
  source: string;
  detail_preview: string;
  created_at: string;
}

interface FailureMode {
  category: string;
  agent_b_attribution: string;
  count: number;
  samples: FailureSample[];
}

interface PRFailuresData {
  since: string;
  days: number;
  by_category: Record<string, { total: number; by_attribution: Record<string, number> }>;
  by_dimension: Record<string, number>;
  by_source: Record<string, number>;
  top_failure_modes: FailureMode[];
}

const DAYS_OPTIONS = [
  { value: 7, label: "近 7d" },
  { value: 30, label: "近 30d" },
  { value: 90, label: "近 90d" },
];

function attributionBadge(attr: string) {
  if (attr === "yes") return <Badge variant="destructive">Agent B 归因</Badge>;
  if (attr === "no") return <Badge variant="secondary">非 B 归因</Badge>;
  return <Badge variant="outline">归因待定</Badge>;
}

function sourceLabel(src: string): string {
  return {
    agent_c: "Agent C 评审",
    maintainer_review: "Maintainer review",
    maintainer_close: "Maintainer close",
  }[src] ?? src;
}

export function PRFailuresPanel() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<PRFailuresData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const resp = await fetch(`/api/v1/dashboard/pr-failures?days=${days}`);
      if (!resp.ok) {
        setError(`HTTP ${resp.status}`);
        return;
      }
      setData(await resp.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "fetch failed");
    }
  }, [days]);

  useEffect(() => {
    void fetchOnce();
    const id = setInterval(() => void fetchOnce(), 60_000);
    return () => clearInterval(id);
  }, [fetchOnce]);

  if (error) return <p className="text-sm text-destructive">加载失败：{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">加载中...</p>;

  const totalRejections = data.top_failure_modes.reduce((a, m) => a + m.count, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <div className="flex gap-1">
          {DAYS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setDays(o.value)}
              className={
                "rounded border px-2 py-1 text-xs " +
                (days === o.value
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground")
              }
            >
              {o.label}
            </button>
          ))}
        </div>
        <span className="text-muted-foreground">
          共 <span className="font-mono text-foreground">{totalRejections}</span> 条退回信号
        </span>
      </div>

      {totalRejections === 0 ? (
        <p className="text-sm text-muted-foreground">
          该时间段没有 PR 失败 / 评审退回。可以歇会儿。
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Box title="按来源" rows={data.by_source} formatLabel={sourceLabel} />
            <Box title="按维度" rows={data.by_dimension} />
            <Box
              title="按 category × 归因"
              rows={Object.fromEntries(
                Object.entries(data.by_category).map(([k, v]) => [k, v.total]),
              )}
            />
          </div>

          <h3 className="text-sm font-semibold mt-4">Top 失败模式</h3>
          <div className="space-y-2">
            {data.top_failure_modes.map((m, i) => {
              const key = `${m.category}|${m.agent_b_attribution}`;
              const isOpen = expanded === key;
              return (
                <div key={i} className="rounded border border-border bg-muted/20">
                  <button
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : key)}
                    className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-muted/40"
                  >
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono">{m.category}</span>
                      {attributionBadge(m.agent_b_attribution)}
                    </div>
                    <div className="flex items-center gap-3 text-sm text-muted-foreground">
                      <span className="font-mono text-foreground">{m.count}</span>
                      <span className="text-xs">{isOpen ? "收起" : "展开样本 →"}</span>
                    </div>
                  </button>
                  {isOpen && m.samples.length > 0 && (
                    <div className="border-t border-border bg-background/40 px-3 py-2 space-y-2">
                      {m.samples.map((s) => (
                        <div key={s.rejection_id} className="text-xs">
                          <div className="flex items-center gap-2">
                            {s.pr_url ? (
                              <a
                                href={s.pr_url}
                                target="_blank"
                                rel="noreferrer"
                                className="font-mono hover:underline"
                              >
                                PR #{s.pr_number ?? "?"}
                              </a>
                            ) : (
                              <span className="font-mono text-muted-foreground">(无 PR)</span>
                            )}
                            <Badge variant="outline">{sourceLabel(s.source)}</Badge>
                            <span className="text-muted-foreground">
                              {new Date(s.created_at).toLocaleString()}
                            </span>
                          </div>
                          {s.pr_title && (
                            <p className="mt-0.5 line-clamp-1 text-muted-foreground">
                              {s.pr_title}
                            </p>
                          )}
                          <p className="mt-0.5 text-muted-foreground whitespace-pre-wrap line-clamp-3">
                            {s.detail_preview}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                  {isOpen && m.samples.length === 0 && (
                    <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
                      没有可展示的样本（可能 PR 数据缺失）。
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function Box({
  title, rows, formatLabel,
}: {
  title: string;
  rows: Record<string, number>;
  formatLabel?: (k: string) => string;
}) {
  const entries = Object.entries(rows).sort((a, b) => b[1] - a[1]);
  return (
    <div className="rounded border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted-foreground">{title}</p>
      {entries.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">-</p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm">
          {entries.map(([k, v]) => (
            <li key={k} className="flex items-center justify-between">
              <span className="font-mono text-xs">{formatLabel ? formatLabel(k) : k}</span>
              <span className="font-mono">{v}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
