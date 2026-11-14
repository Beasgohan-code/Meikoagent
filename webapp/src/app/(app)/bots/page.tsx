"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Bot as BotIcon, Play, Plus, RefreshCw, Square, Trash2 } from "lucide-react";
import { api, formatDate } from "@/lib/api";
import type { Bot, Page } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, Modal, SectionHeader, Skeleton } from "@/components/ui";
import { emoji } from "@/lib/premium-emoji";

export default function BotsPage() {
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState<Bot | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["bots"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const start = useMutation({
    mutationFn: (id: number) => api.post<{ running: boolean; status: string; last_error?: string }>(`/bots/${id}/start`),
    onSuccess: (result) => {
      setActionError(result.running ? null : result.last_error ?? "Bot did not start");
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const stop = useMutation({
    mutationFn: (id: number) => api.post(`/bots/${id}/stop`),
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/bots/${id}`),
    onSuccess: () => {
      setConfirmDelete(null);
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  return (
    <div className="space-y-5">
      <SectionHeader
        title="My Bots"
        description="Every bot has its own runtime, plugins, groups, commands and audit trail."
        action={
          <Link href="/bots/new" className="btn-primary">
            <Plus className="h-4 w-4" /> Connect bot
          </Link>
        }
      />

      {actionError ? <ErrorNote message={actionError} onRetry={() => setActionError(null)} /> : null}
      {bots.isError ? <ErrorNote message="Could not load bots." onRetry={() => bots.refetch()} /> : null}

      {bots.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      ) : bots.data && bots.data.items.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {bots.data.items.map((bot) => (
            <Card key={bot.id} hover className="flex flex-col">
              <div className="flex items-start gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-violet-500/25 to-indigo-500/15 text-lg">
                  {emoji("robot")}
                </div>
                <div className="min-w-0 flex-1">
                  <Link href={`/bots/${bot.id}`} className="block truncate text-sm font-semibold text-white hover:text-violet-200">
                    {bot.name}
                  </Link>
                  <p className="truncate text-xs text-slate-500">
                    {bot.username ? `@${bot.username}` : "unverified token"}
                    {bot.is_clone ? " · clone" : ""}
                  </p>
                </div>
                <Badge status={bot.status}>{bot.status}</Badge>
              </div>

              <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg border border-white/[0.05] px-2.5 py-1.5">
                  <dt className="text-slate-500">Mode</dt>
                  <dd className="font-mono text-slate-300">{bot.runtime_mode}</dd>
                </div>
                <div className="rounded-lg border border-white/[0.05] px-2.5 py-1.5">
                  <dt className="text-slate-500">Token</dt>
                  <dd className={bot.token_valid ? "text-emerald-300" : "text-amber-300"}>
                    {bot.token_valid ? "verified" : "unverified"}
                  </dd>
                </div>
              </dl>

              {bot.last_error ? (
                <p className="mt-2 line-clamp-2 text-[11px] text-rose-300/80">{bot.last_error}</p>
              ) : (
                <p className="mt-2 text-[11px] text-slate-600">Started {formatDate(bot.last_started_at)}</p>
              )}

              <div className="mt-4 flex items-center gap-1.5">
                {bot.status === "running" ? (
                  <button className="btn-ghost flex-1" onClick={() => stop.mutate(bot.id)} disabled={stop.isPending}>
                    <Square className="h-3.5 w-3.5" /> Stop
                  </button>
                ) : (
                  <button className="btn-primary flex-1" onClick={() => start.mutate(bot.id)} disabled={start.isPending}>
                    <Play className="h-3.5 w-3.5" /> Start
                  </button>
                )}
                <Link href={`/bots/${bot.id}`} className="btn-ghost" aria-label="Open bot">
                  <RefreshCw className="h-3.5 w-3.5" />
                </Link>
                <button
                  className="btn-danger"
                  onClick={() => setConfirmDelete(bot)}
                  aria-label="Delete bot"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No bots connected yet"
          description="Connect an existing Telegram bot token in seconds, or start from a template with moderation, anti-spam and automation preconfigured."
          action={
            <Link href="/bots/new" className="btn-primary">
              <BotIcon className="h-4 w-4" /> Connect your first bot
            </Link>
          }
          icon={<BotIcon className="h-7 w-7" />}
        />
      )}

      <Modal
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        title="Delete this bot?"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setConfirmDelete(null)}>
              Cancel
            </button>
            <button
              className="btn-danger"
              onClick={() => confirmDelete && remove.mutate(confirmDelete.id)}
              disabled={remove.isPending}
            >
              Delete permanently
            </button>
          </>
        }
      >
        <p className="text-sm text-slate-300">
          Deleting <span className="font-medium text-white">{confirmDelete?.name}</span> stops its runtime and
          removes its configuration, plugins, groups and moderation history. The Telegram bot itself is not
          deleted from BotFather.
        </p>
      </Modal>
    </div>
  );
}
