"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Archive, ShieldCheck } from "lucide-react";
import { Badge, Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { JobRow } from "@/components/encoder";
import { saver } from "@/lib/modules";

export default function SaverBackupJobsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="saver" title="Backup jobs" description="Authorized backups of content this bot can already access.">
        {({ botId }) => <Jobs botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Jobs({ botId }: { botId: number }) {
  const overview = useQuery({ queryKey: ["saver-overview", botId], queryFn: () => saver.overview(botId), refetchInterval: 6000 });
  if (overview.isLoading) return <Skeleton className="h-64" />;
  if (overview.error) return <ErrorNote message={(overview.error as Error).message} />;
  const data = overview.data!;
  const config = data.config as Record<string, unknown>;

  return (
    <div className="space-y-4">
      <Card className="border-emerald-500/20 bg-emerald-500/[0.04]">
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
          <div>
            <p className="text-sm font-medium text-emerald-100">Authorized sources only</p>
            <p className="mt-1 text-xs text-emerald-200/70">{data.policy.note}</p>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Library" value={data.library.count} hint={`${data.library.human} referenced`} icon={<Archive className="h-4 w-4" />} />
        <Stat label="Batch size" value={String(config.max_batch ?? "—")} hint="max posts per /batch" />
        <Stat label="Send mode" value={String(config.send_mode ?? "copy")} hint="how saved posts are re-delivered" />
        <Stat label="Jobs recorded" value={data.jobs.length} hint="from the shared job ledger" />
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Recent jobs</h3>
        {data.jobs.length === 0 ? (
          <p className="text-sm text-slate-500">
            No backup jobs recorded yet. Use <span className="kbd">/save &lt;link&gt;</span> or{" "}
            <span className="kbd">/batch</span> in chat; each run is recorded here.
          </p>
        ) : (
          <div className="space-y-2">
            {data.jobs.map((job) => <JobRow key={job.id} job={job} />)}
          </div>
        )}
      </Card>

      <Card>
        <h3 className="mb-2 text-sm font-medium text-slate-200">What this bot refuses</h3>
        <ul className="space-y-1.5 text-xs text-slate-400">
          <li>· Content behind a paywall, a protected chat, or a restriction the bot cannot see with its own permissions.</li>
          <li>· Anything that needs a user account/session to reach — Meiko never uses a logged-in human account to bypass limits.</li>
          <li>· Re-uploading someone else&apos;s work as your own; captions carry the source link when one is known.</li>
        </ul>
        <Badge status="ok" className="mt-3">policy enforced server-side</Badge>
      </Card>
    </div>
  );
}
