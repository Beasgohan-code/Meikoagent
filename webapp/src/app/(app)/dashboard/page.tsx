"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Activity,
  ArrowUpRight,
  Bot,
  Boxes,
  Gauge,
  Shield,
  Users,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, formatDate } from "@/lib/api";
import type { ActivityItem, Bot as BotType, Dashboard, RuntimeHealth } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton, Stat } from "@/components/ui";
import { emoji } from "@/lib/premium-emoji";

export default function DashboardPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/dashboard") });
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<{ items: BotType[]; total: number }>("/bots") });
  const activity = useQuery({ queryKey: ["activity"], queryFn: () => api.get<ActivityItem[]>("/activity?limit=8") });
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: () => api.get<RuntimeHealth>("/infrastructure/runtime") });

  const data = dashboard.data;
  const series = data?.activity_series ?? [];

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Dashboard"
        description="Live state of your bots, groups, plugins and infrastructure. Every number is queried from the platform database."
        action={
          <Link href="/bots/new" className="btn-primary">
            Connect a bot
          </Link>
        }
      />

      {dashboard.isError ? <ErrorNote message="Could not load dashboard data." onRetry={() => dashboard.refetch()} /> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Bots"
          value={data ? data.bots.total : <Skeleton className="h-7 w-12" />}
          hint={data ? `${data.bots.running} running · ${data.bots.stopped} stopped` : undefined}
          icon={<Bot className="h-4 w-4" />}
        />
        <Stat
          label="Connected groups"
          value={data ? data.groups : <Skeleton className="h-7 w-12" />}
          hint="Groups the bots have observed"
          icon={<Users className="h-4 w-4" />}
        />
        <Stat
          label="Plugins enabled"
          value={data ? data.plugins_enabled : <Skeleton className="h-7 w-12" />}
          hint="Enabled or running installations"
          icon={<Boxes className="h-4 w-4" />}
        />
        <Stat
          label="Moderation actions"
          value={data ? data.moderation_actions : <Skeleton className="h-7 w-12" />}
          hint="Recorded in the audit journal"
          icon={<Shield className="h-4 w-4" />}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-white">Platform activity</p>
              <p className="text-xs text-slate-500">Events recorded over the last 7 days</p>
            </div>
            <Badge status="info">
              <Zap className="h-3 w-3" /> real data
            </Badge>
          </div>
          {series.length === 0 && !dashboard.isLoading ? (
            <EmptyState
              title="No activity recorded yet"
              description="Activity appears as soon as a bot receives an update or a moderation action is recorded."
              icon={<Activity className="h-6 w-6" />}
            />
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ left: -18, right: 6, top: 6 }}>
                  <defs>
                    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 4" stroke="rgba(148,163,184,0.10)" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      background: "#14161f",
                      border: "1px solid rgba(148,163,184,0.15)",
                      borderRadius: 12,
                      fontSize: 12,
                    }}
                  />
                  <Area type="monotone" dataKey="count" stroke="#a78bfa" strokeWidth={2} fill="url(#fill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card>
          <p className="text-sm font-medium text-white">Runtime health</p>
          <p className="text-xs text-slate-500">Computed from live probes</p>
          <div className="mt-4 space-y-3 text-sm">
            <HealthRow label="API" status={runtime.data?.status ?? "…"} />
            <HealthRow label="Database" status={runtime.data?.services.database.status ?? "…"} />
            <HealthRow label="Cache" status={runtime.data?.services.cache.status ?? "…"} />
            <HealthRow
              label="Workers"
              status={runtime.data ? `${runtime.data.services.workers.running_bots} bots` : "…"}
            />
            <HealthRow
              label="Disk free"
              status={
                runtime.data
                  ? `${Math.round(runtime.data.services.storage.free_bytes / 1024 / 1024 / 1024)} GB`
                  : "…"
              }
            />
            <HealthRow label="Bot API" status={runtime.data?.bot_api ?? "10.4"} />
          </div>
          <Link href="/infrastructure" className="btn-ghost mt-4 w-full">
            Infrastructure <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-white">Recent bots</p>
            <Link href="/bots" className="text-xs text-violet-300 hover:underline">
              View all
            </Link>
          </div>
          {bots.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : bots.data && bots.data.items.length > 0 ? (
            <div className="space-y-2">
              {bots.data.items.slice(0, 5).map((bot) => (
                <Link
                  key={bot.id}
                  href={`/bots/${bot.id}`}
                  className="flex items-center gap-3 rounded-xl border border-white/[0.05] px-3 py-2.5 transition hover:border-violet-500/30 hover:bg-white/[0.03]"
                >
                  <div className="grid h-8 w-8 place-items-center rounded-lg bg-violet-500/15 text-sm">
                    {emoji("robot")}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-100">{bot.name}</p>
                    <p className="truncate text-xs text-slate-500">
                      @{bot.username ?? "unverified"} · {bot.runtime_mode}
                    </p>
                  </div>
                  <Badge status={bot.status}>{bot.status}</Badge>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No bots connected"
              description="Connect an existing bot token or create one from a template."
              action={
                <Link href="/bots/new" className="btn-primary">
                  Connect bot
                </Link>
              }
              icon={<Bot className="h-6 w-6" />}
            />
          )}
        </Card>

        <Card>
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-white">Live activity</p>
            <Link href="/activity" className="text-xs text-violet-300 hover:underline">
              Open feed
            </Link>
          </div>
          {activity.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : activity.data && activity.data.length > 0 ? (
            <ul className="space-y-2">
              {activity.data.map((item) => (
                <li key={item.id} className="flex items-start gap-3 rounded-lg px-1 py-1.5">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-violet-400" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-slate-200">{item.title || item.kind}</p>
                    <p className="text-[11px] text-slate-500">
                      {item.kind} · {formatDate(item.created_at)}
                    </p>
                  </div>
                  <Badge status={item.severity}>{item.severity}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No events yet"
              description="Events stream in from bot updates, moderation actions and runtime changes."
              icon={<Gauge className="h-6 w-6" />}
            />
          )}
        </Card>
      </div>
    </div>
  );
}

function HealthRow({ label, status }: { label: string; status: string }) {
  const ok = ["ok", "running", "10.4"].some((value) => status.includes(value)) || status.includes("bots");
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-400">{label}</span>
      <span className={`font-mono text-xs ${ok ? "text-emerald-300" : "text-slate-300"}`}>{status}</span>
    </div>
  );
}
