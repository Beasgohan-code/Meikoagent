"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import {
  Bot,
  Crown,
  Power,
  ShieldAlert,
  Users,
  Wrench,
} from "lucide-react";
import { api, formatDate } from "@/lib/api";
import type { CapabilityState, User } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton, Stat, Toggle } from "@/components/ui";
import { SearchInput, Tabs } from "@/components/patterns";

type Tab = "overview" | "users" | "bots" | "plugins" | "capabilities" | "audit" | "security";

type OwnerOverview = {
  users: number;
  bots: number;
  running_bots: number;
  plugin_installations: number;
  runtime_errors: number;
  workers: { id: number; name: string; status: string; load: string }[];
};

type OwnerBot = {
  id: number;
  owner_id: number;
  name: string;
  username: string | null;
  status: string;
  is_clone: boolean;
  token_valid: boolean;
  last_error: string | null;
  worker_id: number | null;
};

type AuditRow = {
  id: number;
  actor_user_id: number | null;
  actor_type: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip_address: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
};

type SecurityEvent = { id: number; action: string; actor_user_id: number | null; ip_address: string | null; created_at: string };

type GlobalInstallation = {
  id: number;
  slug: string | null;
  name: string | null;
  state: string;
  status: string;
  version: string;
  running_somewhere: boolean;
  config: Record<string, unknown>;
};

type PluginOverviewRow = {
  slug: string;
  name: string;
  version: string;
  category: string;
  status: string;
  implemented: boolean;
  permissions: string[];
  missing_permissions: string[];
  pending_permission_changes: string[];
  dependencies: string[];
  installations: { global: number; bot: number; group: number };
  states: Record<string, number>;
};

type PluginOverview = {
  plugins: PluginOverviewRow[];
  totals: Record<string, number>;
};

const TABS: Tab[] = ["overview", "users", "bots", "plugins", "capabilities", "audit", "security"];

export default function OwnerPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <OwnerContent />
    </Suspense>
  );
}

function OwnerContent() {
  // The sidebar deep-links sections with ?tab=…; an unknown value falls back to
  // Overview rather than rendering nothing.
  const params = useSearchParams();
  const requested = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(requested && TABS.includes(requested) ? requested : "overview");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const overview = useQuery({ queryKey: ["owner-overview"], queryFn: () => api.get<OwnerOverview>("/owner/overview") });
  const users = useQuery({ queryKey: ["owner-users"], queryFn: () => api.get<User[]>("/owner/users?limit=100"), enabled: tab === "users" });
  const bots = useQuery({ queryKey: ["owner-bots"], queryFn: () => api.get<OwnerBot[]>("/owner/bots?limit=200"), enabled: tab === "bots" });
  const audit = useQuery({ queryKey: ["owner-audit"], queryFn: () => api.get<AuditRow[]>("/owner/audit?limit=200"), enabled: tab === "audit" });
  const security = useQuery({
    queryKey: ["owner-security"],
    queryFn: () => api.get<SecurityEvent[]>("/owner/security-events"),
    enabled: tab === "security",
  });

  const pluginOverview = useQuery({
    queryKey: ["owner-plugin-overview"],
    queryFn: () => api.get<PluginOverview>("/owner/plugins/overview"),
    enabled: tab === "plugins",
  });
  const globalPlugins = useQuery({
    queryKey: ["owner-global-plugins"],
    queryFn: () => api.get<GlobalInstallation[]>("/owner/plugins/global"),
    enabled: tab === "plugins",
  });

  const reconcileGlobal = useMutation({
    mutationFn: (slug: string) => api.post(`/owner/plugins/global/${slug}/reconcile`),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["owner-global-plugins"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const setPluginStatus = useMutation({
    mutationFn: ({ slug, action }: { slug: string; action: "approve" | "block" }) =>
      api.post(`/owner/plugins/${slug}/${action}`),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["owner-plugin-overview"] });
      void queryClient.invalidateQueries({ queryKey: ["owner-audit"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const capabilities = useQuery({
    queryKey: ["owner-capabilities"],
    queryFn: () => api.get<CapabilityState[]>("/owner/capabilities"),
    enabled: tab === "capabilities",
  });

  const setCapability = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.post(`/owner/capabilities/${name}?enabled=${enabled}`),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["owner-capabilities"] });
      void queryClient.invalidateQueries({ queryKey: ["owner-audit"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const setRole = useMutation({
    mutationFn: ({ id, role }: { id: number; role: string }) => api.post(`/owner/users/${id}/role?role=${role}`),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["owner-users"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.post(`/owner/users/${id}/status?status=${status}`),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["owner-users"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const maintenance = useMutation({
    mutationFn: (enabled: boolean) => api.post<{ maintenance_mode: boolean }>(`/owner/maintenance?enabled=${enabled}`),
    onSuccess: () => setError(null),
    onError: (err: Error) => setError(err.message),
  });

  if (overview.isError) {
    return <ErrorNote message="The Owner Control Center is restricted to platform owners." onRetry={() => overview.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Owner Control Center"
        description="Platform-wide administration. Every action requires the owner role, enforced by the API — never by this page."
      />
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Users" value={overview.data?.users ?? "…"} icon={<Users className="h-4 w-4" />} />
        <Stat label="Bots" value={overview.data?.bots ?? "…"} hint={`${overview.data?.running_bots ?? 0} running`} icon={<Bot className="h-4 w-4" />} />
        <Stat label="Plugin installs" value={overview.data?.plugin_installations ?? "…"} />
        <Stat label="Runtime errors" value={overview.data?.runtime_errors ?? "…"} icon={<ShieldAlert className="h-4 w-4" />} />
        <Stat label="Workers" value={overview.data?.workers.length ?? "…"} hint={overview.data?.workers.map((worker) => worker.name).join(", ")} />
      </div>

      <Tabs
        tabs={[
          { id: "overview" as Tab, label: "Overview" },
          { id: "users" as Tab, label: "Users" },
          { id: "bots" as Tab, label: "All bots" },
          { id: "plugins" as Tab, label: "Plugins", count: pluginOverview.data?.totals.catalogue },
          { id: "capabilities" as Tab, label: "Capabilities" },
          { id: "audit" as Tab, label: "Audit log" },
          { id: "security" as Tab, label: "Security" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "overview" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <p className="flex items-center gap-2 text-sm font-medium text-white">
              <Wrench className="h-4 w-4 text-amber-300" /> Maintenance mode
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Blocks every non-health endpoint with a 503 while migrations or restores run.
            </p>
            <div className="mt-3 flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2.5">
              <span className="text-sm text-slate-300">Engage maintenance</span>
              <Toggle checked={false} onChange={(next) => maintenance.mutate(next)} label="maintenance" />
            </div>
            {maintenance.data ? (
              <p className="mt-2 text-xs text-emerald-300">
                maintenance_mode = {String(maintenance.data.maintenance_mode)}
              </p>
            ) : null}
          </Card>

          <Card>
            <p className="flex items-center gap-2 text-sm font-medium text-white">
              <Crown className="h-4 w-4 text-amber-300" /> Worker fleet
            </p>
            {overview.data?.workers.length ? (
              <ul className="mt-3 space-y-1.5">
                {overview.data.workers.map((worker) => (
                  <li key={worker.id} className="flex items-center justify-between rounded-lg border border-white/[0.05] px-3 py-2 text-sm">
                    <span className="text-slate-200">{worker.name}</span>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-slate-500">{worker.load}</span>
                      <Badge status={worker.status === "online" ? "running" : worker.status}>{worker.status}</Badge>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-slate-500">No workers registered.</p>
            )}
          </Card>
        </div>
      ) : null}

      {tab === "users" ? (
        users.isLoading ? (
          <Skeleton className="h-48" />
        ) : users.data?.length ? (
          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Telegram</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.data.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <p className="text-slate-100">{user.first_name || "unnamed"}</p>
                      <p className="text-[11px] text-slate-500">#{user.id}</p>
                    </td>
                    <td className="font-mono text-xs">{user.telegram_id ?? "—"}</td>
                    <td>
                      <select
                        className="input max-w-[120px] py-1 text-xs"
                        value={user.is_owner ? "owner" : user.role}
                        onChange={(event) => setRole.mutate({ id: user.id, role: event.target.value })}
                        disabled={user.is_owner}
                      >
                        <option value="user">user</option>
                        <option value="admin">admin</option>
                        <option value="owner">owner</option>
                      </select>
                    </td>
                    <td>
                      <Badge status={user.status}>{user.status}</Badge>
                    </td>
                    <td className="text-xs text-slate-500">{formatDate(user.last_seen_at)}</td>
                    <td className="text-right">
                      <button
                        className={user.status === "active" ? "btn-danger px-2 py-1" : "btn-ghost px-2 py-1"}
                        onClick={() => setStatus.mutate({ id: user.id, status: user.status === "active" ? "restricted" : "active" })}
                      >
                        {user.status === "active" ? "Restrict" : "Restore"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState title="No users" description="Users are created on first Telegram login." />
        )
      ) : null}

      {tab === "bots" ? (
        bots.isLoading ? (
          <Skeleton className="h-48" />
        ) : bots.data?.length ? (
          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Bot</th>
                  <th>Owner</th>
                  <th>Status</th>
                  <th>Token</th>
                  <th>Worker</th>
                  <th>Last error</th>
                </tr>
              </thead>
              <tbody>
                {bots.data.map((bot) => (
                  <tr key={bot.id}>
                    <td>
                      <p className="text-slate-100">{bot.name}</p>
                      <p className="text-[11px] text-slate-500">
                        {bot.username ? `@${bot.username}` : `#${bot.id}`}
                        {bot.is_clone ? " · clone" : ""}
                      </p>
                    </td>
                    <td className="text-xs">#{bot.owner_id}</td>
                    <td>
                      <Badge status={bot.status}>{bot.status}</Badge>
                    </td>
                    <td className="text-xs">{bot.token_valid ? "verified" : "unverified"}</td>
                    <td className="font-mono text-xs">{bot.worker_id ?? "—"}</td>
                    <td className="max-w-[260px] truncate text-xs text-rose-300/80">{bot.last_error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState title="No bots on the platform" description="Bots appear here once an operator connects one." icon={<Bot className="h-6 w-6" />} />
        )
      ) : null}

      {tab === "audit" ? (
        audit.isLoading ? (
          <Skeleton className="h-48" />
        ) : audit.data?.length ? (
          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Actor</th>
                  <th>Target</th>
                  <th>IP</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {audit.data.map((row) => (
                  <tr key={row.id}>
                    <td className="font-mono text-xs text-violet-200">{row.action}</td>
                    <td className="font-mono text-xs">
                      {row.actor_user_id ?? "system"}
                      <span className="ml-1 text-slate-600">({row.actor_type})</span>
                    </td>
                    <td className="text-xs">
                      {row.target_type ?? "—"}
                      {row.target_id ? ` #${row.target_id}` : ""}
                    </td>
                    <td className="font-mono text-xs text-slate-500">{row.ip_address ?? "—"}</td>
                    <td className="text-xs text-slate-500">{formatDate(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState title="Audit log empty" description="Every privileged mutation is recorded here." />
        )
      ) : null}

      {tab === "plugins" ? (
        <div className="space-y-4">
          <Card className="border-amber-500/25 bg-amber-500/[0.04]">
            <p className="text-sm font-medium text-amber-100">Global scope is yours alone</p>
            <p className="mt-1 text-xs text-amber-100/70">
              A global installation applies to every bot unless that bot has its own row. The API rejects global
              installs from anyone but a platform owner, so this screen is the only place they can be managed.
              Reconciling pushes the current state to running bots immediately instead of waiting for a restart.
            </p>
          </Card>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Catalogue" value={pluginOverview.data?.totals.catalogue ?? "…"} />
            <Stat label="Implemented" value={pluginOverview.data?.totals.implemented ?? "…"} hint="runtime code present" />
            <Stat label="Global installs" value={pluginOverview.data?.totals.global_installations ?? "…"} />
            <Stat label="Blocked" value={pluginOverview.data?.totals.blocked ?? "…"} hint={`${pluginOverview.data?.totals.pending ?? 0} pending`} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <p className="text-sm font-medium text-white">Global activations</p>
              {globalPlugins.isLoading ? (
                <Skeleton className="mt-3 h-24" />
              ) : globalPlugins.data?.length ? (
                <ul className="mt-3 space-y-2">
                  {globalPlugins.data.map((row) => (
                    <li
                      key={row.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/[0.06] px-3 py-2"
                    >
                      <div>
                        <p className="text-sm text-slate-100">{row.name ?? row.slug}</p>
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                          v{row.version} · {row.state} · {row.running_somewhere ? "live on some bots" : "not loaded"}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge status={row.status}>{row.status}</Badge>
                        <button
                          className="btn-ghost px-2 py-1 text-xs"
                          onClick={() => row.slug && reconcileGlobal.mutate(row.slug)}
                          disabled={!row.slug || reconcileGlobal.isPending}
                          title="Reconcile this plugin on every running bot"
                        >
                          Reconcile
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-sm text-slate-500">
                  No global installations. Owners can install globally from the marketplace API
                  (<span className="kbd">POST /plugins/&#123;slug&#125;/install</span> with no bot id).
                </p>
              )}
            </Card>

            <Card>
              <p className="text-sm text-white">Pending permission changes</p>
              <p className="mt-1 text-xs text-slate-500">
                A plugin cannot grant itself a permission at runtime. When a manifest asks for more than the
                approved record, it shows up here for review.
              </p>
              <ul className="mt-3 space-y-1.5">
                {(pluginOverview.data?.plugins ?? [])
                  .filter((plugin) => plugin.pending_permission_changes.length || plugin.missing_permissions.length)
                  .map((plugin) => (
                    <li key={plugin.slug} className="rounded-lg border border-amber-500/20 px-3 py-2 text-xs">
                      <span className="text-slate-200">{plugin.name}</span>{" "}
                      <span className="text-amber-200">
                        +{(plugin.pending_permission_changes.length ? plugin.pending_permission_changes : plugin.missing_permissions).join(", ")}
                      </span>
                    </li>
                  ))}
              </ul>
              {!(pluginOverview.data?.plugins ?? []).some(
                (plugin) => plugin.pending_permission_changes.length || plugin.missing_permissions.length,
              ) ? (
                <p className="mt-3 text-sm text-slate-500">Every installed manifest matches its approved permissions.</p>
              ) : null}
            </Card>
          </div>

          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Plugin</th>
                  <th>Status</th>
                  <th>Runtime</th>
                  <th>Installations</th>
                  <th>Permissions</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(pluginOverview.data?.plugins ?? []).map((plugin) => (
                  <tr key={plugin.slug}>
                    <td>
                      <p className="text-sm text-slate-100">{plugin.name}</p>
                      <p className="font-mono text-[11px] text-slate-500">
                        {plugin.slug} · v{plugin.version} · {plugin.category}
                      </p>
                    </td>
                    <td>
                      <Badge status={plugin.status}>{plugin.status}</Badge>
                    </td>
                    <td className="text-xs text-slate-400">
                      {plugin.implemented ? "implemented" : "no implementation"}
                    </td>
                    <td className="font-mono text-xs text-slate-400">
                      G{plugin.installations.global} · B{plugin.installations.bot} · Gr
                      {plugin.installations.group}
                    </td>
                    <td className="text-xs text-slate-400">
                      {plugin.permissions.length ? plugin.permissions.join(", ") : "none"}
                    </td>
                    <td>
                      <div className="flex justify-end gap-1">
                        <button
                          className="btn-ghost px-2 py-1 text-xs"
                          onClick={() => setPluginStatus.mutate({ slug: plugin.slug, action: "approve" })}
                          disabled={plugin.status === "approved"}
                        >
                          Approve
                        </button>
                        <button
                          className="btn-danger px-2 py-1 text-xs"
                          onClick={() => setPluginStatus.mutate({ slug: plugin.slug, action: "block" })}
                          disabled={plugin.status === "blocked"}
                        >
                          Block
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}

      {tab === "capabilities" ? (
        capabilities.isLoading ? (
          <Skeleton className="h-48" />
        ) : (
          <div className="space-y-3">
            <Card className="border-amber-500/25 bg-amber-500/[0.04]">
              <p className="text-sm font-medium text-amber-100">Dangerous features are closed by default</p>
              <p className="mt-1 text-xs text-amber-100/70">
                A capability gate is enforced inside the plugin runtime, not only here. Opening one records an
                audit entry and can be narrowed to specific Telegram ids or bots.
              </p>
            </Card>
            {(capabilities.data ?? []).map((capability) => (
              <Card key={capability.capability} className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-white">{capability.label}</p>
                      <Badge status={capability.risk === "critical" ? "error" : capability.risk === "high" ? "warning" : "info"}>
                        {capability.risk}
                      </Badge>
                      <Badge status={capability.enabled ? "enabled" : "disabled"}>
                        {capability.enabled ? "open" : "closed"}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">{capability.description}</p>
                    <p className="mt-1 font-mono text-[11px] text-slate-600">
                      plugin {capability.plugin} · limit {capability.max_runs_per_day || "∞"} runs/day · updated{" "}
                      {capability.updated_at ? formatDate(capability.updated_at) : "never"}
                    </p>
                  </div>
                  <Toggle
                    checked={capability.enabled}
                    onChange={(next) => setCapability.mutate({ name: capability.capability, enabled: next })}
                    label={`toggle ${capability.label}`}
                  />
                </div>
                {capability.allowed_telegram_ids.length || capability.allowed_bot_ids.length ? (
                  <div className="flex flex-wrap gap-2 text-[11px] text-slate-400">
                    {capability.allowed_telegram_ids.length ? (
                      <span className="badge border-white/10 bg-white/[0.03]">
                        telegram: {capability.allowed_telegram_ids.join(", ")}
                      </span>
                    ) : null}
                    {capability.allowed_bot_ids.length ? (
                      <span className="badge border-white/10 bg-white/[0.03]">
                        bots: {capability.allowed_bot_ids.join(", ")}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </Card>
            ))}
          </div>
        )
      ) : null}

      {tab === "security" ? (
        security.isLoading ? (
          <Skeleton className="h-48" />
        ) : security.data?.length ? (
          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>IP</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {security.data.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <Badge status={event.action.includes("role") ? "warning" : "info"}>{event.action}</Badge>
                    </td>
                    <td className="font-mono text-xs">{event.actor_user_id ?? "system"}</td>
                    <td className="font-mono text-xs text-slate-500">{event.ip_address ?? "—"}</td>
                    <td className="text-xs text-slate-500">{formatDate(event.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState
            title="No security events in 7 days"
            description="Logins, role changes and status changes are recorded as security events."
            icon={<ShieldAlert className="h-6 w-6" />}
          />
        )
      ) : null}
    </div>
  );
}
