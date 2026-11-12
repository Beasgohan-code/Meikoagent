"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Activity, Cpu, Database, Gauge, HardDrive, RefreshCw, Server } from "lucide-react";
import { api, formatBytes, formatDate, formatDuration, relativeTime } from "@/lib/api";
import type { Bot, Page, RuntimeHealth } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton, Stat } from "@/components/ui";
import { Tabs } from "@/components/patterns";

type Tab = "overview" | "workers" | "logs" | "monitoring";

type WorkerRecord = {
  id: number;
  name: string;
  hostname: string;
  pid: number;
  status: string;
  capacity: number;
  current_load: number;
  started_at: string;
  last_heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  is_local: boolean;
};

type LogEntry = { id: number; bot_id: number | null; kind: string; severity: string; message: string; created_at: string };

type Monitoring = {
  window_hours: number;
  activity_events: number;
  runtime_errors: number;
  running_bots: number;
  total_bots: number;
  worker: string;
  degraded: boolean;
};

type RunningBot = {
  id: number;
  name: string;
  username: string | null;
  status: string;
  is_running: boolean;
  uptime_seconds: number;
  plugins: number;
  last_error: string | null;
};

type DbHealth = {
  health: { status: string; dialect?: string; latency_ms?: number };
  dialect: string;
  row_counts: Record<string, number>;
  migrations: string;
};

export default function InfrastructurePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <InfrastructureContent />
    </Suspense>
  );
}

function InfrastructureContent() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "overview");
  const runtime = useQuery({
    queryKey: ["runtime"],
    queryFn: () => api.get<RuntimeHealth>("/infrastructure/runtime"),
    refetchInterval: 20_000,
  });
  const monitoring = useQuery({
    queryKey: ["monitoring"],
    queryFn: () => api.get<Monitoring>("/infrastructure/monitoring"),
    refetchInterval: 20_000,
  });
  const database = useQuery({ queryKey: ["database"], queryFn: () => api.get<DbHealth>("/infrastructure/database") });
  const running = useQuery({
    queryKey: ["running-bots"],
    queryFn: () => api.get<RunningBot[]>("/infrastructure/bots"),
    refetchInterval: 20_000,
  });

  const data = runtime.data;

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Infrastructure"
        description="Live probes of the API, database, cache, worker fleet and process. No synthetic uptime numbers."
        action={
          <button className="btn-ghost" onClick={() => { void runtime.refetch(); void monitoring.refetch(); }}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </button>
        }
      />

      {runtime.isError ? <ErrorNote message="Runtime probe failed." onRetry={() => runtime.refetch()} /> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="API status"
          value={data?.status ?? (runtime.isLoading ? <Skeleton className="h-7 w-16" /> : "—")}
          hint={data ? `v${data.version} · Bot API ${data.bot_api}` : undefined}
          icon={<Server className="h-4 w-4" />}
        />
        <Stat
          label="Database"
          value={data?.services.database.status ?? "…"}
          hint={data?.services.database.dialect}
          icon={<Database className="h-4 w-4" />}
        />
        <Stat
          label="Cache"
          value={data?.services.cache.status ?? "…"}
          hint={data?.services.cache.backend}
          icon={<Cpu className="h-4 w-4" />}
        />
        <Stat
          label="Workers"
          value={data ? `${data.services.workers.running_bots}/${data.services.workers.capacity}` : "…"}
          hint={data?.services.workers.local}
          icon={<Gauge className="h-4 w-4" />}
        />
      </div>

      <Tabs
        tabs={[
          { id: "overview" as Tab, label: "Overview" },
          { id: "workers" as Tab, label: "Workers" },
          { id: "logs" as Tab, label: "Runtime logs" },
          { id: "monitoring" as Tab, label: "Monitoring" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "overview" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <p className="text-sm font-medium text-white">Service probes</p>
            <div className="mt-3 space-y-2 text-sm">
              <ProbeRow label="API process" status={data?.status ?? "…"} />
              <ProbeRow label="PostgreSQL / SQLite" status={data?.services.database.status ?? "…"} />
              <ProbeRow label="Cache backend" status={data?.services.cache.status ?? "…"} />
              <ProbeRow label="Worker heartbeat" status={data?.services.workers.local ?? "…"} />
              <ProbeRow
                label="Process uptime"
                status={data ? formatDuration(data.services.process.uptime_seconds) : "…"}
              />
              <ProbeRow
                label="Disk free"
                status={data ? formatBytes(data.services.storage.free_bytes) : "…"}
              />
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Probes hit the real dependencies on every refresh; a failing dependency returns{" "}
              <span className="kbd">degraded</span> instead of a fake green light.
            </p>
          </Card>

          <Card>
            <p className="text-sm font-medium text-white">Data directory</p>
            <p className="mt-1 font-mono text-xs text-slate-400">{data?.services.storage.data_dir ?? "—"}</p>
            <div className="mt-3 space-y-2">
              <div className="flex items-center justify-between rounded-lg border border-white/[0.05] px-3 py-2 text-sm">
                <span className="flex items-center gap-2 text-slate-300">
                  <HardDrive className="h-3.5 w-3.5 text-violet-300" /> Storage
                </span>
                <span className="font-mono text-xs text-slate-400">
                  {data ? `${formatBytes(data.services.storage.free_bytes)} free` : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-white/[0.05] px-3 py-2 text-sm">
                <span className="text-slate-300">Migrations</span>
                <span className="font-mono text-xs text-slate-400">{database.data?.migrations ?? "alembic"}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-white/[0.05] px-3 py-2 text-sm">
                <span className="text-slate-300">Dialect</span>
                <span className="font-mono text-xs text-slate-400">{database.data?.dialect ?? "—"}</span>
              </div>
            </div>
            <div className="mt-3">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Row counts</p>
              <ul className="mt-1.5 space-y-1">
                {Object.entries(database.data?.row_counts ?? {}).map(([table, count]) => (
                  <li key={table} className="flex items-center justify-between text-xs">
                    <span className="font-mono text-slate-400">{table}</span>
                    <span className="text-slate-300">{count.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </div>
          </Card>

          <Card className="lg:col-span-2">
            <p className="text-sm font-medium text-white">Running bots</p>
            <p className="mb-3 text-xs text-slate-500">Derived from the live worker manager state, not from the database status column.</p>
            {running.isLoading ? (
              <Skeleton className="h-32" />
            ) : running.data?.length ? (
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>Bot</th>
                      <th>Runtime</th>
                      <th>Uptime</th>
                      <th>Plugins</th>
                      <th>Last error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {running.data.map((bot) => (
                      <tr key={bot.id}>
                        <td className="text-slate-100">{bot.name}</td>
                        <td>
                          <Badge status={bot.is_running ? "running" : "stopped"}>{bot.is_running ? "running" : "stopped"}</Badge>
                        </td>
                        <td className="text-xs">{formatDuration(bot.uptime_seconds)}</td>
                        <td className="text-xs">{bot.plugins}</td>
                        <td className="max-w-[280px] truncate text-xs text-rose-300/80">{bot.last_error ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No bots" description="Connect a bot to start a runtime." />
            )}
          </Card>
        </div>
      ) : null}

      {tab === "workers" ? <WorkersPanel /> : null}
      {tab === "logs" ? <LogsPanel /> : null}

      {tab === "monitoring" ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label={`Activity events (${monitoring.data?.window_hours ?? 24}h)`} value={monitoring.data?.activity_events ?? "…"} icon={<Activity className="h-4 w-4" />} />
          <Stat label="Runtime errors" value={monitoring.data?.runtime_errors ?? "…"} icon={<Gauge className="h-4 w-4" />} />
          <Stat label="Running bots" value={monitoring.data?.running_bots ?? "…"} hint={`${monitoring.data?.total_bots ?? 0} total`} />
          <Stat label="Health" value={monitoring.data?.degraded ? "degraded" : "ok"} hint={monitoring.data?.worker} />
        </div>
      ) : null}
    </div>
  );
}

function ProbeRow({ label, status }: { label: string; status: string }) {
  const ok = ["ok", "alive", "running", "memory", "sqlite", "postgresql"].some((value) => status.toLowerCase().includes(value));
  return (
    <div className="flex items-center justify-between rounded-lg border border-white/[0.05] px-3 py-2">
      <span className="text-slate-300">{label}</span>
      <span className={`font-mono text-xs ${ok ? "text-emerald-300" : "text-amber-300"}`}>{status}</span>
    </div>
  );
}

function WorkersPanel() {
  const workers = useQuery({
    queryKey: ["workers"],
    queryFn: () => api.get<WorkerRecord[]>("/infrastructure/workers"),
    refetchInterval: 20_000,
  });

  if (workers.isLoading) return <Skeleton className="h-40" />;
  if (!workers.data?.length) {
    return <EmptyState title="No workers registered" description="Start the API or a worker service to register a heartbeat row." />;
  }
  return (
    <Card className="p-0">
      <table className="table-base">
        <thead>
          <tr>
            <th>Worker</th>
            <th>Host / pid</th>
            <th>Status</th>
            <th>Load</th>
            <th>Heartbeat</th>
            <th>Started</th>
          </tr>
        </thead>
        <tbody>
          {workers.data.map((worker) => (
            <tr key={worker.id}>
              <td>
                <p className="text-slate-100">{worker.name}</p>
                {worker.is_local ? <span className="text-[10px] uppercase tracking-wider text-violet-300">this process</span> : null}
              </td>
              <td className="font-mono text-xs">
                {worker.hostname}:{worker.pid}
              </td>
              <td>
                <Badge status={worker.status === "online" ? "running" : worker.status}>{worker.status}</Badge>
              </td>
              <td className="text-xs">
                {worker.current_load}/{worker.capacity}
              </td>
              <td className="text-xs text-slate-400">
                {worker.heartbeat_age_seconds !== null ? `${worker.heartbeat_age_seconds}s ago` : "never"}
              </td>
              <td className="text-xs text-slate-500">{relativeTime(worker.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function LogsPanel() {
  const [kind, setKind] = useState("");
  const logs = useQuery({
    queryKey: ["logs", kind],
    queryFn: () => api.get<LogEntry[]>(`/infrastructure/logs?limit=200${kind ? `&kind=${kind}` : ""}`),
    refetchInterval: 15_000,
  });

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-white">Runtime events</p>
        <div className="flex items-center gap-2">
          <input className="input max-w-[200px]" placeholder="filter kind (e.g. bot.)" value={kind} onChange={(event) => setKind(event.target.value)} />
          <button className="btn-ghost" onClick={() => logs.refetch()}>
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </button>
        </div>
      </div>
      {logs.isLoading ? (
        <Skeleton className="h-40" />
      ) : logs.data?.length ? (
        <ul className="max-h-[520px] space-y-1 overflow-y-auto font-mono text-[11.5px]">
          {logs.data.map((entry) => (
            <li key={entry.id} className="flex flex-wrap gap-2 rounded-lg px-2 py-1 hover:bg-white/[0.03]">
              <span className="text-slate-600">{formatDate(entry.created_at)}</span>
              <span
                className={
                  entry.severity === "error" || entry.severity === "critical"
                    ? "text-rose-300"
                    : entry.severity === "warning"
                      ? "text-amber-300"
                      : "text-emerald-300"
                }
              >
                {entry.severity}
              </span>
              <span className="text-violet-300">{entry.kind}</span>
              {entry.bot_id ? <span className="text-slate-500">bot {entry.bot_id}</span> : null}
              <span className="flex-1 text-slate-300">{entry.message}</span>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          title="No runtime events"
          description="Events are written by the worker manager, scheduler and watchdog as they act."
          icon={<Activity className="h-6 w-6" />}
        />
      )}
    </Card>
  );
}
