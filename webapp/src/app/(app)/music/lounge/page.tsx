"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense, useMemo, useState } from "react";
import { Headphones, Radio, Users } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { ModuleShell, clock } from "@/components/modules";
import { formatDuration } from "@/lib/api";
import { music } from "@/lib/modules";

export default function MusicLoungePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="music" title="Lounge" description="Who is in the room, what is playing, and where the session has been.">
        {({ botId }) => <Lounge botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Lounge({ botId }: { botId: number }) {
  const sessions = useQuery({ queryKey: ["music-sessions", botId], queryFn: () => music.sessions(botId) });
  const rooms = sessions.data?.rooms ?? [];
  const [chatId, setChatId] = useState<number | null>(null);
  const selected = useMemo(() => rooms.find((row) => row.chat_id === chatId) ?? rooms[0], [rooms, chatId]);
  const detail = useQuery({
    queryKey: ["music-room", botId, selected?.chat_id],
    queryFn: () => music.room(botId, selected!.chat_id),
    enabled: Boolean(selected),
  });

  if (sessions.isLoading) return <Skeleton className="h-64" />;
  if (sessions.error) return <ErrorNote message={(sessions.error as Error).message} />;
  if (rooms.length === 0) {
    return <EmptyState icon={<Radio className="h-6 w-6" />} title="No room is live" description="The lounge fills up when the bot joins a voice chat and someone plays a track." />;
  }

  const listeners = detail.data?.listeners ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {rooms.map((row) => (
          <button
            key={row.chat_id}
            onClick={() => setChatId(row.chat_id)}
            className={`rounded-xl border px-3 py-1.5 text-xs transition ${
              row.chat_id === selected?.chat_id
                ? "border-violet-500/40 bg-violet-500/10 text-violet-100"
                : "border-white/[0.06] bg-white/[0.02] text-slate-300 hover:bg-white/[0.05]"
            }`}
          >
            chat {row.chat_id}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-start gap-3">
            <div className="grid h-16 w-16 place-items-center rounded-xl border border-white/[0.08] bg-gradient-to-br from-violet-600/30 to-indigo-700/20">
              <Headphones className="h-6 w-6 text-violet-200/80" />
            </div>
            <div className="min-w-0">
              <Badge status={selected?.paused ? "warning" : "running"}>{selected?.paused ? "paused" : "live"}</Badge>
              <h2 className="mt-2 truncate text-base font-semibold text-white">{selected?.current?.title ?? "Idle room"}</h2>
              <p className="text-xs text-slate-400">
                {selected?.current?.uploader || "—"} · mode {selected?.mode} · volume {selected?.volume}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                position {clock(selected?.position)} / {clock(selected?.current?.duration)} · queued{" "}
                {selected?.items.length ?? 0} · loop {selected?.loop_count ? `×${selected?.loop_count}` : "off"}
                {selected?.loop_queue ? " · queue repeat" : ""}
                {selected?.autoplay ? " · autoplay" : ""}
              </p>
            </div>
          </div>
        </Card>

        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <Users className="h-4 w-4" /> Listeners ({listeners.length})
          </h3>
          {listeners.length === 0 ? (
            <p className="text-sm text-slate-500">No presence recorded yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {listeners.map((listener) => (
                <li key={listener.user_id} className="flex items-center justify-between text-sm">
                  <span className="min-w-0 truncate text-slate-300">{listener.name || listener.user_id}</span>
                  <span className="text-[11px] text-slate-500">
                    {formatDuration(Math.max(0, Math.floor(Date.now() / 1000) - listener.seen))} ago
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Recently played</h3>
          {(selected?.history ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">Nothing in the room history yet.</p>
          ) : (
            <ol className="space-y-1.5">
              {selected!.history.slice(-12).reverse().map((track, index) => (
                <li key={`${track.source}-${index}`} className="flex items-center justify-between text-sm">
                  <span className="min-w-0 truncate text-slate-300">{track.title}</span>
                  <span className="text-[11px] text-slate-500">{clock(track.duration)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Session notes</h3>
          <ul className="space-y-2 text-xs text-slate-400">
            <li>Presence is recorded when listeners interact with the player; it expires after 15 minutes of silence.</li>
            <li>The queue you see is the bot&apos;s own durable state — a restart does not lose it.</li>
            <li>Playback is real when a voice-chat backend is installed; otherwise the player says so.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
