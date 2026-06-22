"use client";

import { useCallback, useEffect, useState } from "react";

interface StatsData {
  as_of: string;
  by_status: Record<string, number>;
  today_new_issues: number;
  this_week_pr: Record<string, number>;
  this_week_llm: {
    calls: number;
    fallback_calls: number;
    fallback_rate: number;
    failed_calls: number;
    failure_rate: number;
    cost_usd: number;
  };
}

// 状态分组（与 Issue 状态机 + 看板色彩一致）
const STATUS_GROUPS: Array<{ label: string; statuses: string[]; cls: string }> = [
  { label: "待处理", statuses: ["DISCOVERED", "ANALYZING", "PENDING_DECISION"], cls: "text-foreground" },
  { label: "开发中", statuses: ["QUEUED_DEV", "IN_DEV", "DEV_TESTING", "DEV_FAILED"], cls: "text-amber-600 dark:text-amber-500" },
  { label: "评审中", statuses: ["QUEUED_REVIEW", "IN_REVIEW", "REVIEW_REJECTED"], cls: "text-blue-600 dark:text-blue-400" },
  { label: "已提 PR", statuses: ["PR_SUBMITTED"], cls: "text-violet-600 dark:text-violet-400" },
  { label: "成功合并", statuses: ["PR_MERGED"], cls: "text-emerald-600 dark:text-emerald-400" },
  { label: "已关闭/归档", statuses: ["PR_CLOSED", "IGNORED", "ARCHIVED"], cls: "text-muted-foreground" },
];

export function StatsOverview() {
  const [data, setData] = useState<StatsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/dashboard/stats");
      if (!resp.ok) {
        setError(`HTTP ${resp.status}`);
        return;
      }
      setData(await resp.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "fetch failed");
    }
  }, []);

  useEffect(() => {
    void fetchOnce();
    const id = setInterval(() => void fetchOnce(), 15_000);
    return () => clearInterval(id);
  }, [fetchOnce]);

  if (error) return <p className="text-sm text-destructive">加载失败：{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">加载中...</p>;

  const sumGroup = (g: { statuses: string[] }) =>
    g.statuses.reduce((acc, s) => acc + (data.by_status[s] ?? 0), 0);

  const totalIssues = Object.values(data.by_status).reduce((a, b) => a + b, 0);
  const merged = data.this_week_pr["MERGED_CLEAN"] ?? 0;
  const closed = data.this_week_pr["CLOSED_BY_MAINTAINER"] ?? 0;

  return (
    <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
      {STATUS_GROUPS.map((g) => (
        <Card key={g.label} label={g.label} value={sumGroup(g)} valueCls={g.cls} />
      ))}
      <div className="col-span-full grid gap-4 sm:grid-cols-2 lg:grid-cols-4 border-t pt-4">
        <Card label="今日新增" value={data.today_new_issues} sub={`总 ${totalIssues}`} />
        <Card
          label="本周 PR 合并 / 关闭"
          value={`${merged} / ${closed}`}
          valueCls="font-mono"
        />
        <Card
          label="本周 LLM 调用 / 成本"
          value={`${data.this_week_llm.calls}`}
          sub={`$${data.this_week_llm.cost_usd.toFixed(4)}`}
        />
        <Card
          label="本周 fallback / 失败率"
          value={`${(data.this_week_llm.fallback_rate * 100).toFixed(0)}% / ${(data.this_week_llm.failure_rate * 100).toFixed(0)}%`}
          valueCls={
            data.this_week_llm.failure_rate > 0.3
              ? "text-amber-600 dark:text-amber-500"
              : ""
          }
        />
      </div>
    </div>
  );
}

function Card({
  label, value, sub, valueCls,
}: { label: string; value: string | number; sub?: string; valueCls?: string }) {
  return (
    <div className="rounded border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={"mt-1 text-2xl font-semibold " + (valueCls ?? "")}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}
