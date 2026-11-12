"use client";

import { Suspense } from "react";
import { Card, Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { JobRow } from "@/components/encoder";
import { LeechShell } from "@/components/leech";

export default function LeechHistoryPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="leech" title="History" description="Finished, failed and retried transfers from the shared job ledger.">
        {({ botId }) => (
          <LeechShell botId={botId}>
            {(state) => (
              <Card>
                <div className="space-y-2">
                  {state.jobs.data!.jobs.length === 0 ? (
                    <p className="text-sm text-slate-500">No ledger entries for this bot yet.</p>
                  ) : (
                    state.jobs.data!.jobs.map((job) => (
                      <JobRow key={job.id} job={job} onRetry={(id) => state.retry.mutate(id)} />
                    ))
                  )}
                </div>
              </Card>
            )}
          </LeechShell>
        )}
      </ModuleShell>
    </Suspense>
  );
}
