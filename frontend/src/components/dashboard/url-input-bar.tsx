"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, api } from "@/lib/api";
import type { ManualSubmitResponse } from "@/lib/types";

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; result: ManualSubmitResponse }
  | { kind: "error"; message: string; code?: string };

interface Props {
  onSubmitted?: (result: ManualSubmitResponse) => void;
}

export function UrlInputBar({ onSubmitted }: Props) {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  async function submit(forceConfirm: boolean) {
    if (!url.trim()) return;
    setState({ kind: "submitting" });
    try {
      const result = await api.submitManual({
        url: url.trim(),
        max_issues: 50,
        force_confirm: forceConfirm,
      });

      if (result.needs_confirmation) {
        const ok = window.confirm(
          `该仓库当前有 ${result.open_count_total} 个 open issue，超过默认上限 50。\n继续评估前 50 个？`,
        );
        if (ok) {
          await submit(true);
          return;
        }
        setState({ kind: "idle" });
        return;
      }

      setState({ kind: "success", result });
      setUrl("");
      onSubmitted?.(result);
    } catch (err) {
      if (err instanceof ApiError) {
        setState({
          kind: "error",
          code: err.detail.code,
          message: err.detail.message,
        });
      } else {
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "提交失败",
        });
      }
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Input
          placeholder="粘贴 GitHub repo 或 issue URL，例如 https://github.com/owner/repo 或 .../issues/42"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit(false);
          }}
          disabled={state.kind === "submitting"}
        />
        <Button
          onClick={() => void submit(false)}
          disabled={state.kind === "submitting" || !url.trim()}
        >
          {state.kind === "submitting" ? "提交中..." : "评估"}
        </Button>
      </div>

      {state.kind === "error" && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {state.code && (
            <span className="font-mono text-xs">[{state.code}] </span>
          )}
          {state.message}
        </div>
      )}

      {state.kind === "success" && (
        <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          已入队：{state.result.repo_full_name} —— 新增{" "}
          {state.result.issues_enqueued} · 已存在 {state.result.issues_reused}
        </div>
      )}
    </div>
  );
}
