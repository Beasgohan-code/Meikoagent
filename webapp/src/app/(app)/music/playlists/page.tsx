"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { ListMusic, Trash2 } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { ModuleShell, clock } from "@/components/modules";
import { music } from "@/lib/modules";

export default function MusicPlaylistsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="music" title="Playlists" description="Saved queues, owned by you and loadable in any room this bot serves.">
        {({ botId }) => <Playlists botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Playlists({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const playlists = useQuery({ queryKey: ["music-playlists", botId], queryFn: () => music.playlists(botId) });
  const [error, setError] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: (name: string) => music.deletePlaylist(botId, name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["music-playlists", botId] }),
    onError: (err: Error) => setError(err.message),
  });

  if (playlists.isLoading) return <Skeleton className="h-64" />;
  if (playlists.error) return <ErrorNote message={(playlists.error as Error).message} />;
  const rows = Object.values(playlists.data?.playlists ?? {});

  if (rows.length === 0) {
    return <EmptyState icon={<ListMusic className="h-6 w-6" />} title="No playlists" description="Save a room's queue from the Queue page, or use /savequeue in chat." />;
  }

  return (
    <div className="space-y-4">
      {error ? <ErrorNote message={error} /> : null}
      <div className="grid gap-4 md:grid-cols-2">
        {rows.map((playlist) => (
          <Card key={playlist.name}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-medium text-white">{playlist.name}</h3>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  {playlist.tracks.length} tracks ·{" "}
                  {clock(playlist.tracks.reduce((total, track) => total + (track.duration || 0), 0))} ·{" "}
                  {playlist.chat_id ? `from chat ${playlist.chat_id}` : "saved manually"}
                </p>
              </div>
              <button className="btn-danger" onClick={() => remove.mutate(playlist.name)} title="Delete playlist">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
            <ol className="mt-3 space-y-1">
              {playlist.tracks.slice(0, 5).map((track, index) => (
                <li key={`${track.source}-${index}`} className="flex items-center gap-2 text-xs text-slate-400">
                  <span className="w-4 text-right text-slate-600">{index + 1}</span>
                  <span className="min-w-0 flex-1 truncate">{track.title}</span>
                  <span>{clock(track.duration)}</span>
                </li>
              ))}
              {playlist.tracks.length > 5 ? (
                <li className="pl-6 text-[11px] text-slate-500">+{playlist.tracks.length - 5} more</li>
              ) : null}
            </ol>
          </Card>
        ))}
      </div>
      <Badge status="info">{rows.length} playlists</Badge>
    </div>
  );
}
