"use client";

import { useState } from "react";

import { CostStatsPanel } from "@/components/dashboard/cost-stats-panel";
import { CrawlJobsLog } from "@/components/dashboard/crawl-jobs-log";
import { HealthPanel } from "@/components/dashboard/health-panel";
import { IssuesList } from "@/components/dashboard/issues-list";
import { PRFailuresPanel } from "@/components/dashboard/pr-failures-panel";
import { StatsOverview } from "@/components/dashboard/stats-overview";
import { UrlInputBar } from "@/components/dashboard/url-input-bar";

export default function HomePage() {
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <main className="container py-10 space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">IssuePilot</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Multi-agent GitHub Issue automation pipeline
        </p>
      </header>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-base font-semibold">总览</h2>
        <StatsOverview />
      </section>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-3 text-base font-semibold">手动评估</h2>
        <p className="mb-4 text-xs text-muted-foreground">
          粘贴 GitHub 仓库地址或 Issue 链接，Agent A 立刻评估，无需等定时抓取。
        </p>
        <UrlInputBar onSubmitted={() => setRefreshKey((k) => k + 1)} />
      </section>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-base font-semibold">Issues</h2>
        <IssuesList refreshKey={refreshKey} />
      </section>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-base font-semibold">抓取日志</h2>
        <CrawlJobsLog />
      </section>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-base font-semibold">PR 失败复盘</h2>
        <PRFailuresPanel />
      </section>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-base font-semibold">LLM 成本</h2>
        <CostStatsPanel />
      </section>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-base font-semibold">基础设施状态</h2>
        <HealthPanel />
      </section>
    </main>
  );
}
