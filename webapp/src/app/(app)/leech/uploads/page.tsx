"use client";

import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { LeechShell, TransferList } from "@/components/leech";

export default function LeechUploadsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="leech" title="Uploads" description="Mirror jobs: a transfer that lands in a destination instead of the chat.">
        {({ botId }) => (
          <LeechShell botId={botId}>
            {(state) => (
              <TransferList
                data={state.jobs.data!}
                retry={(id) => state.retry.mutate(id)}
                filter={(task) => task.kind === "mirror"}
                empty="No mirror/upload jobs yet. Use /mirror <url>."
              />
            )}
          </LeechShell>
        )}
      </ModuleShell>
    </Suspense>
  );
}
