"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Badge, Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { ProgressBar, ModuleShell } from "@/components/modules";
import { linkshare } from "@/lib/modules";

export default function LinkshareAnalyticsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="linkshare" title="Analytics" description="Only what the backend can actually observe — claims, refusals and link states.">
        {({ botId }) => <Analytics botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Analytics({ botId }: { botId: number }) {
  const links = useQuery({ queryKey: ["linkshare-links", botId], queryFn: () => linkshare.links(botId) });
  if (links.isLoading) return <Skeleton className="h-64" />;
  if (links.error) return <ErrorNote message={(links.error as Error).message} />;
  const data = links.data!.analytics;
  const total = Math.max(1, data.total);
  const verdicts = Object.entries(data.verdicts ?? {});

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Links" value={data.total} hint={`${data.batches} batches`} />
        <Stat label="Active" value={data.active} hint={`${data.expired} expired`} />
        <Stat label="Claims" value={data.claims} hint="gate passes observed by the bot" />
        <Stat label="Refusals" value={data.refusals} hint="blocked by state or cap" />
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Link states</h3>
        <ul className="space-y-2">
          {[
            ["active", data.active],
            ["expired", data.expired],
            ["revoked", data.revoked],
            ["exhausted", data.exhausted],
          ].map(([label, count]) => (
            <li key={String(label)} className="space-y-1">
              <div className="flex justify-between text-sm">
                <span className="text-slate-300">{label}</span>
                <span className="text-xs text-slate-500">{count}</span>
              </div>
              <ProgressBar percent={(Number(count) / total) * 100} />
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Verdicts</h3>
        {verdicts.length === 0 ? (
          <p className="text-sm text-slate-500">No claims recorded yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {verdicts.map(([verdict, count]) => (
              <Badge key={verdict} status={verdict === "allow" ? "ok" : "warning"}>
                {verdict}: {count}
              </Badge>
            ))}
          </div>
        )}
      </Card>

      <p className="text-[11px] text-slate-500">{data.note}</p>
    </div>
  );
}
