"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Pause, Play, Radio } from "lucide-react";
import { api, formatDate } from "@/lib/api";
import { openActivityStream } from "@/lib/stream";
import type { ActivityItem, Bot, Page } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton } from "@/components/ui";
import { Tabs } from "@/components/patterns";

type Severity = "all" | "info" | "warning" | "error";

export default function ActivityPage() {
  const [live, setLive] = useState(true);
  const [filter, setFilter] = useState<Severity>("all");
  const [botId, setBotId] = useState<string>("");
  const [received, setReceived] = useState(0);
  const [streamStatus, setStreamStatus] = useState<"idle" | "connecting" | "open" | "closed" | "error">("idle");
  const seenRef = useRef<Set<number>>(new Set());

  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const feed = useQuery({
    queryKey: ["activity", botId],
    queryFn: () => api.get<ActivityItem[]>(`/activity?limit=100${botId ? `&bot_id=${botId}` : ""}`),
    refetchInterval: live ? 10_000 : false,
  });

  useEffect(() => {
    if (!live) {
      setStreamStatus("closed");
      return;
    }
    const close = openActivityStream({
      onStatus: setStreamStatus,
      onEvent: (payload) => {
        const id = Number(payload.id);
        if (!Number.isFinite(id) || seenRef.current.has(id)) return;
        seenRef.current.add(id);
        setReceived((count) => count + 1);
        void feed.refetch();
      },
    });
    return close;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, botId]);

  const items = useMemo(() => {
    const list = feed.data ?? [];
    const filtered = filter === "all" ? list : list.filter((item) => item.severity === filter);
    return [...filtered].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    // `received` is included so a streamed event forces a re-render even when
    // the refetched page happens to contain identical rows.
  }, [feed.data, filter, received]);

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Live Activity"
        description="Every moderation action, runtime transition and plugin event, read straight from the database."
        action={
          <button className="btn-ghost" onClick={() => setLive((value) => !value)}>
            {live ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            {live ? "Pause stream" : "Resume stream"}
          </button>
        }
      />

      {feed.isError ? <ErrorNote message="Could not load activity." onRetry={() => feed.refetch()} /> : null}

      <div className="flex flex-wrap items-center gap-3">
        <Tabs
          tabs={[
            { id: "all" as Severity, label: "Everything" },
            { id: "info" as Severity, label: "Info" },
            { id: "warning" as Severity, label: "Warnings" },
            { id: "error" as Severity, label: "Errors" },
          ]}
          value={filter}
          onChange={setFilter}
        />
        <select className="input max-w-[220px]" value={botId} onChange={(event) => setBotId(event.target.value)}>
          <option value="">All bots</option>
          {(bots.data?.items ?? []).map((bot) => (
            <option key={bot.id} value={bot.id}>
              {bot.name}
            </option>
          ))}
        </select>
        <span
          className={
            streamStatus === "open"
              ? "badge border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
              : "badge border-amber-500/25 bg-amber-500/10 text-amber-300"
          }
        >
          <Radio className="h-3 w-3" /> SSE {live ? streamStatus : "paused"}
        </span>
        <span className="text-xs text-slate-500">{received} streamed since load</span>
      </div>

      {feed.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : items.length > 0 ? (
        <Card className="p-0">
          <ul className="divide-y divide-white/[0.05]">
            {items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <Badge status={item.severity}>{item.severity}</Badge>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-100">{item.title || item.kind}</p>
                  <p className="text-[11px] text-slate-500">
                    {item.kind}
                    {item.bot_id ? ` · bot ${item.bot_id}` : ""}
                    {item.group_id ? ` · group ${item.group_id}` : ""}
                  </p>
                </div>
                <span className="font-mono text-[11px] text-slate-500">{formatDate(item.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : (
        <EmptyState
          title="Nothing recorded yet"
          description="Activity is written when Telegram updates arrive, moderation runs, or a runtime changes state. Nothing here is simulated."
          icon={<Activity className="h-6 w-6" />}
        />
      )}
    </div>
  );
}
