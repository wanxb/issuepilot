"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";

interface CostItem {
  agent_kind: string | null;
  provider: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  fallback_calls: number;
  failed_calls: number;
}

interface CostStats {
  since: string;
  hours: number;
  items: CostItem[];
  totals: { calls: number; cost_usd: number; failure_rate: number };
}

const HOUR_OPTIONS = [
  { value: 24, label: "近 24h" },
  { value: 168, label: "近 7d" },
  { value: 720, label: "近 30d" },
];

export function CostStatsPanel() {
  const [hours, setHours] = useState(168);
  const [data, setData] = useState<CostStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const resp = await fetch(`/api/v1/admin/cost-stats?hours=${hours}`);
      if (!resp.ok) {
        setError(`HTTP ${resp.status}`);
        return;
      }
      setData(await resp.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "fetch failed");
    }
  }, [hours]);

  useEffect(() => {
    void fetchOnce();
    const id = setInterval(() => void fetchOnce(), 30_000);
    return () => clearInterval(id);
  }, [fetchOnce]);

  if (error) return <p className="text-sm text-destructive">加载失败：{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">加载中...</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 text-sm">
        <div className="flex gap-1">
          {HOUR_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setHours(o.value)}
              className={
                "rounded border px-2 py-1 text-xs " +
                (hours === o.value
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground")
              }
            >
              {o.label}
            </button>
          ))}
        </div>
        <span className="text-muted-foreground">
          总调用 <span className="font-mono text-foreground">{data.totals.calls}</span>
          {" · "}总成本 <span className="font-mono text-foreground">${data.totals.cost_usd.toFixed(4)}</span>
          {" · "}失败率{" "}
          <span className="font-mono text-foreground">
            {(data.totals.failure_rate * 100).toFixed(1)}%
          </span>
        </span>
      </div>
      {data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">该时间段无 LLM 调用记录。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 text-left">Agent</th>
                <th className="py-2 text-left">Provider</th>
                <th className="py-2 text-left">Model</th>
                <th className="py-2 text-right">调用</th>
                <th className="py-2 text-right">in tok</th>
                <th className="py-2 text-right">out tok</th>
                <th className="py-2 text-right">$ 成本</th>
                <th className="py-2 text-right">兜底</th>
                <th className="py-2 text-right">失败</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it, i) => (
                <tr key={i} className="border-b last:border-b-0">
                  <td className="py-2 font-mono text-xs">{it.agent_kind ?? "-"}</td>
                  <td className="py-2">
                    <Badge variant={it.provider === "anthropic" ? "default" : "secondary"}>
                      {it.provider}
                    </Badge>
                  </td>
                  <td className="py-2 font-mono text-xs">{it.model}</td>
                  <td className="py-2 text-right">{it.calls}</td>
                  <td className="py-2 text-right text-xs">{it.input_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right text-xs">{it.output_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right font-mono">${it.cost_usd.toFixed(4)}</td>
                  <td className="py-2 text-right">{it.fallback_calls || "-"}</td>
                  <td className={
                    "py-2 text-right " +
                    (it.failed_calls > 0 ? "text-destructive" : "")
                  }>
                    {it.failed_calls || "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
