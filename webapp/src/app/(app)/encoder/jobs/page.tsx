"use client";

import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { JobsPanel } from "@/components/encoder";

export default function EncoderActiveJobsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="encoder" title="Active jobs" description="Every encode currently queued or running for this bot.">
        {({ botId }) => <JobsPanel botId={botId} activeOnly />}
      </ModuleShell>
    </Suspense>
  );
}
