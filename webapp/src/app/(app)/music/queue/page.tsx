"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useMemo, useState } from "react";
import { ListMusic, Save, Trash2 } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Field, Skeleton } from "@/components/ui";
import { ModuleShell, clock } from "@/components/modules";
import { music, type QueueTrack } from "@/lib/modules";

export default function MusicQueuePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="music" title="Queue" description="Per-room queues, with shuffle, loop and playlist snapshots.">
        {({ botId }) => <QueueBoard botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function QueueBoard({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["music-sessions", botId], queryFn: () => music.sessions(botId) });
  const playlists = useQuery({ queryKey: ["music-playlists", botId], queryFn: () => music.playlists(botId) });
  const [name, setName] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const rooms = sessions.data?.rooms ?? [];
  const [roomId, setRoomId] = useState<number | null>(null);
  const room = useMemo(() => rooms.find((row) => row.chat_id === roomId) ?? rooms[0], [rooms, roomId]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["music-sessions", botId] });
    void queryClient.invalidateQueries({ queryKey: ["music-playlists", botId] });
  };

  const action = useMutation({
    mutationFn: ({ act, value }: { act: string; value?: unknown }) => music.transport(botId, room?.chat_id ?? 0, act, value),
    onSuccess: (result) => {
      setMessage(result.applied ? null : result.reason ?? "not applied");
      refresh();
    },
    onError: (err: Error) => setMessage(err.message),
  });

  const save = useMutation({
    mutationFn: () => {
      const tracks: QueueTrack[] = [...(room?.current ? [room.current] : []), ...(room?.items ?? [])];
      return music.savePlaylist(botId, name.trim(), tracks, room?.chat_id ?? 0);
    },
    onSuccess: () => {
      setName("");
      setMessage("Playlist saved from the live queue.");
      refresh();
    },
    onError: (err: Error) => setMessage(err.message),
  });

  const remove = useMutation({
    mutationFn: (playlist: string) => music.deletePlaylist(botId, playlist),
    onSuccess: refresh,
    onError: (err: Error) => setMessage(err.message),
  });

  if (sessions.isLoading) return <Skeleton className="h-64" />;
  if (sessions.error) return <ErrorNote message={(sessions.error as Error).message} />;
  if (rooms.length === 0) {
    return <EmptyState icon={<ListMusic className="h-6 w-6" />} title="No queue yet" description="Start playback in a group, then manage the queue here." />;
  }

  return (
    <div className="space-y-4">
      {message ? <ErrorNote message={message} /> : null}

      <div className="flex flex-wrap items-center gap-2">
        <select className="input max-w-xs" value={room?.chat_id ?? ""} onChange={(event) => setRoomId(Number(event.target.value))}>
          {rooms.map((row) => (
            <option key={row.chat_id} value={row.chat_id}>
              chat {row.chat_id} · {row.current?.title ?? "idle"}
            </option>
          ))}
        </select>
        <button className="btn-ghost" onClick={() => action.mutate({ act: "shuffle" })}>Shuffle</button>
        <button className="btn-ghost" onClick={() => action.mutate({ act: "loop", value: room?.loop_count ? 0 : 3 })}>
          {room?.loop_count ? `Loop ×${room.loop_count}` : "Loop off"}
        </button>
        <button className="btn-ghost" onClick={() => action.mutate({ act: "skip" })}>Skip</button>
        <button className="btn-danger" onClick={() => action.mutate({ act: "stop" })}>Stop room</button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Now playing</h3>
          {room?.current ? (
            <div className="space-y-1 text-sm">
              <p className="truncate font-medium text-white">{room.current.title}</p>
              <p className="text-xs text-slate-400">
                {room.current.uploader} · {clock(room.current.duration)} · {room.paused ? "paused" : "playing"}
              </p>
              <p className="text-[11px] text-slate-500">requested by {room.current.requested_by || "—"}</p>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Nothing playing.</p>
          )}
          <div className="mt-4 border-t border-white/[0.06] pt-3">
            <Field label="Save queue as playlist" hint="Owned by you; group members cannot overwrite it.">
              <div className="flex gap-2">
                <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Late night mix" />
                <button className="btn-primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
                  <Save className="h-4 w-4" /> Save
                </button>
              </div>
            </Field>
          </div>
        </Card>

        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Upcoming ({room?.items.length ?? 0})</h3>
          {(room?.items ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">Nothing queued.</p>
          ) : (
            <ol className="max-h-80 space-y-1 overflow-y-auto pr-1">
              {room!.items.map((track, index) => (
                <li key={`${track.source}-${index}`} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-white/[0.03]">
                  <span className="w-5 text-right text-xs text-slate-500">{index + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-200">{track.title}</span>
                  <span className="text-[11px] text-slate-500">{track.requested_by}</span>
                  <span className="text-xs text-slate-500">{clock(track.duration)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Your playlists</h3>
        {Object.keys(playlists.data?.playlists ?? {}).length === 0 ? (
          <p className="text-sm text-slate-500">No playlists yet.</p>
        ) : (
          <ul className="divide-y divide-white/[0.05]">
            {Object.values(playlists.data!.playlists).map((playlist) => (
              <li key={playlist.name} className="flex items-center justify-between py-2 text-sm">
                <span className="min-w-0">
                  <span className="block truncate text-slate-200">{playlist.name}</span>
                  <span className="text-[11px] text-slate-500">{playlist.tracks.length} tracks</span>
                </span>
                <button className="btn-danger" onClick={() => remove.mutate(playlist.name)} title="Delete">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="flex flex-wrap gap-2 text-[11px] text-slate-500">
        <Badge status="info">{rooms.length} rooms</Badge>
        <span>Every mutation here goes through the same backend the bot uses, so both stay in sync.</span>
      </div>
    </div>
  );
}
