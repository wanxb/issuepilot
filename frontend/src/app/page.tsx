import { HealthPanel } from "@/components/dashboard/health-panel";

export default function HomePage() {
  return (
    <main className="container py-10">
      <header className="mb-10">
        <h1 className="text-3xl font-bold tracking-tight">IssuePilot</h1>
        <p className="mt-2 text-muted-foreground">
          Multi-agent GitHub Issue automation pipeline · 里程碑 1.1 骨架
        </p>
      </header>

      <section className="rounded-lg border bg-card p-6 shadow-sm">
        <h2 className="mb-4 text-lg font-semibold">基础设施状态</h2>
        <HealthPanel />
      </section>
    </main>
  );
}
