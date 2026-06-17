"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

type HealthState =
  | { status: "loading" }
  | { status: "ok"; db: boolean; redis: boolean }
  | { status: "error"; message: string };

const POLL_INTERVAL_MS = 5_000;

async function fetchReadyz(): Promise<HealthState> {
  try {
    const res = await fetch("/readyz", { cache: "no-store" });
    const json = (await res.json()) as { db: boolean; redis: boolean };
    return { status: "ok", db: json.db, redis: json.redis };
  } catch (err) {
    return {
      status: "error",
      message: err instanceof Error ? err.message : "fetch failed",
    };
  }
}

export function HealthPanel() {
  const [state, setState] = useState<HealthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const next = await fetchReadyz();
      if (!cancelled) setState(next);
    };
    void tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (state.status === "loading") {
    return <p className="text-sm text-muted-foreground">检查中...</p>;
  }
  if (state.status === "error") {
    return (
      <p className="text-sm text-destructive">
        无法连接到后端：{state.message}
      </p>
    );
  }

  return (
    <ul className="space-y-2 text-sm">
      <HealthRow label="PostgreSQL" ok={state.db} />
      <HealthRow label="Redis" ok={state.redis} />
    </ul>
  );
}

function HealthRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <li className="flex items-center gap-3">
      <span
        className={cn(
          "inline-block h-2.5 w-2.5 rounded-full",
          ok ? "bg-emerald-500" : "bg-destructive",
        )}
        aria-hidden
      />
      <span className="font-medium">{label}</span>
      <span className="text-muted-foreground">{ok ? "ready" : "down"}</span>
    </li>
  );
}
