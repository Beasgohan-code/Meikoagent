"use client";

import { Suspense } from "react";
import { Badge, Card, Skeleton, Stat } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { EnginePanel, LeechShell, TransferList } from "@/components/leech";
import { formatBytes } from "@/lib/api";

export default function TransferDashboardPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="leech" title="Transfer dashboard" description="Downloads, uploads and the queue of this bot's transfer engine.">
        {({ botId }) => <Dashboard botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Dashboard({ botId }: { botId: number }) {
  return (
    <LeechShell botId={botId}>
      {(state) => {
        const data = state.jobs.data!;
        const active = data.live.filter((task) => task.state === "active" || task.state === "queued");
        const done = data.live.filter((task) => task.state === "done");
        const failed = data.live.filter((task) => task.state === "failed");
        const bytes = done.reduce((total, task) => total + task.downloaded, 0);
        return (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="In flight" value={active.length} hint={data.bot_running ? "bot running" : "bot offline"} />
              <Stat label="Completed" value={done.length} />
              <Stat label="Failed" value={failed.length} />
              <Stat label="Transferred" value={formatBytes(bytes)} hint="bytes actually written" />
            </div>

            {!data.bot_running ? (
              <Card className="border-amber-500/20 bg-amber-500/[0.05]">
                <p className="text-xs text-amber-200/80">
                  The bot is not running, so no live transfers exist right now. History from the shared
                  ledger is still available on the History page.
                </p>
              </Card>
            ) : null}

            <Card>
              <h3 className="mb-3 text-sm font-medium text-slate-200">Active transfers</h3>
              <TransferList
                data={data}
                retry={(id) => state.retry.mutate(id)}
                filter={(task) => task.state === "active" || task.state === "queued"}
                empty="Nothing transferring. Use /leech <url> or /mirror <url> in chat."
              />
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <EnginePanel data={data} />
              <Card>
                <h3 className="mb-3 text-sm font-medium text-slate-200">Ledger</h3>
                <ul className="space-y-1.5 text-xs text-slate-400">
                  <li className="flex justify-between"><span>Recorded jobs</span><Badge status="info">{data.stats.total}</Badge></li>
                  <li className="flex justify-between"><span>Success rate</span><Badge status="ok">{data.stats.success_rate}%</Badge></li>
                  <li className="flex justify-between"><span>Average duration</span><span>{data.stats.avg_seconds}s</span></li>
                </ul>
              </Card>
            </div>
          </div>
        );
      }}
    </LeechShell>
  );
}
