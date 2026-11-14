"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import Link from "next/link";
import { Activity, Boxes, Cpu } from "lucide-react";
import { Badge, Card, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { platform } from "@/lib/modules";

const SECTIONS: Record<string, { label: string; href: string }[]> = {
  music: [
    { label: "Player", href: "/music" },
    { label: "Lounge", href: "/music/lounge" },
    { label: "Queue", href: "/music/queue" },
    { label: "Playlists", href: "/music/playlists" },
    { label: "Rooms", href: "/music/rooms" },
    { label: "Analytics", href: "/music/analytics" },
  ],
  saver: [
    { label: "Backup jobs", href: "/saver" },
    { label: "Media library", href: "/saver/library" },
    { label: "Import / export", href: "/saver/transfer" },
    { label: "Settings", href: "/saver/settings" },
    { label: "History", href: "/saver/history" },
  ],
  encoder: [
    { label: "Studio", href: "/encoder" },
    { label: "Presets", href: "/encoder/presets" },
    { label: "Active jobs", href: "/encoder/jobs" },
    { label: "History", href: "/encoder/history" },
    { label: "Settings", href: "/encoder/settings" },
  ],
  filestore: [
    { label: "My bots", href: "/filestore" },
    { label: "File manager", href: "/filestore/files" },
    { label: "Link generator", href: "/filestore/links" },
    { label: "Storage", href: "/filestore/storage" },
    { label: "Bot settings", href: "/filestore/settings" },
  ],
  linkshare: [
    { label: "Create link", href: "/linkshare" },
    { label: "Batch sharing", href: "/linkshare/batch" },
    { label: "Link manager", href: "/linkshare/manager" },
    { label: "Analytics", href: "/linkshare/analytics" },
  ],
  leech: [
    { label: "Transfer dashboard", href: "/leech" },
    { label: "Downloads", href: "/leech/downloads" },
    { label: "Uploads", href: "/leech/uploads" },
    { label: "Queue", href: "/leech/queue" },
    { label: "Destinations", href: "/leech/destinations" },
    { label: "History", href: "/leech/history" },
  ],
};

export default function ModuleManagementPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <Modules />
    </Suspense>
  );
}

function Modules() {
  const modules = useQuery({ queryKey: ["platform-modules"], queryFn: () => platform.modules() });
  if (modules.isLoading) return <Skeleton className="h-64" />;
  if (modules.error) return <ErrorNote message={(modules.error as Error).message} />;
  const data = modules.data!;
  const rows = Object.entries(data.modules);

  return (
    <div>
      <div className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight text-white">Module management</h1>
        <p className="mt-1 text-sm text-slate-400">
          What each module has persisted, and how much it published to the event hub.
        </p>
      </div>

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Stat label="Modules" value={rows.length} icon={<Boxes className="h-4 w-4" />} />
        <Stat label="Topics" value={Object.keys(data.events.topics).length} icon={<Activity className="h-4 w-4" />} />
        <Stat label="Subscribers" value={data.events.subscribers} icon={<Cpu className="h-4 w-4" />} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {rows.map(([name, row]) => (
          <Card key={name}>
            <div className="flex items-center justify-between">
              <h3 className="font-mono text-sm text-violet-200">{name}</h3>
              <Badge status={row.scopes.length ? "ok" : "inactive"}>{row.scopes.length} scopes</Badge>
            </div>
            <p className="mt-1 text-[11px] text-slate-500">{row.events} events published</p>
            {SECTIONS[name] ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {SECTIONS[name].map((section) => (
                  <Link key={section.href} href={section.href} className="kbd hover:text-violet-200">
                    {section.label}
                  </Link>
                ))}
              </div>
            ) : null}
            {row.scopes.length ? (
              <p className="mt-2 truncate font-mono text-[10px] text-slate-600">{row.scopes.slice(0, 6).join(" · ")}</p>
            ) : null}
          </Card>
        ))}
      </div>

      <Card className="mt-4">
        <h3 className="mb-2 text-sm font-medium text-slate-200">Optional adapters</h3>
        <div className="flex flex-wrap gap-2">
          <Badge status={data.encoder_tools.available ? "ok" : "warning"}>
            ffmpeg: {data.encoder_tools.available ? "ready" : `missing ${data.encoder_tools.missing.join(", ")}`}
          </Badge>
          <Badge status={data.mtproto.available ? "ok" : "disabled"}>
            mtproto: {data.mtproto.available ? data.mtproto.client : "not installed"}
          </Badge>
        </div>
        {data.mtproto.reason ? <p className="mt-2 text-[11px] text-slate-500">{data.mtproto.reason}</p> : null}
      </Card>
    </div>
  );
}
