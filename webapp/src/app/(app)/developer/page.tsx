"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense, useState } from "react";
import { Braces, Download, Eye, KeyRound, Play, Plus, Send, Terminal, Trash2, Upload, Webhook } from "lucide-react";
import { api, formatDate } from "@/lib/api";
import type { ApiKey, Bot, Page } from "@/lib/types";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  SectionHeader,
  Skeleton,
  Stat,
  Toggle,
} from "@/components/ui";
import { CodeBlock, SearchInput, Tabs } from "@/components/patterns";

type Tab = "keys" | "webhooks" | "preview" | "commands";

type ApiKeyCreated = { key: string; record: ApiKey; note: string };

type WebhookRecord = {
  id: number;
  bot_id: number;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
};

type BotCommand = {
  id: number | null;
  name: string;
  description: string;
  category: string;
  aliases: string[];
  permission: string;
  scope: string;
  enabled: boolean;
  handler_type: string;
  handler_ref: string;
  definition: Record<string, unknown>;
  source: string;
};

export default function DeveloperPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <DeveloperContent />
    </Suspense>
  );
}

function DeveloperContent() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "keys");

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Developer"
        description="API keys, signed webhooks, message previews and the declarative command registry."
        action={
          <Link href="/docs" className="btn-ghost">
            <Braces className="h-4 w-4" /> API reference
          </Link>
        }
      />

      <Tabs
        tabs={[
          { id: "keys" as Tab, label: "API keys" },
          { id: "webhooks" as Tab, label: "Webhooks" },
          { id: "commands" as Tab, label: "Command Studio" },
          { id: "preview" as Tab, label: "Message preview" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "keys" ? <ApiKeysPanel /> : null}
      {tab === "webhooks" ? <WebhooksPanel /> : null}
      {tab === "commands" ? <CommandsPanel /> : null}
      {tab === "preview" ? <PreviewPanel /> : null}
    </div>
  );
}

function ApiKeysPanel() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("default");
  const [scopes, setScopes] = useState("bots:read");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api.get<ApiKey[]>("/auth/api-keys") });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.get<Record<string, unknown>[]>("/auth/sessions") });

  const create = useMutation({
    mutationFn: () =>
      api.post<ApiKeyCreated>("/auth/api-keys", {
        name,
        scopes: scopes.split(",").map((scope) => scope.trim()).filter(Boolean),
      }),
    onSuccess: (result) => {
      setCreated(result);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/auth/api-keys/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
    onError: (err: Error) => setError(err.message),
  });

  const logoutAll = useMutation({
    mutationFn: () => api.post<{ revoked: number }>("/auth/logout-all"),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sessions"] }),
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-4">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-white">API keys</p>
            <p className="text-xs text-slate-500">
              Send as <span className="kbd">X-API-Key</span>. Only a hash is stored; the value is shown once.
            </p>
          </div>
          <button className="btn-primary" onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" /> New key
          </button>
        </div>

        {created ? (
          <div className="mt-4 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] p-3">
            <p className="text-xs font-medium text-emerald-200">{created.note}</p>
            <div className="mt-2">
              <CodeBlock title="api key">{created.key}</CodeBlock>
            </div>
          </div>
        ) : null}

        {keys.isLoading ? (
          <Skeleton className="mt-4 h-24" />
        ) : keys.data?.length ? (
          <div className="mt-4 overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Prefix</th>
                  <th>Scopes</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {keys.data.map((key) => (
                  <tr key={key.id}>
                    <td className="text-slate-100">{key.name}</td>
                    <td className="font-mono text-xs">{key.prefix}…</td>
                    <td className="text-xs">{key.scopes.join(", ") || "full account"}</td>
                    <td className="text-xs text-slate-500">{formatDate(key.created_at)}</td>
                    <td className="text-xs text-slate-500">{formatDate(key.last_used_at)}</td>
                    <td className="text-right">
                      <button className="btn-danger px-2 py-1" onClick={() => revoke.mutate(key.id)} aria-label="Revoke key">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="mt-4">
            <EmptyState title="No API keys" description="Create a key to call the API from scripts or CI." icon={<KeyRound className="h-6 w-6" />} />
          </div>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-white">Active sessions</p>
            <p className="text-xs text-slate-500">Sessions are hashed server-side and expire automatically.</p>
          </div>
          <button className="btn-danger" onClick={() => logoutAll.mutate()} disabled={logoutAll.isPending}>
            Revoke all
          </button>
        </div>
        {sessions.data?.length ? (
          <ul className="mt-3 space-y-1.5">
            {sessions.data.map((session, index) => (
              <li key={String(session.id ?? index)} className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.05] px-3 py-2 text-xs">
                <span className="font-mono text-slate-400">#{String(session.id)}</span>
                <span className="text-slate-500">created {formatDate(String(session.created_at ?? ""))}</span>
                <span className="text-slate-500">expires {formatDate(String(session.expires_at ?? ""))}</span>
                {session.revoked ? <Badge status="error">revoked</Badge> : <Badge status="ok">active</Badge>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-500">No sessions recorded.</p>
        )}
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Create API key"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
              Create key
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name">
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Scopes" hint="Comma separated, e.g. bots:read, groups:write">
            <input className="input" value={scopes} onChange={(event) => setScopes(event.target.value)} />
          </Field>
        </div>
      </Modal>
    </div>
  );
}

function WebhooksPanel() {
  const queryClient = useQueryClient();
  const [botId, setBotId] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const selected = botId || String(bots.data?.items?.[0]?.id ?? "");
  const webhooks = useQuery({
    queryKey: ["webhooks", selected],
    queryFn: () => api.get<WebhookRecord[]>(`/developer/bots/${selected}/webhooks`),
    enabled: Boolean(selected),
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["webhooks", selected] });

  const create = useMutation({
    mutationFn: () => api.post<WebhookRecord>(`/developer/bots/${selected}/webhooks`, { url, events: ["moderation", "activity"] }),
    onSuccess: () => {
      setUrl("");
      setError(null);
      invalidate();
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/developer/bots/${selected}/webhooks/${id}`),
    onSuccess: invalidate,
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[200px]">
            <label className="label">Bot</label>
            <select className="input" value={selected} onChange={(event) => setBotId(event.target.value)}>
              {(bots.data?.items ?? []).map((bot) => (
                <option key={bot.id} value={String(bot.id)}>
                  {bot.name}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-[280px] flex-1">
            <label className="label">Endpoint URL</label>
            <input className="input font-mono" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/meiko/hook" />
          </div>
          <button className="btn-primary" onClick={() => create.mutate()} disabled={!url || create.isPending}>
            <Plus className="h-4 w-4" /> Add webhook
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Deliveries are signed and retried; each attempt is recorded with its status code.
        </p>
      </Card>

      {webhooks.isLoading ? (
        <Skeleton className="h-32" />
      ) : webhooks.data?.length ? (
        <div className="space-y-2">
          {webhooks.data.map((webhook) => (
            <Card key={webhook.id} className="flex flex-wrap items-center gap-3 py-3">
              <Webhook className="h-4 w-4 text-cyan-300" />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-xs text-slate-200">{webhook.url}</p>
                <p className="text-[11px] text-slate-500">
                  events: {webhook.events.join(", ")} · created {formatDate(webhook.created_at)}
                </p>
              </div>
              <Badge status={webhook.enabled ? "enabled" : "disabled"}>{webhook.enabled ? "active" : "paused"}</Badge>
              <button className="btn-danger px-2 py-1" onClick={() => remove.mutate(webhook.id)} aria-label="Delete webhook">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No webhooks configured"
          description="Webhooks notify your systems about moderation actions, runtime events and plugin state changes."
          icon={<Webhook className="h-6 w-6" />}
        />
      )}
    </div>
  );
}

type StudioCommand = {
  id: number | null;
  name: string;
  description: string;
  category: string;
  aliases: string[];
  arguments: { name: string; required: boolean; kind: string; description?: string }[];
  permission: string;
  scope: string;
  enabled: boolean;
  owner_only?: boolean;
  hidden?: boolean;
  handler_type: string;
  handler_ref: string;
  plugin: string | null;
  source: string;
  running?: boolean;
};

type CommandLog = {
  id: number;
  command: string;
  chat_type: string;
  result: string;
  duration_ms: number;
  source: string;
  error: string;
  created_at: string;
};

type TestResult = {
  command: Record<string, unknown>;
  checks: { check: string; ok: boolean; detail: string }[];
  would_run: boolean;
  rendered: string | null;
  sent_to_telegram: boolean;
  executed_handler: boolean;
};

type StudioTab = "registry" | "builder" | "logs" | "transfer";

function CommandsPanel() {
  const queryClient = useQueryClient();
  const [botId, setBotId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<StudioTab>("registry");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<StudioCommand | null>(null);
  const [testRole, setTestRole] = useState("everyone");
  const [testChat, setTestChat] = useState("group");
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [form, setForm] = useState({
    name: "",
    description: "",
    response: "Hello {user}, welcome to {chat}!",
    permission: "everyone",
    scope: "all",
    aliases: "",
  });

  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const activeBot = botId || String(bots.data?.items?.[0]?.id ?? "");
  const activeBotName = (bots.data?.items ?? []).find((bot) => String(bot.id) === activeBot)?.name;

  const commands = useQuery({
    queryKey: ["commands", activeBot, search, category],
    queryFn: () =>
      api.get<StudioCommand[]>(
        `/bots/${activeBot}/commands?${new URLSearchParams({
          ...(search ? { search } : {}),
          ...(category ? { category } : {}),
        }).toString()}`,
      ),
    enabled: Boolean(activeBot),
  });
  const categories = useQuery({
    queryKey: ["command-categories", activeBot],
    queryFn: () => api.get<Record<string, number>>(`/bots/${activeBot}/commands/categories`),
    enabled: Boolean(activeBot),
  });
  const logs = useQuery({
    queryKey: ["command-logs", activeBot],
    queryFn: () => api.get<CommandLog[]>(`/bots/${activeBot}/commands/logs?limit=100`),
    enabled: Boolean(activeBot) && tab === "logs",
  });
  const stats = useQuery({
    queryKey: ["command-stats", activeBot],
    queryFn: () => api.get<{ commands: { command: string; total: number; results: Record<string, number>; avg_ms: number }[]; total: number }>(`/bots/${activeBot}/commands/stats`),
    enabled: Boolean(activeBot) && tab === "logs",
  });

  const reload = () => {
    void queryClient.invalidateQueries({ queryKey: ["commands"] });
    void queryClient.invalidateQueries({ queryKey: ["command-categories"] });
    void queryClient.invalidateQueries({ queryKey: ["command-logs"] });
    void queryClient.invalidateQueries({ queryKey: ["command-stats"] });
  };
  const fail = (err: Error) => setError(err.message);
  const done = (message: string) => {
    setError(null);
    setNotice(message);
  };

  const setOverride = useMutation({
    mutationFn: ({ name, patch }: { name: string; patch: Record<string, unknown> }) =>
      api.put(`/bots/${activeBot}/commands/${name}/override`, patch),
    onSuccess: () => {
      done("Command configuration saved.");
      reload();
    },
    onError: fail,
  });

  const clearOverride = useMutation({
    mutationFn: (name: string) => api.del(`/bots/${activeBot}/commands/${name}/override`),
    onSuccess: () => {
      done("Reverted to the platform default.");
      reload();
    },
    onError: fail,
  });

  const runTest = useMutation({
    mutationFn: (name: string) =>
      api.post<TestResult>(`/bots/${activeBot}/commands/test`, {
        name,
        chat_type: testChat,
        as_role: testRole,
      }),
    onSuccess: (result) => {
      setTestResult(result);
      setError(null);
      reload();
    },
    onError: fail,
  });

  const sync = useMutation({
    mutationFn: () => api.post<{ synced: number }>(`/bots/${activeBot}/commands/sync`),
    onSuccess: (result) => done(`Synced ${result.synced} commands to Telegram.`),
    onError: fail,
  });

  const preview = useMutation({
    mutationFn: () =>
      api.post<{ preview: string; buttons: unknown[]; valid: boolean }>(
        `/bots/${activeBot}/commands/preview`,
        {
          name: form.name || "preview",
          description: form.description,
          permission: form.permission,
          scope: form.scope,
          handler_type: "declarative",
          definition: {
            trigger: form.name || "preview",
            response: form.response,
            permission: form.permission,
            scope: form.scope,
          },
        },
      ),
    onError: fail,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post(`/bots/${activeBot}/commands`, {
        name: form.name,
        description: form.description,
        category: "custom",
        aliases: form.aliases.split(",").map((alias) => alias.trim()).filter(Boolean),
        permission: form.permission,
        scope: form.scope,
        enabled: true,
        handler_type: "declarative",
        definition: { trigger: form.name, response: form.response, permission: form.permission, scope: form.scope },
      }),
    onSuccess: () => {
      done(`/${form.name} created. Sync the menu to publish it to Telegram.`);
      setForm({ ...form, name: "", description: "" });
      reload();
    },
    onError: fail,
  });

  const exportConfig = useMutation({
    mutationFn: () => api.get<Record<string, unknown>>(`/bots/${activeBot}/commands/export`),
    onSuccess: (payload) => {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `bot-${activeBot}-commands.json`;
      link.click();
      URL.revokeObjectURL(url);
      done("Configuration exported.");
    },
    onError: fail,
  });

  const [importText, setImportText] = useState("");
  const importConfig = useMutation({
    mutationFn: () => {
      const parsed = JSON.parse(importText) as { commands?: unknown[] };
      if (!parsed.commands) throw new Error("The file must contain a commands array.");
      return api.post<{ created: number; updated: number; skipped: { name: string; reason: string }[] }>(
        `/bots/${activeBot}/commands/import`,
        { version: 1, commands: parsed.commands, replace: false },
      );
    },
    onSuccess: (result) =>
      done(
        `Imported: ${result.created} created, ${result.updated} updated${
          result.skipped.length ? `, ${result.skipped.length} rejected by validation` : ""
        }.`,
      ),
    onError: fail,
  });

  const rows = commands.data ?? [];

  return (
    <div className="space-y-3">
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-white">Command Studio</p>
          <p className="text-xs text-slate-500">
            Managing <span className="text-slate-300">{activeBotName ?? "—"}</span> (bot {activeBot || "none"}) ·
            commands are declared data; nothing here runs code or a handler.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input max-w-[200px]"
            value={activeBot}
            onChange={(event) => setBotId(event.target.value)}
            aria-label="Bot"
          >
            {(bots.data?.items ?? []).map((bot) => (
              <option key={bot.id} value={String(bot.id)}>
                {bot.name}
              </option>
            ))}
          </select>
          <button className="btn-ghost" onClick={() => sync.mutate()} disabled={sync.isPending || !activeBot}>
            <Send className="h-4 w-4" /> Sync menu
          </button>
        </div>
      </Card>

      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      {notice ? (
        <p className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
          {notice}
        </p>
      ) : null}

      <Tabs
        tabs={[
          { id: "registry" as StudioTab, label: "Registry", count: rows.length },
          { id: "builder" as StudioTab, label: "Builder" },
          { id: "logs" as StudioTab, label: "Executions" },
          { id: "transfer" as StudioTab, label: "Import / Export" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "registry" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="w-64">
              <SearchInput value={search} onChange={setSearch} placeholder="Search commands and aliases…" />
            </div>
            <button className={category === "" ? "btn-primary" : "btn-ghost"} onClick={() => setCategory("")}>
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

          {commands.isLoading ? (
            <Skeleton className="h-48" />
          ) : rows.length ? (
            <Card className="p-0">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Command</th>
                    <th>Category</th>
                    <th>Owner</th>
                    <th>Permission</th>
                    <th>Scope</th>
                    <th>Enabled</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((command) => (
                    <tr key={`${command.source}-${command.name}`}>
                      <td>
                        <p className="font-mono text-xs text-slate-100">/{command.name}</p>
                        <p className="text-[10.5px] text-slate-500">
                          {command.description || "no description"}
                          {command.aliases.length ? ` · aliases: ${command.aliases.join(", ")}` : ""}
                        </p>
                      </td>
                      <td className="text-xs text-slate-400">{command.category}</td>
                      <td className="text-xs text-slate-400">
                        {command.plugin ? (
                          <span className="flex items-center gap-1">
                            plugin:{command.plugin}
                            {command.running === false ? (
                              <span className="text-[10px] text-amber-300">not running</span>
                            ) : null}
                          </span>
                        ) : (
                          "core"
                        )}
                      </td>
                      <td className="text-xs">
                        <span className={command.owner_only ? "text-amber-300" : "text-slate-400"}>
                          {command.permission}
                        </span>
                      </td>
                      <td className="text-xs text-slate-400">{command.scope}</td>
                      <td>
                        <Toggle
                          checked={command.enabled}
                          onChange={(next) => setOverride.mutate({ name: command.name, patch: { enabled: next } })}
                          label={`toggle /${command.name}`}
                        />
                      </td>
                      <td>
                        <div className="flex justify-end gap-1">
                          <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setSelected(command)}>
                            Details
                          </button>
                          {command.source !== "core" && command.handler_type !== "plugin" ? (
                            <button
                              className="btn-ghost px-2 py-1 text-xs"
                              onClick={() => clearOverride.mutate(command.name)}
                            >
                              Reset
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : (
            <EmptyState
              title="No commands match"
              description="Clear the search or category filter. Core and plugin commands appear here automatically."
            />
          )}

          {selected ? (
            <Card className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-mono text-sm text-white">/{selected.name}</p>
                  <p className="text-xs text-slate-500">
                    {selected.handler_type} · {selected.source}
                    {selected.running === false ? " · handler not loaded (plugin stopped)" : ""}
                  </p>
                </div>
                <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setSelected(null)}>
                  Close
                </button>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Scope">
                  <select
                    className="input"
                    value={selected.scope}
                    onChange={(event) => {
                      setSelected({ ...selected, scope: event.target.value });
                      setOverride.mutate({ name: selected.name, patch: { scope: event.target.value } });
                    }}
                  >
                    <option value="all">all</option>
                    <option value="private">private only</option>
                    <option value="group">group only</option>
                  </select>
                </Field>
                <Field label="Permission">
                  <select
                    className="input"
                    value={selected.permission}
                    onChange={(event) => {
                      setSelected({ ...selected, permission: event.target.value });
                      setOverride.mutate({ name: selected.name, patch: { permission: event.target.value } });
                    }}
                  >
                    {["everyone", "member", "trusted", "moderator", "admin", "group_owner", "bot_owner"].map(
                      (permission) => (
                        <option key={permission} value={permission}>
                          {permission}
                        </option>
                      ),
                    )}
                  </select>
                </Field>
                <Field label="Description">
                  <input
                    className="input"
                    defaultValue={selected.description}
                    onBlur={(event) =>
                      setOverride.mutate({ name: selected.name, patch: { description: event.target.value } })
                    }
                  />
                </Field>
              </div>

              {selected.arguments.length ? (
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-slate-500">Arguments</p>
                  <ul className="mt-1 space-y-1 text-xs text-slate-300">
                    {selected.arguments.map((argument) => (
                      <li key={argument.name} className="font-mono">
                        {argument.name} · {argument.kind}
                        {argument.required ? " · required" : " · optional"}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="rounded-xl border border-white/[0.06] p-3">
                <p className="text-sm font-medium text-white">Dry run</p>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  Checks the declaration only. Nothing is sent to Telegram and no handler executes.
                </p>
                <div className="mt-2 flex flex-wrap items-end gap-2">
                  <Field label="As role">
                    <select className="input" value={testRole} onChange={(event) => setTestRole(event.target.value)}>
                      {["everyone", "member", "trusted", "moderator", "admin", "group_owner", "bot_owner"].map(
                        (role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ),
                      )}
                    </select>
                  </Field>
                  <Field label="In chat">
                    <select className="input" value={testChat} onChange={(event) => setTestChat(event.target.value)}>
                      <option value="private">private</option>
                      <option value="group">group</option>
                    </select>
                  </Field>
                  <button
                    className="btn-ghost"
                    onClick={() => runTest.mutate(selected.name)}
                    disabled={runTest.isPending}
                  >
                    <Play className="h-4 w-4" /> Run checks
                  </button>
                </div>
                {testResult ? (
                  <ul className="mt-2 space-y-1">
                    {testResult.checks.map((check) => (
                      <li key={check.check} className="flex items-center gap-2 text-xs">
                        <Badge status={check.ok ? "enabled" : "warning"}>{check.ok ? "pass" : "blocked"}</Badge>
                        <span className="text-slate-300">{check.check}</span>
                        <span className="text-slate-500">{check.detail}</span>
                      </li>
                    ))}
                    <li className="pt-1 text-xs">
                      <span className={testResult.would_run ? "text-emerald-300" : "text-rose-300"}>
                        would_run = {String(testResult.would_run)}
                      </span>
                      <span className="text-slate-500">
                        {" "}
                        · sent_to_telegram = {String(testResult.sent_to_telegram)} · executed_handler ={" "}
                        {String(testResult.executed_handler)}
                      </span>
                    </li>
                    {testResult.rendered ? (
                      <li className="rounded-lg bg-black/30 p-2 text-xs text-slate-200">{testResult.rendered}</li>
                    ) : null}
                  </ul>
                ) : null}
              </div>
            </Card>
          ) : null}
        </div>
      ) : null}

      {tab === "builder" ? (
        <Card className="space-y-3">
          <div>
            <p className="text-sm font-medium text-white">Declarative command builder</p>
            <p className="text-xs text-slate-500">
              A control block is a trigger, a template and optional buttons. The webapp never sends code.
            </p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-3">
              <Field label="Command" hint="lowercase, digits and underscores">
                <input
                  className="input font-mono"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  placeholder="rules"
                />
              </Field>
              <Field label="Description">
                <input
                  className="input"
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
              </Field>
              <Field label="Aliases" hint="comma separated">
                <input
                  className="input font-mono"
                  value={form.aliases}
                  onChange={(event) => setForm({ ...form, aliases: event.target.value })}
                  placeholder="rule, policy"
                />
              </Field>
              <Field label="Response template" hint="Allowed: {user} {username} {chat} {date} {count} {bot}">
                <textarea
                  className="input min-h-[110px]"
                  value={form.response}
                  onChange={(event) => setForm({ ...form, response: event.target.value })}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Permission">
                  <select
                    className="input"
                    value={form.permission}
                    onChange={(event) => setForm({ ...form, permission: event.target.value })}
                  >
                    {["everyone", "member", "trusted", "moderator", "admin", "group_owner", "bot_owner"].map(
                      (permission) => (
                        <option key={permission} value={permission}>
                          {permission}
                        </option>
                      ),
                    )}
                  </select>
                </Field>
                <Field label="Scope">
                  <select
                    className="input"
                    value={form.scope}
                    onChange={(event) => setForm({ ...form, scope: event.target.value })}
                  >
                    <option value="all">all</option>
                    <option value="private">private</option>
                    <option value="group">group</option>
                  </select>
                </Field>
              </div>
              <div className="flex gap-2">
                <button className="btn-ghost" onClick={() => preview.mutate()} disabled={preview.isPending}>
                  <Eye className="h-4 w-4" /> Preview
                </button>
                <button
                  className="btn-primary"
                  onClick={() => create.mutate()}
                  disabled={create.isPending || !form.name}
                >
                  <Plus className="h-4 w-4" /> Create command
                </button>
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Rendered preview</p>
              {preview.data ? (
                <div className="rounded-xl border border-white/[0.06] bg-black/30 p-3">
                  <p className="whitespace-pre-wrap text-sm text-slate-200">{preview.data.preview}</p>
                </div>
              ) : (
                <p className="text-xs text-slate-500">Preview a command to see exactly what Telegram would show.</p>
              )}
            </div>
          </div>
        </Card>
      ) : null}

      {tab === "logs" ? (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Total invocations" value={stats.data?.total ?? "…"} hint="from command_executions" />
            <Stat
              label="Denied / errors"
              value={
                (stats.data?.commands ?? []).reduce(
                  (sum, row) => sum + (row.results.error ?? 0) + (row.results.denied ?? 0),
                  0,
                ) || 0
              }
            />
            <Stat label="Distinct commands" value={stats.data?.commands.length ?? "…"} />
          </div>
          {logs.isLoading ? (
            <Skeleton className="h-48" />
          ) : logs.data?.length ? (
            <Card className="p-0">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Command</th>
                    <th>Result</th>
                    <th>Chat</th>
                    <th>Source</th>
                    <th>Duration</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.data.map((log) => (
                    <tr key={log.id}>
                      <td className="font-mono text-xs text-slate-100">/{log.command}</td>
                      <td>
                        <Badge status={log.result === "ok" ? "enabled" : log.result === "error" ? "error" : "warning"}>
                          {log.result}
                        </Badge>
                      </td>
                      <td className="text-xs text-slate-400">{log.chat_type || "—"}</td>
                      <td className="text-xs text-slate-400">{log.source}</td>
                      <td className="font-mono text-xs text-slate-500">{log.duration_ms} ms</td>
                      <td className="text-xs text-slate-500">{formatDate(log.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : (
            <EmptyState
              title="No invocations recorded"
              description="This log is written by the bot process for real /commands used in Telegram, plus Studio dry-runs."
            />
          )}
        </div>
      ) : null}

      {tab === "transfer" ? (
        <Card className="space-y-3">
          <div>
            <p className="text-sm font-medium text-white">Import / export configuration</p>
            <p className="text-xs text-slate-500">
              Ports this bot&apos;s command configuration to another bot. Declarative definitions are validated on
              import; invalid ones are rejected and reported, never stored.
            </p>
          </div>
          <button className="btn-ghost w-fit" onClick={() => exportConfig.mutate()} disabled={exportConfig.isPending}>
            <Download className="h-4 w-4" /> Export JSON
          </button>
          <Field label="Paste an export">
            <textarea
              className="input min-h-[160px] font-mono text-xs"
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              placeholder='{"version": 1, "commands": [...]}'
            />
          </Field>
          <button
            className="btn-primary w-fit"
            onClick={() => importConfig.mutate()}
            disabled={importConfig.isPending || !importText.trim()}
          >
            <Upload className="h-4 w-4" /> Import
          </button>
        </Card>
      ) : null}
    </div>
  );
}

function PreviewPanel() {
  const [kind, setKind] = useState<"html" | "rich">("html");
  const [title, setTitle] = useState("MEIKO AGENT");
  const [lines, setLines] = useState("Moderation is active.\nUse /rules for the group policy.");
  const [rich, setRich] = useState(
    JSON.stringify(
      [
        { type: "heading", text: "Weekly digest", size: 2 },
        { type: "paragraph", text: "Moderation is active in this chat." },
        { type: "bullets", items: ["3 mutes", "1 ban", "12 warnings"] },
        { type: "divider" },
        { type: "quote", text: "Powered by MEIKO AGENT" },
      ],
      null,
      2,
    ),
  );
  const [error, setError] = useState<string | null>(null);

  const render = useMutation({
    mutationFn: () =>
      api.post<{ kind: string; html_preview: string; plain_preview?: string; blocks?: number; keyboard?: unknown }>(
        "/developer/preview",
        kind === "rich"
          ? { kind, blocks: JSON.parse(rich) }
          : { kind, title, lines: lines.split("\n") },
      ),
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <Card>
        <div className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-violet-300" />
          <p className="text-sm font-medium text-white">UI Engine preview</p>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Rendered by the backend with the same builders the bot uses. Nothing is sent to Telegram.
        </p>
        <div className="mt-3 flex gap-2">
          <button className={kind === "html" ? "btn-primary" : "btn-ghost"} onClick={() => setKind("html")}>
            HTML message
          </button>
          <button className={kind === "rich" ? "btn-primary" : "btn-ghost"} onClick={() => setKind("rich")}>
            Rich blocks
          </button>
        </div>

        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div className="space-y-3">
            {kind === "html" ? (
              <>
                <Field label="Title">
                  <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
                </Field>
                <Field label="Lines" hint="One line per row; basic HTML is preserved by the builder.">
                  <textarea className="input min-h-[140px]" value={lines} onChange={(event) => setLines(event.target.value)} />
                </Field>
              </>
            ) : (
              <Field label="Blocks (JSON)" hint="Only declarative block types are rendered.">
                <textarea className="input min-h-[220px] font-mono text-xs" value={rich} onChange={(event) => setRich(event.target.value)} />
              </Field>
            )}
            <button className="btn-primary" onClick={() => render.mutate()} disabled={render.isPending}>
              <Send className="h-4 w-4" /> Render
            </button>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-black/30 p-3">
            <p className="text-[11px] uppercase tracking-wider text-slate-500">Output</p>
            {render.data ? (
              <div className="mt-2 space-y-2">
                <p className="whitespace-pre-wrap text-sm text-slate-200">{render.data.html_preview}</p>
                {render.data.plain_preview ? (
                  <div>
                    <p className="mt-2 text-[11px] uppercase tracking-wider text-slate-500">Plain fallback</p>
                    <p className="whitespace-pre-wrap text-xs text-slate-400">{render.data.plain_preview}</p>
                  </div>
                ) : null}
                {typeof render.data.blocks === "number" ? (
                  <p className="text-xs text-slate-500">{render.data.blocks} rich block(s)</p>
                ) : null}
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-500">Render a message to preview it here.</p>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
