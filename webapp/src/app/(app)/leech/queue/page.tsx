"use client";

import { Suspense } from "react";
import { Badge, Card, Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { LeechShell } from "@/components/leech";

export default function LeechQueuePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="leech" title="Queue" description="Ordered work waiting for a worker slot.">
        {({ botId }) => (
          <LeechShell botId={botId}>
            {(state) => {
              const queued = state.jobs.data!.live.filter((task) => task.state === "queued");
              return (
                <Card className="p-0">
                  {queued.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500">The queue is empty.</p>
                  ) : (
                    <table className="table-base">
                      <thead className="bg-white/[0.02]">
                        <tr><th>#</th><th>Source</th><th>Kind</th><th>Requested by</th></tr>
                      </thead>
                      <tbody>
                        {queued.map((task, index) => (
                          <tr key={task.id}>
                            <td className="text-xs text-slate-500">{index + 1}</td>
                            <td className="max-w-[320px] truncate font-mono text-xs">{task.url}</td>
                            <td><Badge status="info">{task.kind}</Badge></td>
                            <td className="font-mono text-xs">{task.user_id}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Card>
              );
            }}
          </LeechShell>
        )}
      </ModuleShell>
    </Suspense>
  );
}
