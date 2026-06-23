"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";

interface FailureMode {
  category: string;
  agent_b_attribution: string;
  count: number;
}

interface CostByAgent {
  agent_kind: string | null;
  calls: number;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  fallback_calls: number;
}

interface AttributionRatio {
  count: number;
  ratio: number;
}

interface WeeklyReport {
  since: string;
  days: number;
  top_failure_modes: FailureMode[];
  cost_by_agent: CostByAgent[];
  totals: { calls: number; cost_usd: number };
  attribution_ratio: Record<string, AttributionRatio>;
  issue_funnel: Record<string, number>;
  pr_outcomes: Record<string, number>;
}

const DAYS_OPTIONS = [
  { value: 7, label: "近 7d" },
  { value: 14, label: "近 14d" },
  { value: 30, label: "近 30d" },
];

const FUNNEL_ORDER = [
  "DISCOVERED", "ANALYZING", "PENDING_DECISION",
  "QUEUED_DEV", "IN_DEV", "DEV_TESTING",
  "QUEUED_REVIEW", "IN_REVIEW",
  "PR_SUBMITTED", "PR_MERGED",
];

export function WeeklyReportPanel() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<WeeklyReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const r = await fetch(`/api/v1/dashboard/weekly-report?days=${days}`);
      if (!r.ok) { setError(`HTTP ${r.status}`); return; }
      setData(await r.json());
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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <div className="flex gap-1">
          {DAYS_OPTIONS.map(o => (
            <button
              key={o.value} type="button"
              onClick={() => setDays(o.value)}
              className={
                "rounded border px-2 py-1 text-xs " +
                (days === o.value
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground")
              }
            >{o.label}</button>
          ))}
        </div>
        <span className="text-muted-foreground">
          总调用 <span className="font-mono text-foreground">{data.totals.calls}</span>
          {" · "}总成本 <span className="font-mono text-foreground">${data.totals.cost_usd.toFixed(4)}</span>
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Section title="Top 3 失败模式">
          {data.top_failure_modes.length === 0 ? (
            <Empty msg="无退回信号" />
          ) : (
            <ul className="space-y-2 text-sm">
              {data.top_failure_modes.map((m, i) => (
                <li key={i} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">{m.category}</span>
                    <AttributionBadge attr={m.agent_b_attribution} />
                  </div>
                  <span className="font-mono">{m.count}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Agent B 归因比例">
          {Object.keys(data.attribution_ratio).length === 0 ? (
            <Empty msg="无样本" />
          ) : (
            <ul className="space-y-2 text-sm">
              {Object.entries(data.attribution_ratio).map(([k, v]) => (
                <li key={k} className="space-y-1">
                  <div className="flex items-center justify-between">
                    <AttributionBadge attr={k} />
                    <span className="font-mono text-xs">
                      {v.count} ({(v.ratio * 100).toFixed(0)}%)
                    </span>
                  </div>
                  <div className="h-1.5 rounded-full bg-muted">
                    <div
                      className={
                        "h-full rounded-full " +
                        (k === "yes" ? "bg-destructive" : k === "no" ? "bg-emerald-500" : "bg-muted-foreground")
                      }
                      style={{ width: `${v.ratio * 100}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="PR 终态">
          {Object.keys(data.pr_outcomes).length === 0 ? (
            <Empty msg="无 PR 终结事件" />
          ) : (
            <ul className="space-y-2 text-sm">
              {Object.entries(data.pr_outcomes).map(([k, v]) => (
                <li key={k} className="flex items-center justify-between">
                  <span className="font-mono text-xs">{k}</span>
                  <span className="font-mono">{v}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <Section title="按 Agent 类型的成本（总调用 / fallback / cost）">
        {data.cost_by_agent.length === 0 ? (
          <Empty msg="无 LLM 调用" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 text-left">Agent</th>
                <th className="py-2 text-right">调用</th>
                <th className="py-2 text-right">fallback</th>
                <th className="py-2 text-right">tok in/out</th>
                <th className="py-2 text-right">$ 成本</th>
              </tr>
            </thead>
            <tbody>
              {data.cost_by_agent.map((it, i) => (
                <tr key={i} className="border-b last:border-b-0">
                  <td className="py-2 font-mono text-xs">{it.agent_kind ?? "-"}</td>
                  <td className="py-2 text-right">{it.calls}</td>
                  <td className="py-2 text-right">{it.fallback_calls || "-"}</td>
                  <td className="py-2 text-right text-xs">
                    {it.input_tokens.toLocaleString()} / {it.output_tokens.toLocaleString()}
                  </td>
                  <td className="py-2 text-right font-mono">${it.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Issue 漏斗（总量）">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {FUNNEL_ORDER.map(s => {
            const v = data.issue_funnel[s] ?? 0;
            return (
              <div
                key={s}
                className={
                  "rounded border px-2 py-1 " +
                  (v > 0 ? "border-foreground/40" : "border-border text-muted-foreground")
                }
              >
                <span className="font-mono">{s}</span>
                <span className="ml-1 font-mono">{v}</span>
              </div>
            );
          })}
        </div>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-border bg-muted/30 p-3">
      <p className="mb-2 text-xs text-muted-foreground">{title}</p>
      {children}
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return <p className="text-sm text-muted-foreground">{msg}</p>;
}

function AttributionBadge({ attr }: { attr: string }) {
  if (attr === "yes") return <Badge variant="destructive">B 归因</Badge>;
  if (attr === "no") return <Badge variant="secondary">非 B</Badge>;
  return <Badge variant="outline">待定</Badge>;
}
