"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { DevLogEntry } from "@/lib/types";

interface DevLogStreamProps {
  devTaskId: string;
}

// 颜色映射：level → Tailwind text class
const LEVEL_COLORS: Record<string, string> = {
  error: "text-red-400",
  warning: "text-yellow-400",
  info: "text-zinc-100",
  debug: "text-zinc-500",
};

// step label 前缀
const STEP_LABELS: Record<string, string> = {
  setup: "[SETUP]",
  analyze: "[ANALYZE]",
  plan: "[PLAN]",
  implement: "[IMPL]",
  test: "[TEST]",
  commit: "[COMMIT]",
  system: "[SYS]",
};

function LogLine({ entry }: { entry: DevLogEntry }) {
  const color = LEVEL_COLORS[entry.level] ?? "text-zinc-300";
  const stepLabel = STEP_LABELS[entry.step] ?? `[${entry.step.toUpperCase()}]`;
  return (
    <div className={`font-mono text-xs leading-relaxed ${color}`}>
      <span className="text-zinc-600 mr-2">{stepLabel}</span>
      <span>{entry.message}</span>
    </div>
  );
}

export function DevLogStream({ devTaskId }: DevLogStreamProps) {
  const [logs, setLogs] = useState<DevLogEntry[]>([]);
  const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "closed">("connecting");
  const bottomRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const appendLogs = useCallback((newEntries: DevLogEntry[]) => {
    setLogs((prev) => {
      // 去重（history 消息中已有的条目跳过）
      const existingIds = new Set(prev.map((e) => e.id));
      const fresh = newEntries.filter((e) => !existingIds.has(e.id));
      return fresh.length ? [...prev, ...fresh] : prev;
    });
  }, []);

  useEffect(() => {
    // 先用 REST 拉取历史（作为 WS 的 fallback）
    api.getDevLogs(devTaskId).then(appendLogs).catch(() => {});

    // WebSocket 连接
    const wsBase =
      process.env.NEXT_PUBLIC_API_URL?.replace(/^http/, "ws") ?? "ws://localhost:8000";
    const ws = new WebSocket(`${wsBase}/ws/dev-tasks/${devTaskId}/logs`);
    wsRef.current = ws;

    ws.onopen = () => setWsStatus("connected");
    ws.onclose = () => setWsStatus("closed");
    ws.onerror = () => setWsStatus("closed");

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(event.data) as
          | { type: "history"; logs: DevLogEntry[] }
          | { type: "log"; data: DevLogEntry };
        if (msg.type === "history") {
          appendLogs(msg.logs);
        } else if (msg.type === "log") {
          appendLogs([msg.data]);
        }
      } catch {
        // ignore malformed
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [devTaskId, appendLogs]);

  // 自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  return (
    <div className="mt-3 rounded border border-zinc-700 bg-zinc-900">
      {/* 标题栏 */}
      <div className="flex items-center justify-between border-b border-zinc-700 px-3 py-1.5 text-xs text-zinc-400">
        <span>开发日志</span>
        <span
          className={
            wsStatus === "connected"
              ? "text-green-400"
              : wsStatus === "connecting"
                ? "text-yellow-400"
                : "text-zinc-600"
          }
        >
          {wsStatus === "connected" ? "● 实时" : wsStatus === "connecting" ? "○ 连接中" : "○ 已断开"}
        </span>
      </div>

      {/* 日志区 */}
      <div className="max-h-64 overflow-y-auto p-3 space-y-0.5">
        {logs.length === 0 ? (
          <p className="font-mono text-xs text-zinc-600">等待日志...</p>
        ) : (
          logs.map((entry) => <LogLine key={entry.id} entry={entry} />)
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
