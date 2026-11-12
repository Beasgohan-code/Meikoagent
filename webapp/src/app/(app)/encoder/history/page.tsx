"use client";

import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { JobsPanel } from "@/components/encoder";

export default function EncoderHistoryPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell
        slug="encoder"
        title="History"
        description="Finished, failed and cancelled encodes. Failures record the real reason and the command tail."
      >
        {({ botId }) => <JobsPanel botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}
