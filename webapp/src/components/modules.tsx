"use client";

/**
 * Shared shell for the six media module pages.
 *
 * Responsibilities kept in one place so every module behaves the same way:
 *  - pick the bot being managed (the modules are per-bot, always),
 *  - show whether that bot is *running* and whether the module is *enabled*,
 *  - refuse to render controls for a module that is not enabled (a disabled
 *    module must not look usable), with a link to turn it on,
 *  - consistent loading / empty / error presentation.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, Bot as BotIcon, Power, RefreshCw } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton } from "@/components/ui";
import { useActiveBot, useEffectivePlugins } from "@/lib/modules";
import type { Bot } from "@/lib/types";

export function BotPicker({
  bots,
  value,
  onChange,
}: {
  bots: Bot[];
  value: number | null;
  onChange: (id: number) => void;
}) {
  if (bots.length === 0) return null;
  return (
    <select
      className="input max-w-xs"
      value={value ?? ""}
      onChange={(event) => onChange(Number(event.target.value))}
      aria-label="Bot"
    >
      {bots.map((bot) => (
        <option key={bot.id} value={bot.id}>
          {bot.name} {bot.username ? `(@${bot.username})` : ""} · {bot.status}
        </option>
      ))}
    </select>
  );
}

export function ModuleShell({
  slug,
  title,
  description,
  children,
  actions,
}: {
  slug: string;
  title: string;
  description: string;
  children: (context: { botId: number; running: boolean }) => React.ReactNode;
  actions?: React.ReactNode;
}) {
  const params = useSearchParams();
  const { bots, bot, loading, error } = useActiveBot(params.get("bot"));
  const effective = useEffectivePlugins();

  const botKey = bot ? String(bot.id) : "";
  const state = effective.data?.bots[botKey];
  const plugin = state?.plugins?.[slug];
  const running = state?.running ?? false;
  const enabled = plugin?.enabled ?? false;

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  if (error) return <ErrorNote message={error.message} />;

  if (bots.length === 0) {
    return (
      <EmptyState
        title="No bots yet"
        description="Modules run inside a bot. Create or connect one first, then come back."
        icon={<BotIcon className="h-6 w-6" />}
        action={
          <Link href="/bots/new" className="btn-primary">
            Create a bot
          </Link>
        }
      />
    );
  }

  return (
    <div>
      <SectionHeader
        title={title}
        description={description}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <BotPicker bots={bots} value={bot?.id ?? null} onChange={(id) => {
              const url = new URL(window.location.href);
              url.searchParams.set("bot", String(id));
              window.location.href = url.toString();
            }} />
            {actions}
          </div>
        }
      />

      {!enabled ? (
        <Card className="mb-4 border-amber-500/25 bg-amber-500/[0.06]">
          <div className="flex flex-wrap items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-100">
                The <span className="font-mono">{slug}</span> module is not enabled for this bot
              </p>
              <p className="mt-1 text-xs text-amber-200/70">
                Its commands are not registered and its background work never starts. Enable it to use
                this dashboard — the status below is the runtime&apos;s own answer, not a placeholder.
              </p>
            </div>
            <Link href={`/plugins?tab=switches&bot=${bot?.id ?? ""}`} className="btn-primary">
              <Power className="h-4 w-4" /> Enable module
            </Link>
          </div>
        </Card>
      ) : null}

      {enabled && !running ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] px-4 py-2.5 text-xs text-slate-400">
          <RefreshCw className="h-3.5 w-3.5" />
          <span>
            Bot is <strong className="text-slate-200">{bot?.status ?? "stopped"}</strong>. Queues and
            sessions are read from what the bot last wrote; actions that need a live process will say so.
          </span>
        </div>
      ) : null}

      {bot ? children({ botId: bot.id, running }) : null}
    </div>
  );
}

export function ModuleStatusRow({
  items,
}: {
  items: { label: string; value: React.ReactNode; status?: string }[];
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <div
          key={item.label}
          className="flex items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-1.5 text-xs text-slate-400"
        >
          <span>{item.label}</span>
          {item.status ? <Badge status={item.status}>{item.value}</Badge> : <span className="font-medium text-slate-200">{item.value}</span>}
        </div>
      ))}
    </div>
  );
}

export function ProgressBar({ percent, tone = "violet" }: { percent: number; tone?: "violet" | "emerald" | "rose" | "amber" }) {
  const width = Math.max(0, Math.min(100, percent));
  const colors: Record<string, string> = {
    violet: "from-violet-500 to-indigo-500",
    emerald: "from-emerald-500 to-teal-500",
    rose: "from-rose-500 to-red-500",
    amber: "from-amber-500 to-orange-500",
  };
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
      <div
        className={`h-full rounded-full bg-gradient-to-r ${colors[tone]} transition-[width] duration-500`}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

export function stateTone(state: string): string {
  switch (state) {
    case "done":
    case "active":
    case "running":
      return "running";
    case "failed":
    case "cancelled":
    case "revoked":
      return "error";
    case "queued":
    case "retrying":
      return "pending";
    case "expired":
    case "exhausted":
      return "warning";
    default:
      return "inactive";
  }
}

export function clock(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${minutes}:${String(secs).padStart(2, "0")}`;
}
