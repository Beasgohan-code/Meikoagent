"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useMemo, useState } from "react";
import {
  ListMusic,
  Music2,
  Pause,
  Play,
  Repeat,
  Shuffle,
  SkipBack,
  SkipForward,
  Square,
  Volume2,
} from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton, Stat } from "@/components/ui";
import { ProgressBar, ModuleShell, clock, stateTone } from "@/components/modules";
import { formatDuration } from "@/lib/api";
import { getToken } from "@/lib/api";
import { music, openMusicSocket, type RoomSnapshot } from "@/lib/modules";

const TRANSPORT_ICON: Record<string, React.ElementType> = {
  pause: Pause,
  resume: Play,
  skip: SkipForward,
  previous: SkipBack,
  stop: Square,
  shuffle: Shuffle,
  loop: Repeat,
};

export default function MusicStudioPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell
        slug="music"
        title="Music Studio"
        description="The player, the queue and the room's listener presence — driven by the bot's real session."
      >
        {({ botId, running }) => <Studio botId={botId} running={running} />}
      </ModuleShell>
    </Suspense>
  );
}

function Studio({ botId, running }: { botId: number; running: boolean }) {
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["music-sessions", botId], queryFn: () => music.sessions(botId) });
  const [selected, setSelected] = useState<number | null>(null);
  const [live, setLive] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const rooms = sessions.data?.rooms ?? [];
  const room: RoomSnapshot | undefined = useMemo(
    () => rooms.find((row) => row.chat_id === selected) ?? rooms[0],
    [rooms, selected],
  );

  const transport = useMutation({
    mutationFn: ({ action, value }: { action: string; value?: unknown }) =>
      music.transport(botId, room?.chat_id ?? 0, action, value),
    onSuccess: (result) => {
      if (!result.applied) {
        setNotice(result.reason ?? "the action could not be applied");
      } else {
        setNotice(null);
      }
      void queryClient.invalidateQueries({ queryKey: ["music-sessions", botId] });
    },
    onError: (err: Error) => setNotice(err.message),
  });

  // real-time sync: the WebSocket carries the bot's own room snapshots
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const dispose = openMusicSocket(token, () => {
      setLive(true);
      void queryClient.invalidateQueries({ queryKey: ["music-sessions", botId] });
    });
    return dispose;
  }, [botId, queryClient]);

  if (sessions.isLoading) return <Skeleton className="h-72" />;
  if (sessions.error) return <ErrorNote message={(sessions.error as Error).message} />;

  if (rooms.length === 0) {
    return (
      <EmptyState
        icon={<Music2 className="h-6 w-6" />}
        title="No active room"
        description="Send /play in a group where this bot is a member. The queue appears here as soon as the bot starts a session."
      />
    );
  }

  const analytics = sessions.data!.analytics;
  const current = room?.current ?? null;
  const percent = current && current.duration ? Math.min(100, (room!.position / current.duration) * 100) : 0;

  return (
    <div className="space-y-4">
      {notice ? <ErrorNote message={notice} /> : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-start gap-4">
            <div className="grid h-24 w-24 shrink-0 place-items-center overflow-hidden rounded-xl border border-white/[0.08] bg-gradient-to-br from-violet-600/30 to-indigo-700/20">
              {current?.thumbnail ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={current.thumbnail} alt="" className="h-full w-full object-cover" />
              ) : (
                <Music2 className="h-8 w-8 text-violet-300/70" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <Badge status={room?.paused ? "warning" : "running"}>{room?.paused ? "paused" : "playing"}</Badge>
                <span>chat {room?.chat_id}</span>
                {live ? <Badge status="ok">live</Badge> : null}
                {!running ? <Badge status="stopped">bot offline</Badge> : null}
              </div>
              <h2 className="mt-2 truncate text-lg font-semibold text-white">
                {current?.title || "Nothing playing"}
              </h2>
              <p className="truncate text-sm text-slate-400">
                {current?.uploader || "—"} {current?.requested_by ? `· requested by ${current.requested_by}` : ""}
              </p>
              <div className="mt-3">
                <ProgressBar percent={percent} />
                <div className="mt-1 flex justify-between text-[11px] text-slate-500">
                  <span>{clock(room?.position)}</span>
                  <span>{clock(current?.duration)}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {(Object.keys(TRANSPORT_ICON) as string[]).map((action) => {
              const Icon = TRANSPORT_ICON[action];
              return (
                <button
                  key={action}
                  className={action === "stop" ? "btn-danger" : "btn-ghost"}
                  disabled={transport.isPending}
                  onClick={() => transport.mutate({ action })}
                  title={action}
                >
                  <Icon className="h-4 w-4" />
                  <span className="hidden sm:inline">{action}</span>
                </button>
              );
            })}
            <div className="ml-auto flex items-center gap-2">
              <Volume2 className="h-4 w-4 text-slate-500" />
              <input
                type="range"
                min={0}
                max={200}
                defaultValue={room?.volume ?? 100}
                onMouseUp={(event) => transport.mutate({ action: "volume", value: Number(event.currentTarget.value) })}
                className="w-24 accent-violet-500"
                aria-label="Volume"
              />
            </div>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            Controls call the running bot. When it is offline the backend answers
            <span className="font-mono"> applied: false</span> with the reason instead of faking a pause.
          </p>
        </Card>

        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <ListMusic className="h-4 w-4" /> Rooms
          </h3>
          <div className="space-y-2">
            {rooms.map((row) => (
              <button
                key={row.chat_id}
                onClick={() => setSelected(row.chat_id)}
                className={`flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left text-xs transition ${
                  row.chat_id === room?.chat_id
                    ? "border-violet-500/40 bg-violet-500/10 text-violet-100"
                    : "border-white/[0.06] bg-white/[0.02] text-slate-300 hover:bg-white/[0.05]"
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium">{row.current?.title || "idle"}</span>
                  <span className="block text-[11px] text-slate-500">
                    {row.items.length} queued · {row.paused ? "paused" : "playing"}
                  </span>
                </span>
                <Badge status={stateTone(row.current ? "active" : "inactive")}>{row.chat_id}</Badge>
              </button>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Active rooms" value={analytics.active_rooms} hint={`${analytics.rooms} known`} />
        <Stat label="Plays tracked" value={analytics.plays} hint={formatDuration(analytics.seconds) + " listened"} />
        <Stat label="Playlists" value={analytics.playlists} />
        <Stat label="Listening rooms" value={analytics.listening_rooms} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionHeader title="Up next" description="The queue as the bot holds it right now." />
          {(room?.items ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">Queue is empty.</p>
          ) : (
            <ol className="space-y-1.5">
              {room!.items.slice(0, 12).map((track, index) => (
                <li key={`${track.source}-${index}`} className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-white/[0.03]">
                  <span className="w-5 text-right text-xs text-slate-500">{index + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-200">{track.title}</span>
                  <span className="text-xs text-slate-500">{clock(track.duration)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
        <Card>
          <SectionHeader title="Top tracks" description="Counted from real session changes, not views." />
          {analytics.top_tracks.length === 0 ? (
            <p className="text-sm text-slate-500">No plays recorded yet.</p>
          ) : (
            <ul className="space-y-2">
              {analytics.top_tracks.map((row) => (
                <li key={row.title} className="flex items-center justify-between text-sm">
                  <span className="min-w-0 truncate text-slate-300">{row.title}</span>
                  <Badge status="info">{row.plays}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
