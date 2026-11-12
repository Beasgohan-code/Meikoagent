"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { BarChart3, Download, Info } from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/lib/api";
import type { Page, Bot } from "@/lib/types";
import { Card, EmptyState, ErrorNote, SectionHeader, Skeleton, Stat } from "@/components/ui";

type Analytics = {
  bots: number;
  series: { date: string; count: number }[];
  moderation_series: { date: string; count: number }[];
  moderation: { by_action: Record<string, number>; total: number; days: number };
  groups: number;
  empty?: boolean;
  note?: string;
};

type Usage = { totals: Record<string, number>; bots: number; clones: number; note: string };

const COLORS = ["#a78bfa", "#818cf8", "#22d3ee", "#f472b6", "#facc15", "#34d399"];

const tooltipStyle = {
  background: "#14161f",
  border: "1px solid rgba(148,163,184,0.15)",
  borderRadius: 12,
  fontSize: 12,
};

export default function AnalyticsPage() {
  const [days, setDays] = useState(7);
  const [botId, setBotId] = useState("");

  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const analytics = useQuery({
    queryKey: ["analytics", days, botId],
    queryFn: () => api.get<Analytics>(`/analytics?days=${days}${botId ? `&bot_id=${botId}` : ""}`),
  });
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.get<Usage>("/usage") });

  const data = analytics.data;
  const actions = Object.entries(data?.moderation.by_action ?? {})
    .map(([action, count]) => ({ action, count }))
    .sort((a, b) => b.count - a.count);
  const chartData = (data?.series ?? []).map((point) => ({
    ...point,
    moderation: data?.moderation_series.find((item) => item.date === point.date)?.count ?? 0,
  }));

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Analytics"
        description="Aggregated from recorded events and moderation rows. Empty ranges mean no data was collected — nothing is estimated."
        action={
          <div className="flex gap-2">
            <select className="input max-w-[160px]" value={botId} onChange={(event) => setBotId(event.target.value)}>
              <option value="">All bots</option>
              {(bots.data?.items ?? []).map((bot) => (
                <option key={bot.id} value={bot.id}>
                  {bot.name}
                </option>
              ))}
            </select>
            <select className="input max-w-[140px]" value={days} onChange={(event) => setDays(Number(event.target.value))}>
              <option value={7}>Last 7 days</option>
              <option value={14}>Last 14 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
            <a
              className="btn-ghost"
              href={`/api/backend/analytics?days=${days}${botId ? `&bot_id=${botId}` : ""}`}
              target="_blank"
              rel="noreferrer"
            >
              <Download className="h-4 w-4" /> JSON
            </a>
          </div>
        }
      />

      {analytics.isError ? <ErrorNote message="Could not load analytics." onRetry={() => analytics.refetch()} /> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Bots in scope" value={data?.bots ?? <Skeleton className="h-7 w-10" />} hint={usage.data ? `${usage.data.clones} clones` : undefined} />
        <Stat label="Active groups" value={data?.groups ?? <Skeleton className="h-7 w-10" />} />
        <Stat
          label={`Moderation (${days}d)`}
          value={data?.moderation.total ?? <Skeleton className="h-7 w-10" />}
          hint="Total actions on record"
        />
        <Stat
          label="Recorded usage"
          value={
            usage.data
              ? Object.values(usage.data.totals).reduce((sum, value) => sum + value, 0).toFixed(0)
              : <Skeleton className="h-7 w-10" />
          }
          hint="Sum of metered platform events"
        />
      </div>

      {data?.empty ? (
        <Card className="flex items-start gap-3 border-indigo-500/20 bg-indigo-500/[0.04]">
          <Info className="mt-0.5 h-4 w-4 text-indigo-300" />
          <p className="text-sm text-indigo-200">{data.note}</p>
        </Card>
      ) : null}

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-white">Events per day</p>
            <p className="text-xs text-slate-500">All recorded activity vs. moderation actions</p>
          </div>
          <BarChart3 className="h-4 w-4 text-violet-300" />
        </div>
        {chartData.length ? (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ left: -18, right: 8, top: 6 }}>
                <defs>
                  <linearGradient id="events" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="mods" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.4} />
                    <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 4" stroke="rgba(148,163,184,0.10)" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltipStyle} />
                <Area type="monotone" dataKey="count" name="events" stroke="#a78bfa" strokeWidth={2} fill="url(#events)" />
                <Area
                  type="monotone"
                  dataKey="moderation"
                  name="moderation"
                  stroke="#22d3ee"
                  strokeWidth={2}
                  fill="url(#mods)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <EmptyState
            title="No events in this window"
            description="Choose a different range or wait for the bot to receive updates."
          />
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <p className="text-sm font-medium text-white">Moderation by action</p>
          <p className="mb-3 text-xs text-slate-500">Window: {data?.moderation.days ?? days} days</p>
          {actions.length ? (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={actions} margin={{ left: -20, right: 8 }}>
                  <CartesianGrid strokeDasharray="3 4" stroke="rgba(148,163,184,0.10)" vertical={false} />
                  <XAxis dataKey="action" tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                    {actions.map((entry, index) => (
                      <Cell key={entry.action} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="No moderation actions" description="Warnings, mutes and bans are recorded as bots act." />
          )}
        </Card>

        <Card>
          <p className="text-sm font-medium text-white">Metered usage</p>
          <p className="mb-3 text-xs text-slate-500">{usage.data?.note ?? "Recorded from real platform events only."}</p>
          {usage.data && Object.keys(usage.data.totals).length ? (
            <ul className="space-y-2">
              {Object.entries(usage.data.totals)
                .sort((a, b) => b[1] - a[1])
                .map(([metric, value]) => (
                  <li key={metric} className="flex items-center justify-between rounded-lg border border-white/[0.05] px-3 py-2">
                    <span className="font-mono text-xs text-slate-300">{metric}</span>
                    <span className="text-sm text-white">{value.toLocaleString()}</span>
                  </li>
                ))}
            </ul>
          ) : (
            <EmptyState
              title="No usage recorded"
              description="Usage rows are written as plugins, workflows and scheduled tasks run."
            />
          )}
        </Card>
      </div>
    </div>
  );
}
