"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Radio } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { ModuleShell, clock, stateTone } from "@/components/modules";
import { music } from "@/lib/modules";

export default function MusicRoomsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="music" title="Rooms" description="Every chat with a session, isolated from the others: separate queue, volume, loop and history.">
        {({ botId }) => <Rooms botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Rooms({ botId }: { botId: number }) {
  const sessions = useQuery({ queryKey: ["music-sessions", botId], queryFn: () => music.sessions(botId) });
  if (sessions.isLoading) return <Skeleton className="h-64" />;
  if (sessions.error) return <ErrorNote message={(sessions.error as Error).message} />;
  const rooms = sessions.data?.rooms ?? [];
  if (rooms.length === 0) {
    return <EmptyState icon={<Radio className="h-6 w-6" />} title="No rooms" description="A room appears when the bot joins a voice chat or starts a queue in a group." />;
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.06]">
      <table className="table-base">
        <thead className="bg-white/[0.02]">
          <tr>
            <th>Chat</th>
            <th>Track</th>
            <th>State</th>
            <th>Queue</th>
            <th>Volume</th>
            <th>Position</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {rooms.map((room) => (
            <tr key={room.chat_id}>
              <td className="font-mono text-xs">{room.chat_id}</td>
              <td className="max-w-[220px] truncate">{room.current?.title ?? "—"}</td>
              <td>
                <Badge status={stateTone(room.current ? (room.paused ? "queued" : "active") : "inactive")}>
                  {room.current ? (room.paused ? "paused" : "playing") : "idle"}
                </Badge>
              </td>
              <td>{room.items.length}</td>
              <td>{room.volume}</td>
              <td>{clock(room.position)}</td>
              <td className="text-xs text-slate-500">
                {room.updated_at ? new Date(room.updated_at * 1000).toLocaleTimeString() : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
