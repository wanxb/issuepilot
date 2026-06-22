"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";

interface CrawlJobItem {
  id: string;
  trigger: "cron" | "manual_target" | "manual_url";
  status: "pending" | "running" | "succeeded" | "failed" | "partial";
  input_url: string | null;
  started_at: string | null;
  finished_at: string | null;
  stats: Record<string, unknown>;
  created_at: string;
}

const POLL_MS = 8_000;

function statusVariant(
  s: CrawlJobItem["status"],
): "default" | "success" | "warning" | "destructive" | "secondary" {
  if (s === "succeeded") return "success";
  if (s === "failed") return "destructive";
  if (s === "partial") return "warning";
  if (s === "running") return "default";
  return "secondary";
}

export function CrawlJobsLog() {
  const [items, setItems] = useState<CrawlJobItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/crawl-jobs?limit=30");
      if (!resp.ok) {
        setError(`HTTP ${resp.status}`);
        return;
      }
      const data = await resp.json();
      setItems(data.items ?? []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "fetch failed");
    }
  }, []);

  useEffect(() => {
    void fetchOnce();
    const id = setInterval(() => void fetchOnce(), POLL_MS);
    return () => clearInterval(id);
  }, [fetchOnce]);

  if (items === null && error === null) {
    return <p className="text-sm text-muted-foreground">加载中...</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">加载失败：{error}</p>;
  }
  if (!items || items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        没有抓取记录。在 CLI 添加 crawl_target 后会出现定时记录；或粘贴 URL 手动触发。
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr className="border-b">
            <th className="py-2 text-left">触发</th>
            <th className="py-2 text-left">状态</th>
            <th className="py-2 text-left">来源 / URL</th>
            <th className="py-2 text-right">入队</th>
            <th className="py-2 text-right">复用</th>
            <th className="py-2 text-right">耗时</th>
            <th className="py-2 text-left">时间</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => {
            const stats = it.stats as Record<string, unknown>;
            const source = (stats.target_name as string | undefined) ?? it.input_url ?? "-";
            const enqueued = (stats.issues_enqueued as number | undefined) ?? 0;
            const reused = (stats.issues_reused as number | undefined) ?? 0;
            const dur = stats.duration_seconds as number | undefined;
            return (
              <tr key={it.id} className="border-b last:border-b-0">
                <td className="py-2">
                  <span className="font-mono text-xs">{it.trigger}</span>
                </td>
                <td className="py-2">
                  <Badge variant={statusVariant(it.status)}>{it.status}</Badge>
                </td>
                <td className="py-2 max-w-[300px] truncate" title={source}>
                  {source}
                </td>
                <td className="py-2 text-right">{enqueued}</td>
                <td className="py-2 text-right">{reused}</td>
                <td className="py-2 text-right">
                  {dur !== undefined ? `${dur}s` : "-"}
                </td>
                <td className="py-2 text-xs text-muted-foreground">
                  {new Date(it.created_at).toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
