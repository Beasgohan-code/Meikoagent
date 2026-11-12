"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import Link from "next/link";
import { Cpu, Power, RefreshCw, Server } from "lucide-react";
import { Badge, Card, ErrorNote, Skeleton, Stat, Toggle } from "@/components/ui";
import { SearchInput } from "@/components/patterns";
import { ModuleStatusRow } from "@/components/modules";
import { effectiveStates, pluginsApi, type PluginToggle } from "@/lib/plugins";

const MEDIA_MODULES = ["music", "saver", "encoder", "filestore", "linkshare", "leech"];

export default function PlatformWorkersPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <Workers />
    </Suspense>
  );
}

function Workers() {
  const params = useSearchParams();
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const effective = useQuery({ queryKey: ["plugins-effective"], queryFn: effectiveStates });
  const toggles = useQuery({ queryKey: ["plugin-toggles"], queryFn: () => pluginsApi.toggles() });

  const toggle = useMutation({
    mutationFn: ({ slug, botId, enabled }: { slug: string; botId: number | null; enabled: boolean }) =>
      pluginsApi.toggle(slug, botId, enabled),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["plugins-effective"] });
      void queryClient.invalidateQueries({ queryKey: ["plugin-toggles"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  if (effective.isLoading || toggles.isLoading) return <Skeleton className="h-64" />;
  if (effective.error) return <ErrorNote message={(effective.error as Error).message} />;

  const bots = Object.entries(effective.data!.bots).filter(([id]) => !search || id.includes(search));
  const enabledCount = bots.reduce((total, [, row]) => total + Object.values(row.plugins).filter((plugin) => plugin.enabled).length, 0);

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Shared workers</h1>
          <p className="mt-1 text-sm text-slate-400">
            One worker manager runs every bot; the modules share its queue, storage ledger and event hub.
          </p>
        </div>
        <div className="w-48"><SearchInput value={search} onChange={setSearch} placeholder="Bot id…" /></div>
      </div>

      {error ? <ErrorNote message={error} /> : null}

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Stat label="Bots" value={bots.length} icon={<Server className="h-4 w-4" />} />
        <Stat label="Running" value={bots.filter(([, row]) => row.running).length} icon={<Cpu className="h-4 w-4" />} />
        <Stat label="Module installs" value={enabledCount} hint="enabled module/bot pairs" />
      </div>

      <div className="space-y-3">
        {bots.length === 0 ? <Card>No bots matched.</Card> : null}
        {bots.map(([botId, row]) => (
          <Card key={botId}>
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/bots/${botId}`} className="font-mono text-sm text-violet-200">bot {botId}</Link>
              <Badge status={row.running ? "running" : "stopped"}>{row.running ? "running" : "stopped"}</Badge>
              <span className="ml-auto text-[11px] text-slate-500">
                {Object.values(row.plugins).filter((plugin) => plugin.enabled).length} modules enabled
              </span>
            </div>
            <div className="mt-3 space-y-2">
              {MEDIA_MODULES.map((slug) => {
                const plugin = row.plugins[slug];
                const record: PluginToggle | undefined = toggles.data?.find((entry) => entry.slug === slug);
                return (
                  <div key={slug} className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.05] px-3 py-1.5">
                    <span className="w-20 font-mono text-xs text-slate-300">{slug}</span>
                    <Badge status={plugin?.enabled ? (plugin.running ? "running" : "configured") : "disabled"}>
                      {plugin?.enabled ? (plugin.running ? "running" : "enabled (bot stopped)") : plugin?.state ?? "available"}
                    </Badge>
                    <span className="text-[11px] text-slate-500">scope {plugin?.scope ?? "—"}</span>
                    <div className="ml-auto flex items-center gap-2">
                      {record?.missing_config?.length ? (
                        <Badge status="warning">needs {record.missing_config.join(", ")}</Badge>
                      ) : null}
                      <Toggle
                        checked={Boolean(plugin?.enabled)}
                        onChange={(value) => toggle.mutate({ slug, botId: Number(botId), enabled: value })}
                        label={`toggle ${slug}`}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Link href="/platform/modules" className="btn-ghost"><Power className="h-4 w-4" /> Module management</Link>
        <Link href="/plugins?tab=switches" className="btn-ghost"><RefreshCw className="h-4 w-4" /> Plugin switches</Link>
      </div>
      <div className="mt-3">
        <ModuleStatusRow
          items={[
            { label: "worker manager", value: "shared", status: "ok" },
            { label: "queue", value: "process-local", status: "info" },
            { label: "scope", value: params.get("tab") ?? "all" },
          ]}
        />
      </div>
    </div>
  );
}
