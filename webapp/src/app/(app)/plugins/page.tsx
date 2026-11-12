"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { AlertTriangle, Boxes, Download, Play, Power, RotateCw, Search, Trash2 } from "lucide-react";
import { api, formatDate } from "@/lib/api";
import type { Bot, Page, Plugin, PluginInstallation, PluginToggle } from "@/lib/types";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Modal,
  SectionHeader,
  Skeleton,
  Stat,
  Toggle,
} from "@/components/ui";
import { SearchInput, Tabs } from "@/components/patterns";
import { emoji } from "@/lib/premium-emoji";

type Tab = "marketplace" | "switches" | "installed" | "logs";

type PluginDetail = Plugin & { implemented: boolean; installable: boolean; note: string | null; manifest: Record<string, unknown> };
type PluginLog = { id: number; level: string; message: string; created_at: string; installation_id: number };

export default function PluginsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <PluginsContent />
    </Suspense>
  );
}

function PluginsContent() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "marketplace");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [target, setTarget] = useState<Plugin | null>(null);
  const [botId, setBotId] = useState<string>(params.get("bot") ?? "");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const catalogue = useQuery({ queryKey: ["plugins"], queryFn: () => api.get<Plugin[]>("/plugins") });
  const categories = useQuery({ queryKey: ["plugin-categories"], queryFn: () => api.get<Record<string, number>>("/plugins/categories") });
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const installed = useQuery({
    queryKey: ["plugins-installed", botId],
    queryFn: () => api.get<PluginInstallation[]>(`/plugins/installed${botId ? `?bot_id=${botId}` : ""}`),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const install = useMutation({
    mutationFn: ({ slug, targetBot }: { slug: string; targetBot: number | null }) =>
      api.post(`/plugins/${slug}/install`, { bot_id: targetBot }),
    onSuccess: () => {
      setTarget(null);
      setError(null);
      invalidate();
      setTab("installed");
    },
    onError: (err: Error) => setError(err.message),
  });

  const lifecycle = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "enable" | "disable" | "restart" }) =>
      api.post(`/plugins/installations/${id}/${action}`),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err: Error) => setError(err.message),
  });

  const uninstall = useMutation({
    mutationFn: (id: number) => api.del(`/plugins/installations/${id}`),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err: Error) => setError(err.message),
  });

  const filtered = useMemo(
    () =>
      (catalogue.data ?? []).filter((plugin) => {
        const matchesCategory = !category || plugin.category === category;
        const haystack = `${plugin.name} ${plugin.slug} ${plugin.description} ${plugin.author}`.toLowerCase();
        return matchesCategory && haystack.includes(search.toLowerCase());
      }),
    [catalogue.data, category, search],
  );

  const installations = installed.data ?? [];
  const enabledCount = installations.filter((row) => ["enabled", "configured", "running"].includes(row.state)).length;

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Plugin Marketplace"
        description="Typed manifests, real lifecycle states and tenant-isolated execution. Disabled plugins register nothing."
        action={
          <select className="input max-w-[240px]" value={botId} onChange={(event) => setBotId(event.target.value)}>
            <option value="">All my bots</option>
            {(bots.data?.items ?? []).map((bot) => (
              <option key={bot.id} value={bot.id}>
                {bot.name}
              </option>
            ))}
          </select>
        }
      />

      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Catalogue" value={catalogue.data?.length ?? "…"} icon={<Boxes className="h-4 w-4" />} />
        <Stat label="Installed" value={installations.length} hint={botId ? "for the selected bot" : "across your bots"} />
        <Stat label="Active" value={enabledCount} hint="enabled, configured or running" />
      </div>

      <Tabs
        tabs={[
          { id: "marketplace" as Tab, label: "Marketplace" },
          { id: "switches" as Tab, label: "On / Off" },
          { id: "installed" as Tab, label: "Installed", count: installations.length },
          { id: "logs" as Tab, label: "Logs" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "marketplace" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="w-64">
              <SearchInput value={search} onChange={setSearch} placeholder="Search plugins…" />
            </div>
            <button
              className={category === "" ? "btn-primary" : "btn-ghost"}
              onClick={() => setCategory("")}
            >
              All
            </button>
            {Object.entries(categories.data ?? {}).map(([name, count]) => (
              <button
                key={name}
                className={category === name ? "btn-primary" : "btn-ghost"}
                onClick={() => setCategory(name)}
              >
                {name} <span className="text-[10px] opacity-70">{count}</span>
              </button>
            ))}
          </div>

          {catalogue.isLoading ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
            </div>
          ) : filtered.length ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {filtered.map((plugin) => (
                <Card key={plugin.slug} hover className="flex flex-col">
                  <div className="flex items-start gap-3">
                    <div className="grid h-10 w-10 place-items-center rounded-xl bg-violet-500/15 text-lg">
                      {emoji(plugin.icon)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <Link href={`/plugins/${plugin.slug}`} className="truncate text-sm font-semibold text-white hover:text-violet-200">
                          {plugin.name}
                        </Link>
                        {plugin.is_official ? <Badge status="info">official</Badge> : null}
                      </div>
                      <p className="truncate text-[11px] text-slate-500">
                        v{plugin.version} · {plugin.author}
                      </p>
                    </div>
                    <Badge status={plugin.status}>{plugin.status}</Badge>
                  </div>
                  <p className="mt-3 flex-1 text-sm leading-relaxed text-slate-400">{plugin.description}</p>
                  <div className="mt-3 flex flex-wrap gap-1">
                    {plugin.permissions.slice(0, 4).map((permission) => (
                      <span key={permission} className="badge border-white/10 bg-white/[0.03] text-slate-400">
                        {permission}
                      </span>
                    ))}
                  </div>
                  <button className="btn-primary mt-4 w-full" onClick={() => setTarget(plugin)}>
                    <Download className="h-4 w-4" /> Install
                  </button>
                </Card>
              ))}
            </div>
          ) : (
            <EmptyState title="No plugins match" description="Try a different search or category." icon={<Search className="h-6 w-6" />} />
          )}
        </div>
      ) : null}

      {tab === "switches" ? <PluginSwitches botId={botId} onError={setError} /> : null}

      {tab === "installed" ? (
        installed.isLoading ? (
          <Skeleton className="h-48" />
        ) : installations.length ? (
          <div className="space-y-2">
            {installations.map((row) => {
              const active = ["enabled", "configured", "running"].includes(row.state);
              return (
                <Card key={row.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="grid h-9 w-9 place-items-center rounded-lg bg-violet-500/15 text-base">
                    {emoji(row.plugin?.icon ?? "plug")}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-slate-100">{row.plugin?.name ?? `plugin #${row.plugin_id}`}</p>
                    <p className="text-[11px] text-slate-500">
                      {row.scope} scope · v{row.version} · {row.bot_id ? `bot ${row.bot_id}` : "global"}
                      {row.last_error ? ` · ${row.last_error}` : ""}
                    </p>
                  </div>
                  <Badge status={row.state}>{row.state}</Badge>
                  <div className="flex items-center gap-1">
                    <button
                      className={active ? "btn-ghost px-2 py-1" : "btn-primary px-2 py-1"}
                      onClick={() => lifecycle.mutate({ id: row.id, action: active ? "disable" : "enable" })}
                      title={active ? "Disable" : "Enable"}
                    >
                      <Power className="h-3.5 w-3.5" />
                    </button>
                    <button className="btn-ghost px-2 py-1" onClick={() => lifecycle.mutate({ id: row.id, action: "restart" })} title="Restart">
                      <RotateCw className="h-3.5 w-3.5" />
                    </button>
                    <button className="btn-danger px-2 py-1" onClick={() => uninstall.mutate(row.id)} title="Uninstall">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </Card>
              );
            })}
          </div>
        ) : (
          <EmptyState
            title="Nothing installed yet"
            description="Install a plugin into a bot or globally; the lifecycle goes AVAILABLE → INSTALLED → ENABLED → CONFIGURED → RUNNING."
            icon={<Boxes className="h-6 w-6" />}
            action={
              <button className="btn-primary" onClick={() => setTab("marketplace")}>
                Browse marketplace
              </button>
            }
          />
        )
      ) : null}

      {tab === "logs" ? <PluginLogs installations={installations} /> : null}

      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={`Install ${target?.name ?? ""}`}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setTarget(null)}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={() => target && install.mutate({ slug: target.slug, targetBot: botId ? Number(botId) : null })}
              disabled={install.isPending}
            >
              <Play className="h-4 w-4" /> Install
            </button>
          </>
        }
      >
        <p className="text-sm text-slate-300">
          The plugin will be installed {botId ? `into bot ${botId}` : "globally"}. After installation you can enable it
          and fill its configuration; enabling without required config is rejected with{" "}
          <span className="kbd">config_required</span>.
        </p>
        <div className="mt-3">
          <label className="label">Target bot</label>
          <select className="input" value={botId} onChange={(event) => setBotId(event.target.value)}>
            <option value="">Global (applies to every bot)</option>
            {(bots.data?.items ?? []).map((bot) => (
              <option key={bot.id} value={bot.id}>
                {bot.name}
              </option>
            ))}
          </select>
        </div>
        {target?.permissions.length ? (
          <p className="mt-3 flex items-start gap-2 text-xs text-slate-500">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 text-amber-300" />
            Requests: {target.permissions.join(", ")}
          </p>
        ) : null}
      </Modal>
    </div>
  );
}

function PluginSwitches({ botId, onError }: { botId: string; onError: (message: string | null) => void }) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<string | null>(null);

  const toggles = useQuery({
    queryKey: ["plugin-toggles", botId],
    queryFn: () => api.get<PluginToggle[]>(`/plugins/toggles${botId ? `?bot_id=${botId}` : ""}`),
  });

  const flip = useMutation({
    mutationFn: ({ slug, enabled }: { slug: string; enabled: boolean }) =>
      api.post(`/plugins/${slug}/toggle?enabled=${enabled}${botId ? `&bot_id=${botId}` : ""}`),
    onMutate: ({ slug }) => setPending(slug),
    onSuccess: () => {
      onError(null);
      void queryClient.invalidateQueries({ queryKey: ["plugin-toggles"] });
      void queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });
    },
    onError: (err: Error) => onError(err.message),
    onSettled: () => setPending(null),
  });

  const rows = toggles.data ?? [];
  const on = rows.filter((row) => row.enabled).length;
  const grouped = rows.reduce<Record<string, PluginToggle[]>>((acc, row) => {
    (acc[row.category] = acc[row.category] ?? []).push(row);
    return acc;
  }, {});

  if (toggles.isLoading) return <Skeleton className="h-64" />;
  if (!rows.length) {
    return <EmptyState title="No toggleable plugins" description="Builtin plugin packages appear here once registered." />;
  }

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-white">
            {on} of {rows.length} plugins enabled
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Flipping a switch installs the plugin into {botId ? `bot ${botId}` : "global scope"} if needed, then
            enables or disables it — no restart required.
          </p>
        </div>
        <Badge status={on ? "running" : "available"}>{botId ? `bot ${botId}` : "global"}</Badge>
      </Card>

      {Object.entries(grouped).map(([category, plugins]) => (
        <Card key={category} className="space-y-1">
          <p className="text-[11px] uppercase tracking-wider text-slate-500">{category}</p>
          <div className="divide-y divide-white/[0.04]">
            {plugins.map((plugin) => (
              <div key={plugin.slug} className="flex items-center gap-3 py-2.5">
                <div className="grid h-9 w-9 place-items-center rounded-lg bg-violet-500/15 text-base">
                  {emoji(plugin.icon)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/plugins/${plugin.slug}`}
                      className="truncate text-sm font-medium text-slate-100 hover:text-violet-200"
                    >
                      {plugin.name}
                    </Link>
                    <Badge status={plugin.enabled ? "enabled" : plugin.state}>{plugin.state}</Badge>
                    {pending === plugin.slug ? <span className="text-[10px] text-slate-500">saving…</span> : null}
                  </div>
                  <p className="line-clamp-1 text-[11px] text-slate-500">{plugin.description}</p>
                  {plugin.missing_config.length ? (
                    <p className="text-[11px] text-amber-300/80">
                      needs config: {plugin.missing_config.join(", ")}
                    </p>
                  ) : null}
                </div>
                <Toggle
                  checked={plugin.enabled}
                  onChange={(next) => flip.mutate({ slug: plugin.slug, enabled: next })}
                  label={`toggle ${plugin.name}`}
                />
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

function PluginLogs({ installations }: { installations: PluginInstallation[] }) {
  const [selected, setSelected] = useState<number | null>(installations[0]?.id ?? null);
  const logs = useQuery({
    queryKey: ["plugin-logs", selected],
    queryFn: () => api.get<PluginLog[]>(`/plugins/installations/${selected}/logs?limit=100`),
    enabled: Boolean(selected),
    refetchInterval: 15_000,
  });

  if (!installations.length) {
    return <EmptyState title="No installations to inspect" description="Install a plugin to see its runtime logs." />;
  }
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="label mb-0">Installation</label>
        <select
          className="input max-w-[320px]"
          value={selected ?? ""}
          onChange={(event) => setSelected(Number(event.target.value))}
        >
          {installations.map((row) => (
            <option key={row.id} value={row.id}>
              {row.plugin?.name ?? row.plugin_id} · {row.scope} · {row.state}
            </option>
          ))}
        </select>
      </div>
      {logs.isLoading ? (
        <Skeleton className="h-40" />
      ) : logs.data?.length ? (
        <ul className="max-h-[420px] space-y-1 overflow-y-auto font-mono text-[11.5px]">
          {logs.data.map((entry) => (
            <li key={entry.id} className="flex gap-2 rounded-lg px-2 py-1 hover:bg-white/[0.03]">
              <span className="text-slate-600">{formatDate(entry.created_at)}</span>
              <span
                className={
                  entry.level === "error"
                    ? "text-rose-300"
                    : entry.level === "warning"
                      ? "text-amber-300"
                      : "text-slate-400"
                }
              >
                {entry.level}
              </span>
              <span className="flex-1 text-slate-300">{entry.message}</span>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title="No log entries" description="Plugin logs are written when the plugin runs inside a bot runtime." />
      )}
    </Card>
  );
}
