"use client";

import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { LeechShell, TransferList } from "@/components/leech";

export default function LeechDownloadsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="leech" title="Downloads" description="Every incoming transfer, newest first, with the backend that handled it.">
        {({ botId }) => (
          <LeechShell botId={botId}>
            {(state) => (
              <TransferList
                data={state.jobs.data!}
                retry={(id) => state.retry.mutate(id)}
                filter={(task) => task.kind !== "mirror"}
                empty="No downloads yet."
              />
            )}
          </LeechShell>
        )}
      </ModuleShell>
    </Suspense>
  );
}
