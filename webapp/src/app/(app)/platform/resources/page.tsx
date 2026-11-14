"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Cpu, Gauge, Layers } from "lucide-react";
import { Badge, Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { platform } from "@/lib/modules";
import { formatDate } from "@/lib/api";

export default function ResourceMonitoringPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <Resources />
    </Suspense>
  );
}

function Resources() {
  const modules = useQuery({ queryKey: ["platform-modules"], queryFn: () => platform.modules(), refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ["platform-jobs"], queryFn: () => platform.jobs(), refetchInterval: 5000 });
  if (modules.isLoading || jobs.isLoading) return <Skeleton className="h-64" />;
  if (modules.error || jobs.error) return <ErrorNote message={((modules.error || jobs.error) as Error).message} />;
  const data = modules.data!;
  const active = jobs.data!.jobs.filter((job) => job.state === "running" || job.state === "queued");
  const topics = Object.entries(data.events.topics).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-white">Resource monitoring</h1>
        <p className="mt-1 text-sm text-slate-400">Live view of the shared queue, the event hub and module load.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Active jobs" value={active.length} icon={<Gauge className="h-4 w-4" />} />
        <Stat label="Ledger entries" value={jobs.data!.count} icon={<Layers className="h-4 w-4" />} />
        <Stat label="Subscribers" value={data.events.subscribers} icon={<Cpu className="h-4 w-4" />} />
        <Stat label="Buffered events" value={data.events.buffered} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Event topics</h3>
          {topics.length === 0 ? (
            <p className="text-sm text-slate-500">No events published in this process yet.</p>
          ) : (
            <ul className="space-y-1">
              {topics.slice(0, 12).map(([topic, count]) => (
                <li key={topic} className="flex items-center justify-between text-xs">
                  <span className="truncate font-mono text-slate-300">{topic}</span>
                  <Badge status="info">{count}</Badge>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[11px] text-slate-500">
            The hub is process-local: a slow WebSocket client is dropped from the ring buffer rather than
            blocking a bot&apos;s progress update.
          </p>
        </Card>

        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Recent jobs</h3>
          {jobs.data!.jobs.length === 0 ? (
            <p className="text-sm text-slate-500">No jobs recorded.</p>
          ) : (
            <ul className="space-y-1.5">
              {jobs.data!.jobs.slice(0, 10).map((job) => (
                <li key={`${job.module}-${job.id}`} className="flex items-center gap-2 text-xs">
                  <Badge status={job.state === "failed" ? "error" : job.state === "running" ? "ok" : "inactive"}>{job.state}</Badge>
                  <span className="font-mono text-slate-400">{job.module}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-300">{job.kind}</span>
                  <span className="text-slate-500">{formatDate(new Date(job.created_at * 1000).toISOString())}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <p className="text-[11px] text-slate-500">Server time: {new Date(data.server_time * 1000).toLocaleString()}</p>
    </div>
  );
}
