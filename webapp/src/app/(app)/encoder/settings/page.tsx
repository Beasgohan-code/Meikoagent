"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import Link from "next/link";
import { Badge, Card, ErrorNote, Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { ProfileForm } from "@/components/encoder";
import { encoder } from "@/lib/modules";

export default function EncoderSettingsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="encoder" title="Encoder settings" description="Defaults, limits and the environment this encoder runs in.">
        {({ botId }) => <Settings botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Settings({ botId }: { botId: number }) {
  const jobs = useQuery({ queryKey: ["encoder-jobs", botId], queryFn: () => encoder.jobs(botId) });
  if (jobs.error) return <ErrorNote message={(jobs.error as Error).message} />;
  return (
    <div className="space-y-4">
      <ProfileForm botId={botId} tools={jobs.data?.tools} />
      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Runtime rules</h3>
        <ul className="space-y-2 text-xs text-slate-400">
          <li className="flex items-start gap-2">
            <Badge status={jobs.data?.tools.available ? "ok" : "warning"}>{jobs.data?.tools.available ? "ready" : "missing tools"}</Badge>
            <span>ffmpeg and ffprobe are detected at runtime; a job is refused rather than started blind.</span>
          </li>
          <li className="flex items-start gap-2">
            <Badge status="info">limits</Badge>
            <span>One encode per chat at a time, bounded by file size and duration configured in the plugin.</span>
          </li>
          <li className="flex items-start gap-2">
            <Badge status="info">cleanup</Badge>
            <span>Temporary files live in a per-job workspace with a TTL; the workspace is removed when the job ends.</span>
          </li>
          <li className="flex items-start gap-2">
            <Badge status="info">injection</Badge>
            <span>Arguments are passed as an argv list — never through a shell — and paths are resolved inside the workspace.</span>
          </li>
        </ul>
        <Link href="/encoder/presets" className="btn-ghost mt-3">
          View accepted codecs
        </Link>
      </Card>
    </div>
  );
}
