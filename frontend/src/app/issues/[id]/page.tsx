"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, api } from "@/lib/api";
import type {
  DecideAction,
  IssueDetail,
  IssueDetailDevTask,
  IssueDetailRejection,
  IssueDetailReviewTask,
  PRClosedAction,
} from "@/lib/types";

const DEV_STEPS = ["ANALYZE", "PLAN", "IMPLEMENT", "TEST", "COMMIT"];

function devStepIndex(status: string): number {
  // 用 dev_task.status 粗略推断当前 phase；详细日志由 DevLogStream 提供
  return (
    {
      PENDING: 0,
      RUNNING: 2,         // 默认 IMPLEMENT，dev_logs 步骤更精细
      SUCCEEDED: 4,
      FAILED: -1,
      TIMEOUT: -1,
    }[status as keyof Record<string, number>] ?? 0
  );
}

export default function IssueDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;
  const [data, setData] = useState<IssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!id) return;
    try {
      setData(await api.getIssueDetail(id));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `[${err.detail.code}] ${err.detail.message}`
          : err instanceof Error
            ? err.message
            : "加载失败",
      );
    }
  }, [id]);

  useEffect(() => {
    void fetchOnce();
    const t = setInterval(() => void fetchOnce(), 8_000);
    return () => clearInterval(t);
  }, [fetchOnce]);

  const goBack = useCallback(() => {
    // 若有历史记录就回退（保持列表滚动位置 + filters），否则直接去首页
    if (typeof window !== "undefined" && window.history.length > 1) {
      router.back();
    } else {
      router.push("/");
    }
  }, [router]);

  const doDecide = useCallback(
    async (action: DecideAction) => {
      if (!id) return;
      setActing(action);
      setActionError(null);
      try {
        await api.decide(id, action);
        await fetchOnce();
      } catch (err) {
        setActionError(
          err instanceof ApiError
            ? `[${err.detail.code}] ${err.detail.message}`
            : err instanceof Error
              ? err.message
              : "操作失败",
        );
      } finally {
        setActing(null);
      }
    },
    [id, fetchOnce],
  );

  const doPRClosedDecide = useCallback(
    async (action: PRClosedAction) => {
      if (!id) return;
      setActing(action);
      setActionError(null);
      try {
        await api.prClosedDecide(id, action);
        await fetchOnce();
      } catch (err) {
        setActionError(
          err instanceof ApiError
            ? `[${err.detail.code}] ${err.detail.message}`
            : err instanceof Error
              ? err.message
              : "操作失败",
        );
      } finally {
        setActing(null);
      }
    },
    [id, fetchOnce],
  );

  if (error) {
    return <main className="container py-8"><p className="text-destructive">{error}</p></main>;
  }
  if (!data) {
    return <main className="container py-8"><p className="text-muted-foreground">加载中...</p></main>;
  }

  const latestDev = data.dev_tasks[data.dev_tasks.length - 1] ?? null;
  const latestReview = data.review_tasks[data.review_tasks.length - 1] ?? null;

  return (
    <main className="container py-8 space-y-6">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={goBack}
          className="text-sm text-muted-foreground hover:underline"
        >
          ← 返回看板
        </button>
        {/* 详情页操作按钮：按状态显示 */}
        <div className="flex items-center gap-2">
          {data.status === "PENDING_DECISION" && (
            <>
              <Button
                size="sm"
                onClick={() => void doDecide("start_dev")}
                disabled={acting !== null}
              >
                {acting === "start_dev" ? "提交中..." : "加入开发"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void doDecide("ignore")}
                disabled={acting !== null}
              >
                {acting === "ignore" ? "提交中..." : "忽略"}
              </Button>
            </>
          )}
          {data.status === "PR_CLOSED" && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void doPRClosedDecide("restart_dev")}
                disabled={acting !== null}
              >
                {acting === "restart_dev" ? "提交中..." : "重新开发"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void doPRClosedDecide("archive")}
                disabled={acting !== null}
              >
                {acting === "archive" ? "提交中..." : "归档"}
              </Button>
            </>
          )}
        </div>
      </div>
      {actionError && (
        <p className="text-sm text-destructive">{actionError}</p>
      )}

      {/* Header */}
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{data.repository.full_name}</span>
          {data.repository.primary_language && (
            <Badge variant="outline">{data.repository.primary_language}</Badge>
          )}
          <span>★ {data.repository.stars}</span>
          <span>#{data.github_number}</span>
          <Badge variant="default">{data.status}</Badge>
        </div>
        <h1 className="text-2xl font-bold">
          <a href={data.github_url} target="_blank" rel="noreferrer" className="hover:underline">
            {data.title}
          </a>
        </h1>
        {data.labels.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {data.labels.map((l) => <Badge key={l} variant="secondary">{l}</Badge>)}
          </div>
        )}
      </header>

      {/* Evaluation */}
      {data.evaluation && (
        <section className="rounded border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Agent A 评估报告</h2>
            <div className="text-xs text-muted-foreground">
              {data.evaluation.provider} / {data.evaluation.model}
              {data.evaluation.is_fallback && " (fallback)"}
            </div>
          </div>
          <div className="mt-3 grid gap-4 sm:grid-cols-4">
            <Stat label="总分" value={data.evaluation.total_score.toFixed(2)} />
            <Stat label="难度" value={data.evaluation.difficulty} />
            <Stat label="预估耗时" value={`${data.evaluation.estimated_hours}h`} />
            <Stat
              label="是否值得做"
              value={data.evaluation.is_worth_developing ? "是" : "否"}
              valueCls={data.evaluation.is_worth_developing ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-500"}
            />
          </div>
          <div className="mt-3">
            <p className="text-xs text-muted-foreground">摘要</p>
            <p className="text-sm">{data.evaluation.summary}</p>
            <p className="mt-2 text-xs text-muted-foreground">建议</p>
            <p className="text-sm">{data.evaluation.recommendation}</p>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {Object.entries(data.evaluation.dimensions).map(([k, v]) => (
              <div key={k} className="rounded border border-border bg-muted/30 p-2">
                <p className="text-xs text-muted-foreground">{k}</p>
                <p className="font-mono text-sm">{v.score}</p>
                <p className="line-clamp-2 text-xs text-muted-foreground" title={v.comment}>
                  {v.comment}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 开发进度步骤可视化 */}
      {latestDev && (
        <section className="rounded border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">
              开发进度（尝试 #{latestDev.attempt_number}）
            </h2>
            <Badge variant={
              latestDev.status === "SUCCEEDED" ? "success"
              : latestDev.status === "FAILED" || latestDev.status === "TIMEOUT" ? "destructive"
              : latestDev.status === "RUNNING" ? "warning"
              : "secondary"
            }>{latestDev.status}</Badge>
          </div>
          <DevProgress status={latestDev.status} />
        </section>
      )}

      {/* PR 卡片 */}
      {data.pull_request && (
        <section className="rounded border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Pull Request</h2>
            <Badge variant={
              data.pull_request.final_outcome === "MERGED_CLEAN" || data.pull_request.final_outcome === "MERGED_WITH_CHANGES" ? "success"
              : data.pull_request.final_outcome?.startsWith("CLOSED") || data.pull_request.final_outcome === "REVERTED" ? "destructive"
              : "default"
            }>{data.pull_request.final_outcome ?? data.pull_request.status}</Badge>
          </div>
          <div className="mt-2 space-y-1 text-sm">
            <a
              href={data.pull_request.github_pr_url}
              target="_blank"
              rel="noreferrer"
              className="font-medium hover:underline"
            >
              PR #{data.pull_request.github_pr_number}: {data.pull_request.title}
            </a>
            <p className="text-xs text-muted-foreground">
              {data.pull_request.head_repo}:{data.pull_request.head_branch}
              {" → "}
              {data.pull_request.base_repo}:{data.pull_request.base_branch}
            </p>
            {data.pull_request.merged_at && (
              <p className="text-xs text-muted-foreground">
                merged by {data.pull_request.merger_login}{" "}
                @ {new Date(data.pull_request.merged_at).toLocaleString()}
              </p>
            )}
            {data.pull_request.closed_at && !data.pull_request.merged_at && (
              <p className="text-xs text-muted-foreground">
                closed by {data.pull_request.closer_login}{" "}
                @ {new Date(data.pull_request.closed_at).toLocaleString()}
              </p>
            )}
          </div>
        </section>
      )}

      {/* 历次开发 */}
      {data.dev_tasks.length > 0 && (
        <section className="rounded border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">
            开发历史（{data.dev_tasks.length} 次尝试）
          </h2>
          <div className="space-y-2">
            {data.dev_tasks.map((t) => <DevTaskRow key={t.id} t={t} />)}
          </div>
        </section>
      )}

      {/* 历次评审 */}
      {data.review_tasks.length > 0 && (
        <section className="rounded border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">
            评审历史（{data.review_tasks.length} 次评审）
          </h2>
          <div className="space-y-2">
            {data.review_tasks.map((r) => <ReviewTaskRow key={r.id} r={r} />)}
          </div>
        </section>
      )}

      {/* rejection_reasons 摘要 */}
      {data.rejection_reasons.length > 0 && (
        <section className="rounded border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">
            退回信号（{data.rejection_reasons.length} 条）
          </h2>
          <div className="space-y-2">
            {data.rejection_reasons.map((r) => <RejectionRow key={r.id} r={r} />)}
          </div>
        </section>
      )}
    </main>
  );
}

function Stat({ label, value, valueCls }: { label: string; value: string; valueCls?: string }) {
  return (
    <div className="rounded border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={"mt-1 text-xl font-semibold " + (valueCls ?? "")}>{value}</p>
    </div>
  );
}

function DevProgress({ status }: { status: string }) {
  const idx = devStepIndex(status);
  return (
    <div className="mt-3 flex items-center gap-1">
      {DEV_STEPS.map((s, i) => {
        const done = idx >= 0 && i <= idx;
        const failed = idx < 0;
        const active = idx >= 0 && i === idx && status === "RUNNING";
        return (
          <div key={s} className="flex flex-1 flex-col items-center text-xs">
            <div className={
              "h-2 w-full rounded-full " +
              (failed ? "bg-destructive/30"
                : done ? "bg-emerald-500"
                : "bg-muted")
            } />
            <span className={
              "mt-1 " +
              (active ? "font-semibold text-foreground" : "text-muted-foreground")
            }>{s}</span>
          </div>
        );
      })}
    </div>
  );
}

function DevTaskRow({ t }: { t: IssueDetailDevTask }) {
  return (
    <div className="rounded border border-border bg-muted/20 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono">#{t.attempt_number}</span>
        <Badge variant={
          t.status === "SUCCEEDED" ? "success"
          : t.status === "FAILED" || t.status === "TIMEOUT" ? "destructive"
          : t.status === "RUNNING" ? "warning"
          : "secondary"
        }>{t.status}</Badge>
        {t.use_fallback_provider && <Badge variant="outline">fallback provider</Badge>}
        {t.branch_pushed === true && <Badge variant="outline">pushed</Badge>}
        {t.branch_pushed === false && <Badge variant="destructive">push 失败</Badge>}
        <span className="text-muted-foreground">
          loop {t.loop_iterations} · ${t.total_cost_usd.toFixed(4)}
        </span>
        <span className="text-muted-foreground">
          {t.started_at && `started ${new Date(t.started_at).toLocaleString()}`}
        </span>
      </div>
      {t.diff_summary && (
        <p className="mt-2 text-xs">
          <span className="text-muted-foreground">diff: </span>
          {t.diff_summary}
        </p>
      )}
      {t.files_changed.length > 0 && (
        <p className="mt-1 line-clamp-1 font-mono text-xs text-muted-foreground">
          {t.files_changed.join(", ")}
        </p>
      )}
      {t.failure_reason && (
        <p className="mt-1 text-destructive">
          ✗ {t.failure_reason}
          {t.failure_detail && `：${t.failure_detail.slice(0, 200)}`}
        </p>
      )}
    </div>
  );
}

function ReviewTaskRow({ r }: { r: IssueDetailReviewTask }) {
  return (
    <div className="rounded border border-border bg-muted/20 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono">#{r.attempt_number}</span>
        <Badge variant={
          r.verdict === "APPROVED" ? "success"
          : r.verdict === "REJECTED" ? "destructive"
          : r.status === "RUNNING" ? "warning"
          : "secondary"
        }>{r.verdict ?? r.status}</Badge>
        {r.overall_score !== null && (
          <span className="font-mono">score {r.overall_score.toFixed(2)}</span>
        )}
        {r.is_fallback && <Badge variant="outline">fallback</Badge>}
        <span className="text-muted-foreground">
          {r.provider} / {r.model}
          {r.cost_usd && r.cost_usd > 0 && ` · $${r.cost_usd.toFixed(4)}`}
        </span>
      </div>
      {r.dimensions && (
        <div className="mt-2 grid gap-1 sm:grid-cols-5 text-xs">
          {Object.entries(r.dimensions).map(([k, v]) => (
            <div key={k} className={
              "rounded border border-border px-2 py-1 " +
              (v.passed ? "" : "border-destructive/40 bg-destructive/5")
            }>
              <p className="text-muted-foreground">{k}</p>
              <p className="font-mono">{v.score}</p>
            </div>
          ))}
        </div>
      )}
      {r.rejection_reason && (
        <p className="mt-2 whitespace-pre-wrap text-destructive">{r.rejection_reason}</p>
      )}
      {r.overall_comment && (
        <p className="mt-1 text-muted-foreground">{r.overall_comment}</p>
      )}
    </div>
  );
}

function RejectionRow({ r }: { r: IssueDetailRejection }) {
  return (
    <div className="rounded border border-border bg-muted/20 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{r.source}</Badge>
        <span className="font-mono">{r.category}</span>
        <Badge variant={
          r.severity === "blocker" ? "destructive"
          : r.severity === "major" ? "warning"
          : "secondary"
        }>{r.severity}</Badge>
        {r.dimension && <Badge variant="outline">{r.dimension}</Badge>}
        {r.agent_b_attribution === "yes" && <Badge variant="destructive">B 归因</Badge>}
        {r.classified_by === null && (
          <Badge variant="secondary">待分类</Badge>
        )}
        <span className="text-muted-foreground">
          {new Date(r.created_at).toLocaleString()}
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-muted-foreground line-clamp-3">
        {r.detail}
      </p>
    </div>
  );
}
