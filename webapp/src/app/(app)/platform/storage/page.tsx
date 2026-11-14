"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Database } from "lucide-react";
import { Badge, Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { ProgressBar } from "@/components/modules";
import { platform } from "@/lib/modules";

export default function PlatformStoragePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <Storage />
    </Suspense>
  );
}

function Storage() {
  const storage = useQuery({ queryKey: ["platform-storage"], queryFn: () => platform.storage() });
  if (storage.isLoading) return <Skeleton className="h-64" />;
  if (storage.error) return <ErrorNote message={(storage.error as Error).message} />;
  const data = storage.data!;
  const bots = Object.entries(data.per_bot);
  const max = Math.max(1, ...bots.map(([, row]) => row.total));

  return (
    <div>
      <div className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight text-white">Storage usage</h1>
        <p className="mt-1 text-sm text-slate-400">
          Reserved bytes per owner, tracked by the shared storage ledger the modules write to.
        </p>
      </div>

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Stat label="Reserved" value={data.platform.total_human} icon={<Database className="h-4 w-4" />} />
        <Stat label="Owners" value={data.platform.owner_count} />
        <Stat label="Bots tracked" value={bots.length} />
      </div>

      <div className="space-y-3">
        {bots.length === 0 ? <Card>No storage has been reserved yet.</Card> : null}
        {bots.map(([botId, row]) => (
          <Card key={botId}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Link2 botId={botId} />
              <span className="text-xs text-slate-400">{row.total_human}</span>
            </div>
            <div className="mt-2"><ProgressBar percent={(row.total / max) * 100} /></div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(row.modules).length === 0 ? (
                <span className="text-[11px] text-slate-500">Nothing reserved.</span>
              ) : (
                Object.entries(row.modules).map(([module, value]) => (
                  <Badge key={module} status="info">{module}: {value.human}</Badge>
                ))
              )}
            </div>
          </Card>
        ))}
      </div>
      <p className="mt-3 text-[11px] text-slate-500">
        Quotas are per owner, not per module: the encoder, FileStore and leech draw from the same
        allowance, so no module can starve the others silently.
      </p>
    </div>
  );
}

function Link2({ botId }: { botId: string }) {
  return <span className="font-mono text-sm text-violet-200">bot {botId}</span>;
}
