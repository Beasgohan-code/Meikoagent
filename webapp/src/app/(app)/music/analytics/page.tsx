"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Badge, Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { ProgressBar, ModuleShell } from "@/components/modules";
import { formatDuration } from "@/lib/api";
import { music } from "@/lib/modules";

export default function MusicAnalyticsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="music" title="Analytics" description="Listening statistics derived from real session changes.">
        {({ botId }) => <Analytics botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Analytics({ botId }: { botId: number }) {
  const sessions = useQuery({ queryKey: ["music-sessions", botId], queryFn: () => music.sessions(botId) });
  if (sessions.isLoading) return <Skeleton className="h-64" />;
  if (sessions.error) return <ErrorNote message={(sessions.error as Error).message} />;
  const data = sessions.data!.analytics;
  const max = Math.max(1, ...data.top_tracks.map((row) => row.plays));

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Plays" value={data.plays} hint="counted when the track changes" />
        <Stat label="Listened" value={formatDuration(data.seconds)} hint="sum of track durations played" />
        <Stat label="Rooms" value={data.rooms} hint={`${data.active_rooms} with an active queue`} />
        <Stat label="Playlists" value={data.playlists} />
      </div>
      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Top tracks</h3>
        {data.top_tracks.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing played yet on this bot.</p>
        ) : (
          <ul className="space-y-2">
            {data.top_tracks.map((row) => (
              <li key={row.title} className="space-y-1">
                <div className="flex items-center justify-between text-sm">
                  <span className="min-w-0 truncate text-slate-300">{row.title}</span>
                  <Badge status="info">{row.plays}</Badge>
                </div>
                <ProgressBar percent={(row.plays / max) * 100} />
              </li>
            ))}
          </ul>
        )}
      </Card>
      <p className="text-[11px] text-slate-500">
        These numbers come from the bot&apos;s own session mirror. Meiko does not invent listening time,
        and it does not count a pause, seek or volume change as a play.
      </p>
    </div>
  );
}
