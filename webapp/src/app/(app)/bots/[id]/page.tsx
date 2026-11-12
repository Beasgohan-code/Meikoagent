"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import {
  Activity,
  ArrowLeft,
  Boxes,
  Copy,
  Play,
  RefreshCw,
  RotateCw,
  Save,
  Square,
  Trash2,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { api, formatDate, relativeTime } from "@/lib/api";
import type { Bot, BotHealth, Group, PluginInstallation, Workflow } from "@/lib/types";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  SectionHeader,
  Skeleton,
  Toggle,
} from "@/components/ui";
import { cn } from "@/lib/utils";

type Tab = "overview" | "groups" | "plugins" | "automation" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "groups", label: "Groups" },
  { id: "plugins", label: "Plugins" },
  { id: "automation", label: "Automation" },
  { id: "settings", label: "Settings" },
];

export default function BotDetailPage() {
  const params = useParams<{ id: string }>();
  const botId = Number(params.id);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [error, setError] = useState<string | null>(null);

  const bot = useQuery({ queryKey: ["bot", botId], queryFn: () => api.get<Bot>(`/bots/${botId}`) });
  const health = useQuery({
    queryKey: ["bot-health", botId],
    queryFn: () => api.get<BotHealth>(`/bots/${botId}/health`),
    refetchInterval: 15_000,
  });
  const groups = useQuery({
    queryKey: ["bot-groups", botId],
    queryFn: () => api.get<Group[]>(`/bots/${botId}/groups`),
  });
  const plugins = useQuery({
    queryKey: ["plugins-installed", botId],
    queryFn: () => api.get<PluginInstallation[]>(`/plugins/installed?bot_id=${botId}`),
  });
  const workflows = useQuery({
    queryKey: ["workflows", botId],
    queryFn: () => api.get<Workflow[]>(`/bots/${botId}/workflows`),
  });

  const synchronize = () => {
    void queryClient.invalidateQueries({ queryKey: ["bot", botId] });
    void queryClient.invalidateQueries({ queryKey: ["bot-health", botId] });
    void queryClient.invalidateQueries({ queryKey: ["bots"] });
  };

  const act = (action: "start" | "restart" | "stop") =>
    useMutation({
      mutationFn: () => api.post<BotHealth>(`/bots/${botId}/${action}`),
      onSuccess: (result) => {
        setError(result.running === false && action !== "stop" ? result.last_error ?? "Bot did not start" : null);
        synchronize();
      },
      onError: (err: Error) => setError(err.message),
    });

  // Hooks must not be created lazily inside handlers, so declare them eagerly.
  const start = useMutation({
    mutationFn: () => api.post<BotHealth>(`/bots/${botId}/start`),
    onSuccess: (result) => {
      setError(result.running ? null : result.last_error ?? "Bot did not start");
      synchronize();
    },
    onError: (err: Error) => setError(err.message),
  });
  const stop = useMutation({
    mutationFn: () => api.post<BotHealth>(`/bots/${botId}/stop`),
    onSuccess: synchronize,
    onError: (err: Error) => setError(err.message),
  });
  const restart = useMutation({
    mutationFn: () => api.post<BotHealth>(`/bots/${botId}/restart`),
    onSuccess: (result) => {
      setError(result.running ? null : result.last_error ?? "Bot did not restart");
      synchronize();
    },
    onError: (err: Error) => setError(err.message),
  });
  void act;

  if (bot.isError) {
    return (
      <div className="space-y-4">
        <Link href="/bots" className="btn-ghost">
          <ArrowLeft className="h-4 w-4" /> Back to bots
        </Link>
        <ErrorNote message="This bot could not be loaded. It may have been deleted or belongs to another account." onRetry={() => bot.refetch()} />
      </div>
    );
  }

  if (bot.isLoading || !bot.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const record = bot.data;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/bots" className="mb-2 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
            <ArrowLeft className="h-3 w-3" /> Bots
          </Link>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-white">{record.name}</h1>
            <Badge status={record.status}>{record.status}</Badge>
            {record.is_clone ? <Badge status="info">clone</Badge> : null}
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {record.username ? `@${record.username}` : "username pending verification"} ·{" "}
            {record.runtime_mode} · created {formatDate(record.created_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-ghost" onClick={() => restart.mutate()} disabled={restart.isPending}>
            <RotateCw className="h-4 w-4" /> Restart
          </button>
          {record.status === "running" ? (
            <button className="btn-danger" onClick={() => stop.mutate()} disabled={stop.isPending}>
              <Square className="h-4 w-4" /> Stop
            </button>
          ) : (
            <button className="btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>
              <Play className="h-4 w-4" /> Start
            </button>
          )}
        </div>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}

      <div className="flex gap-1 overflow-x-auto rounded-xl border border-white/[0.06] bg-charcoal-850/60 p-1">
        {TABS.map((item) => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            className={cn(
              "whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition",
              tab === item.id ? "bg-violet-500/15 text-violet-200" : "text-slate-400 hover:text-slate-200",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <p className="text-sm font-medium text-white">Runtime</p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Info label="Status" value={health.data?.status ?? record.status} />
              <Info label="Mode" value={record.runtime_mode} />
              <Info label="Uptime" value={health.data ? `${Math.floor(health.data.uptime_seconds / 60)} min` : "—"} />
              <Info label="Plugins loaded" value={String(health.data?.plugin_count ?? 0)} />
              <Info label="Token" value={record.token_valid ? "verified with getMe" : "unverified"} />
              <Info label="Restarts" value={String(record.restart_count)} />
            </div>
            {health.data?.note ? (
              <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">
                {health.data.note}
              </p>
            ) : null}
            {record.last_error ? (
              <p className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-xs text-rose-200">
                {record.last_error}
              </p>
            ) : null}
          </Card>

          <Card>
            <p className="text-sm font-medium text-white">Connect webhook</p>
            <p className="mt-1 text-xs text-slate-500">
              Endpoint Telegram should call in webhook mode. The secret header is validated server-side.
            </p>
            <code className="mt-3 block break-all rounded-lg border border-white/[0.06] bg-black/30 px-3 py-2 font-mono text-[11px] text-cyan-200">
              POST /api/v1/telegram/webhook/{record.id}
            </code>
            <p className="mt-3 text-xs text-slate-500">
              Only registers when the runtime is available; updates are rejected rather than acknowledged
              silently.
            </p>
          </Card>

          <Card className="lg:col-span-3">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium text-white">Groups</p>
              <Link href="/groups" className="text-xs text-violet-300 hover:underline">
                Group settings
              </Link>
            </div>
            {groups.data && groups.data.length > 0 ? (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {groups.data.slice(0, 6).map((group) => (
                  <Link
                    key={group.id}
                    href={`/groups/${group.id}`}
                    className="rounded-xl border border-white/[0.06] px-3 py-2.5 transition hover:border-violet-500/30"
                  >
                    <p className="truncate text-sm text-slate-100">{group.title}</p>
                    <p className="text-xs text-slate-500">
                      {group.member_count} members · {group.bot_status}
                    </p>
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyState
                title="No groups yet"
                description="Add the bot to a Telegram group; the runtime records the chat on the first update."
                icon={<Activity className="h-6 w-6" />}
              />
            )}
          </Card>
        </div>
      ) : null}

      {tab === "groups" ? <BotGroups botId={botId} /> : null}
      {tab === "plugins" ? <BotPlugins botId={botId} data={plugins.data} loading={plugins.isLoading} onChange={synchronize} /> : null}
      {tab === "automation" ? (
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-white">Workflows</p>
            <Link href="/automation" className="btn-ghost">
              <WorkflowIcon className="h-4 w-4" /> Open studio
            </Link>
          </div>
          {workflows.data && workflows.data.length > 0 ? (
            <div className="space-y-2">
              {workflows.data.map((workflow) => (
                <div key={workflow.id} className="flex items-center justify-between rounded-xl border border-white/[0.06] px-3 py-2.5">
                  <div>
                    <p className="text-sm text-slate-100">{workflow.name}</p>
                    <p className="text-xs text-slate-500">
                      v{workflow.version} · {workflow.run_count} runs · {workflow.enabled ? "enabled" : "disabled"}
                    </p>
                  </div>
                  <Badge status={workflow.enabled ? "enabled" : "disabled"}>
                    {workflow.enabled ? "active" : "paused"}
                  </Badge>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No workflows yet"
              description="Build trigger → condition → action flows in the Automation Studio; nothing is executed from raw code."
            />
          )}
        </Card>
      ) : null}
      {tab === "settings" ? <BotSettings bot={record} onSaved={synchronize} onDeleted={() => router.push("/bots")} /> : null}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
      <p className="text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm text-slate-200">{value}</p>
    </div>
  );
}

function BotGroups({ botId }: { botId: number }) {
  const groups = useQuery({
    queryKey: ["bot-groups", botId],
    queryFn: () => api.get<Group[]>(`/bots/${botId}/groups`),
  });
  if (groups.isLoading) return <Skeleton className="h-48" />;
  if (!groups.data?.length) {
    return (
      <EmptyState
        title="No groups recorded"
        description="Groups appear after the bot receives its first update from a chat."
      />
    );
  }
  return (
    <Card className="overflow-x-auto p-0">
      <table className="table-base">
        <thead>
          <tr>
            <th>Group</th>
            <th>Chat id</th>
            <th>Members</th>
            <th>Bot role</th>
            <th>Last activity</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {groups.data.map((group) => (
            <tr key={group.id}>
              <td className="text-slate-100">{group.title}</td>
              <td className="font-mono text-xs">{group.chat_id}</td>
              <td>{group.member_count}</td>
              <td>
                <Badge status={group.bot_status === "administrator" ? "enabled" : "installed"}>{group.bot_status}</Badge>
              </td>
              <td className="text-xs text-slate-500">{relativeTime(group.last_activity_at)}</td>
              <td className="text-right">
                <Link href={`/groups/${group.id}`} className="text-xs text-violet-300 hover:underline">
                  Manage
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function BotPlugins({
  botId,
  data,
  loading,
  onChange,
}: {
  botId: number;
  data: PluginInstallation[] | undefined;
  loading: boolean;
  onChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const toggle = useMutation({
    mutationFn: ({ id, enable }: { id: number; enable: boolean }) =>
      api.post(`/plugins/installations/${id}/${enable ? "enable" : "disable"}`),
    onSuccess: () => {
      setError(null);
      onChange();
    },
    onError: (err: Error) => setError(err.message),
  });

  if (loading) return <Skeleton className="h-48" />;
  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm font-medium text-white">Plugin installations for this bot</p>
          <Link href={`/plugins?bot=${botId}`} className="text-xs text-violet-300 hover:underline">
            Install more
          </Link>
        </div>
        {data && data.length > 0 ? (
          <div className="space-y-2">
            {data.map((installation) => (
              <div key={installation.id} className="flex items-center gap-3 rounded-xl border border-white/[0.06] px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-100">
                    {installation.plugin?.name ?? `plugin #${installation.plugin_id}`}
                  </p>
                  <p className="text-xs text-slate-500">
                    scope {installation.scope} · v{installation.version} · {installation.state}
                  </p>
                </div>
                <Toggle
                  checked={["enabled", "configured", "running"].includes(installation.state)}
                  onChange={(value) => toggle.mutate({ id: installation.id, enable: value })}
                  label="toggle plugin"
                />
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No plugins installed"
            description="Install from the marketplace; disabled plugins register no handlers at all."
            icon={<Boxes className="h-6 w-6" />}
            action={
              <Link href="/plugins" className="btn-primary">
                Open marketplace
              </Link>
            }
          />
        )}
      </Card>
    </div>
  );
}

function BotSettings({
  bot,
  onSaved,
  onDeleted,
}: {
  bot: Bot;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(bot.name);
  const [description, setDescription] = useState(bot.description);
  const [mode, setMode] = useState(bot.runtime_mode);
  const [cloneToken, setCloneToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => api.patch<Bot>(`/bots/${bot.id}`, { name, description, runtime_mode: mode }),
    onSuccess: () => {
      setNotice("Saved");
      onSaved();
    },
    onError: (err: Error) => setError(err.message),
  });

  const clone = useMutation({
    mutationFn: () => api.post<Bot>(`/bots/${bot.id}/clone`, { token: cloneToken.trim(), copy_plugins: true }),
    onSuccess: () => {
      setNotice("Clone created — see My Bots");
      setCloneToken("");
      onSaved();
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: () => api.del(`/bots/${bot.id}`),
    onSuccess: onDeleted,
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <p className="text-sm font-medium text-white">Bot configuration</p>
        {error ? <div className="mt-3"><ErrorNote message={error} onRetry={() => setError(null)} /></div> : null}
        {notice ? (
          <p className="mt-3 rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
            {notice}
          </p>
        ) : null}
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            setNotice(null);
            save.mutate();
          }}
        >
          <Field label="Name">
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Description">
            <textarea
              className="input min-h-[80px]"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
          <Field label="Runtime mode">
            <select className="input" value={mode} onChange={(event) => setMode(event.target.value)}>
              <option value="polling">Long polling</option>
              <option value="webhook">Webhook</option>
            </select>
          </Field>
          <button className="btn-primary" type="submit" disabled={save.isPending}>
            <Save className="h-4 w-4" /> Save changes
          </button>
        </form>
      </Card>

      <div className="space-y-4">
        <Card>
          <p className="text-sm font-medium text-white">Clone this bot</p>
          <p className="mt-1 text-xs text-slate-500">
            A clone gets an independent runtime, logs and settings. Provide a token from a second bot
            created with @BotFather.
          </p>
          <div className="mt-3 space-y-2">
            <input
              className="input font-mono"
              type="password"
              placeholder="123456789:AAH…"
              value={cloneToken}
              onChange={(event) => setCloneToken(event.target.value)}
            />
            <button
              className="btn-ghost w-full"
              onClick={() => clone.mutate()}
              disabled={clone.isPending || !cloneToken.includes(":")}
            >
              <Copy className="h-4 w-4" /> Create clone
            </button>
          </div>
        </Card>

        <Card className="border-rose-500/20">
          <p className="text-sm font-medium text-rose-200">Danger zone</p>
          <p className="mt-1 text-xs text-slate-500">
            Deleting removes configuration, plugins, groups and history. The Telegram bot is untouched.
          </p>
          <button className="btn-danger mt-3 w-full" onClick={() => remove.mutate()} disabled={remove.isPending}>
            <Trash2 className="h-4 w-4" /> Delete bot
          </button>
        </Card>

        <Card>
          <p className="text-sm font-medium text-white">Sync command menu</p>
          <p className="mt-1 text-xs text-slate-500">
            Pushes the enabled command list to Telegram. Requires a verified token.
          </p>
          <button
            className="btn-ghost mt-3 w-full"
            onClick={async () => {
              try {
                const result = await api.post<{ synced: number; configured?: boolean }>(
                  `/bots/${bot.id}/commands/sync`,
                );
                setNotice(
                  result.configured === false
                    ? "Telegram accepted nothing: token not verified"
                    : `Synced ${result.synced} commands`,
                );
              } catch (err) {
                setError(err instanceof Error ? err.message : "Sync failed");
              }
            }}
          >
            <RefreshCw className="h-4 w-4" /> Sync commands
          </button>
        </Card>
      </div>
    </div>
  );
}
