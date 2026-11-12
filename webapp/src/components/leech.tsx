"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { RotateCw } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { ProgressBar, stateTone } from "@/components/modules";
import { formatBytes } from "@/lib/api";
import { leech, type LeechJobs, type LeechLiveTask } from "@/lib/modules";

export function TransferRow({
  task,
  onRetry,
}: {
  task: LeechLiveTask;
  onRetry?: (id: string) => void;
}) {
  const active = task.state === "active" || task.state === "queued";
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge status={stateTone(task.state)}>{task.state}</Badge>
        <span className="text-sm text-slate-200">{task.kind}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-500">{task.url}</span>
        <span className="text-[11px] text-slate-500">
          {formatBytes(task.downloaded)}
          {task.total ? ` / ${formatBytes(task.total)}` : ""}
        </span>
        {task.speed ? <span className="text-[11px] text-slate-500">{formatBytes(task.speed)}/s</span> : null}
        {!active && onRetry ? (
          <button className="btn-ghost" onClick={() => onRetry(task.id)}>
            <RotateCw className="h-3.5 w-3.5" /> Retry
          </button>
        ) : null}
      </div>
      {active ? <div className="mt-2"><ProgressBar percent={task.percent} /></div> : null}
      {task.error ? <p className="mt-1 text-xs text-rose-300">{task.error}</p> : null}
      {task.destination ? <p className="mt-1 truncate text-[11px] text-slate-500">→ {task.destination}</p> : null}
    </div>
  );
}

export function useLeechJobs(botId: number) {
  const jobs = useQuery({ queryKey: ["leech-jobs", botId], queryFn: () => leech.jobs(botId), refetchInterval: 4000 });
  const queryClient = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  const retry = useMutation({
    mutationFn: (id: string) => leech.retry(botId, id),
    onSuccess: (result) => {
      setNote(result.retried ? null : result.reason ?? "the bot refused the retry");
      void queryClient.invalidateQueries({ queryKey: ["leech-jobs", botId] });
    },
    onError: (err: Error) => setNote(err.message),
  });
  return { jobs, retry, note, setNote };
}

export function TransferList({
  data,
  retry,
  filter,
  empty,
}: {
  data: LeechJobs;
  retry: (id: string) => void;
  filter: (task: LeechLiveTask) => boolean;
  empty: string;
}) {
  const rows = data.live.filter(filter);
  if (rows.length === 0) {
    return <EmptyState title="Nothing here" description={empty} />;
  }
  return (
    <div className="space-y-2">
      {rows.map((task) => <TransferRow key={task.id} task={task} onRetry={retry} />)}
    </div>
  );
}

export function EnginePanel({ data }: { data: LeechJobs }) {
  return (
    <Card>
      <h3 className="mb-3 text-sm font-medium text-slate-200">Capabilities</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <p className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Downloaders</p>
          <ul className="space-y-1">
            {data.engine.downloaders.map((row) => (
              <li key={row.name} className="flex items-center justify-between text-xs">
                <span className="font-mono text-slate-300">{row.name}</span>
                <Badge status={row.available ? "ok" : "disabled"}>{row.available ? "available" : "not configured"}</Badge>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Uploaders</p>
          {data.engine.uploaders.length === 0 ? (
            <p className="text-xs text-slate-500">No uploader is attached to this process; deliveries happen from the bot.</p>
          ) : (
            <ul className="space-y-1">
              {data.engine.uploaders.map((row) => (
                <li key={row.name} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-slate-300">{row.name}</span>
                  <Badge status={row.available ? "ok" : "disabled"}>{row.available ? "available" : "not configured"}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <p className="mt-3 text-[11px] text-slate-500">
        An adapter that is not configured says so instead of failing later. Cloud (rclone) and daemon
        (aria2/qBittorrent) destinations stay unavailable until a real remote is set up.
      </p>
    </Card>
  );
}

export function LeechShell({ botId, children }: { botId: number; children: (state: ReturnType<typeof useLeechJobs>) => React.ReactNode }) {
  const state = useLeechJobs(botId);
  if (state.jobs.isLoading) return <Skeleton className="h-64" />;
  if (state.jobs.error) return <ErrorNote message={(state.jobs.error as Error).message} />;
  return (
    <div className="space-y-3">
      {state.note ? <ErrorNote message={state.note} /> : null}
      {children(state)}
    </div>
  );
}
