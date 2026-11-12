"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Film, Gauge, Layers } from "lucide-react";
import Link from "next/link";
import { Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { EncoderToolsNote, JobsPanel, ProfileForm } from "@/components/encoder";
import { encoder } from "@/lib/modules";

export default function EncoderStudioPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell
        slug="encoder"
        title="Encoding Studio"
        description="Probe, pick tracks, queue an encode with ffmpeg, watch validated progress, collect the output."
        actions={
          <Link href="/encoder/presets" className="btn-ghost">
            <Layers className="h-4 w-4" /> Presets
          </Link>
        }
      >
        {({ botId }) => <Studio botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Studio({ botId }: { botId: number }) {
  const jobs = useQuery({ queryKey: ["encoder-jobs", botId], queryFn: () => encoder.jobs(botId), refetchInterval: 4000 });
  if (jobs.error) return <ErrorNote message={(jobs.error as Error).message} />;
  const stats = jobs.data?.stats;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Active" value={stats?.running ?? 0} hint={`${stats?.queued ?? 0} queued`} icon={<Gauge className="h-4 w-4" />} />
        <Stat label="Completed" value={stats?.done ?? 0} hint={`${stats?.success_rate ?? 0}% success`} icon={<Film className="h-4 w-4" />} />
        <Stat label="Failed" value={stats?.failed ?? 0} hint={`${stats?.cancelled ?? 0} cancelled`} />
        <Stat label="Avg duration" value={`${stats?.avg_seconds ?? 0}s`} hint="per finished job" />
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">The 10-step workflow</h3>
        <ol className="grid gap-2 text-xs text-slate-400 sm:grid-cols-2 lg:grid-cols-5">
          {[
            "Receive the file",
            "Probe streams",
            "Show tracks",
            "Select profile",
            "Queue the job",
            "Live progress",
            "Safe ffmpeg run",
            "Validate output",
            "Deliver to chat",
            "Clean the workspace",
          ].map((step, index) => (
            <li key={step} className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
              <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-violet-500/20 text-[10px] font-semibold text-violet-200">
                {index + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
        <p className="mt-2 text-[11px] text-slate-500">
          Step 8 is not a formality: a reported 100% is never trusted. The runner probes the output and
          refuses to deliver a file with no valid video stream.
        </p>
      </Card>

      <ProfileForm botId={botId} />
      <div>
        <h3 className="mb-2 text-sm font-medium text-slate-200">Active &amp; recent jobs</h3>
        <JobsPanel botId={botId} />
      </div>
      <EncoderToolsNote tools={jobs.data?.tools} />
    </div>
  );
}
