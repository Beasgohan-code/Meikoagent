"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import {
  AlertTriangle,
  Ban,
  Filter,
  Gavel,
  ListOrdered,
  MessageSquareWarning,
  Plus,
  Shield,
  Trash2,
} from "lucide-react";
import { api, formatDate } from "@/lib/api";
import type { Group, ModerationAction, Page, Bot } from "@/lib/types";
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
import { Tabs } from "@/components/patterns";

type Tab = "overview" | "antispam" | "rules" | "reports" | "appeals";

type GroupDetail = Group & {
  settings: {
    welcome_enabled: boolean;
    welcome_text: string;
    goodbye_enabled: boolean;
    goodbye_text: string;
    verification_enabled: boolean;
    auto_delete_seconds: number;
    slow_mode_seconds: number;
    antispam: Record<string, unknown>;
    moderation: Record<string, unknown>;
    logging_enabled: boolean;
  };
  tracked_members: number;
  moderation_actions: number;
};

type Rule = {
  id: number;
  bot_id: number;
  group_id: number | null;
  name: string;
  event: string;
  conditions: Record<string, unknown>[];
  actions: Record<string, unknown>[];
  priority: number;
  enabled: boolean;
};

export default function ModerationPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModerationContent />
    </Suspense>
  );
}

function ModerationContent() {
  const params = useSearchParams();
  const initial = (params.get("tab") as Tab) || "overview";
  const [tab, setTab] = useState<Tab>(initial);
  const [groupFilter, setGroupFilter] = useState<string>(params.get("group") ?? "");

  const groups = useQuery({ queryKey: ["groups"], queryFn: () => api.get<Group[]>("/groups") });
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const appeals = useQuery({ queryKey: ["appeals"], queryFn: () => api.get<Record<string, unknown>[]>("/groups/appeals/all") });

  const groupId = Number(groupFilter) || groups.data?.[0]?.id;
  const detail = useQuery({
    queryKey: ["group", groupId],
    queryFn: () => api.get<GroupDetail>(`/groups/${groupId}`),
    enabled: Boolean(groupId),
  });
  const history = useQuery({
    queryKey: ["group-moderation", groupId],
    queryFn: () => api.get<ModerationAction[]>(`/groups/${groupId}/moderation?limit=50`),
    enabled: Boolean(groupId),
  });
  const rules = useQuery({
    queryKey: ["group-rules", groupId],
    queryFn: () => api.get<Rule[]>(`/groups/${groupId}/rules`),
    enabled: Boolean(groupId),
  });
  const reports = useQuery({
    queryKey: ["group-reports", groupId],
    queryFn: () => api.get<Record<string, unknown>[]>(`/groups/${groupId}/reports`),
    enabled: Boolean(groupId),
  });

  if (groups.isError) {
    return <ErrorNote message="Could not load groups." onRetry={() => groups.refetch()} />;
  }

  if (groups.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!groups.data?.length) {
    return (
      <div className="space-y-4">
        <SectionHeader
          title="Moderation Center"
          description="Warnings, mutes, bans, anti-spam and the rule engine — all persisted with an audit trail."
        />
        <EmptyState
          title="No groups connected"
          description="Moderation is per group. Add a bot to a Telegram group, then configure its protections here."
          icon={<Shield className="h-6 w-6" />}
          action={
            <Link href="/bots" className="btn-primary">
              Manage bots
            </Link>
          }
        />
      </div>
    );
  }

  const actions = history.data ?? [];
  const byAction = actions.reduce<Record<string, number>>((acc, item) => {
    acc[item.action] = (acc[item.action] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Moderation Center"
        description="Every action is written to the audit journal and mirrored to the activity feed."
        action={
          <select className="input max-w-[260px]" value={String(groupId ?? "")} onChange={(event) => setGroupFilter(event.target.value)}>
            {groups.data.map((group) => (
              <option key={group.id} value={group.id}>
                {group.title}
              </option>
            ))}
          </select>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Tracked members" value={detail.data?.tracked_members ?? "…"} icon={<Shield className="h-4 w-4" />} />
        <Stat label="Moderation actions" value={detail.data?.moderation_actions ?? "…"} icon={<Gavel className="h-4 w-4" />} />
        <Stat label="Active rules" value={rules.data?.filter((rule) => rule.enabled).length ?? "…"} icon={<Filter className="h-4 w-4" />} />
        <Stat
          label="Open reports"
          value={reports.data?.filter((report) => report.status === "open").length ?? "…"}
          icon={<MessageSquareWarning className="h-4 w-4" />}
        />
      </div>

      <Tabs
        tabs={[
          { id: "overview" as Tab, label: "Overview" },
          { id: "antispam" as Tab, label: "Anti-spam" },
          { id: "rules" as Tab, label: "Rules" },
          { id: "reports" as Tab, label: "Reports", count: reports.data?.length },
          { id: "appeals" as Tab, label: "Appeals", count: appeals.data?.length },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "overview" ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium text-white">Recent actions</p>
              <span className="text-xs text-slate-500">latest 50</span>
            </div>
            {byAction && Object.keys(byAction).length ? (
              <div className="mb-3 flex flex-wrap gap-1.5">
                {Object.entries(byAction).map(([action, count]) => (
                  <span key={action} className="badge border-white/10 bg-white/[0.03] text-slate-300">
                    {action} · {count}
                  </span>
                ))}
              </div>
            ) : null}
            {history.isLoading ? (
              <Skeleton className="h-48" />
            ) : actions.length ? (
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>Action</th>
                      <th>Target</th>
                      <th>Reason</th>
                      <th>Source</th>
                      <th>When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {actions.map((action) => (
                      <tr key={action.id}>
                        <td>
                          <Badge status={action.action === "ban" ? "error" : action.action === "warn" ? "warning" : "info"}>
                            {action.action}
                          </Badge>
                        </td>
                        <td className="font-mono text-xs">{action.target_id ?? "—"}</td>
                        <td className="max-w-[280px] truncate text-xs text-slate-400">{action.reason || "—"}</td>
                        <td className="text-xs text-slate-500">{action.source}</td>
                        <td className="text-xs text-slate-500">{formatDate(action.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="No moderation actions recorded"
                description="Actions appear here when a moderator or an anti-spam rule takes one in this group."
                icon={<Gavel className="h-6 w-6" />}
              />
            )}
          </Card>

          <Card>
            <p className="text-sm font-medium text-white">Group protections</p>
            <p className="text-xs text-slate-500">Changes apply to the live runtime immediately.</p>
            <div className="mt-4 space-y-3">
              <SettingToggle groupId={groupId!} field="welcome_enabled" label="Welcome message" value={detail.data?.settings.welcome_enabled ?? false} />
              <SettingToggle groupId={groupId!} field="goodbye_enabled" label="Goodbye message" value={detail.data?.settings.goodbye_enabled ?? false} />
              <SettingToggle groupId={groupId!} field="verification_enabled" label="Member verification" value={detail.data?.settings.verification_enabled ?? false} />
              <SettingToggle groupId={groupId!} field="logging_enabled" label="Admin event logging" value={detail.data?.settings.logging_enabled ?? false} />
            </div>
            <Link href={`/groups/${groupId}`} className="btn-ghost mt-4 w-full">
              Full group settings
            </Link>
          </Card>
        </div>
      ) : null}

      {tab === "antispam" ? <AntiSpamPanel groupId={groupId!} detail={detail.data} /> : null}
      {tab === "rules" ? <RulesPanel groupId={groupId!} rules={rules.data ?? []} /> : null}
      {tab === "reports" ? <ReportsPanel reports={reports.data ?? []} groupId={groupId!} /> : null}
      {tab === "appeals" ? (
        <Card className="p-0">
          {appeals.data && appeals.data.length ? (
            <table className="table-base">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Message</th>
                  <th>Status</th>
                  <th>Decision</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {appeals.data.map((appeal, index) => (
                  <tr key={String(appeal.id ?? index)}>
                    <td className="font-mono text-xs">{String(appeal.telegram_id ?? "—")}</td>
                    <td className="max-w-[320px] truncate text-xs">{String(appeal.message ?? "")}</td>
                    <td>
                      <Badge status={String(appeal.status)}>{String(appeal.status)}</Badge>
                    </td>
                    <td className="text-xs text-slate-400">{String(appeal.decision || "—")}</td>
                    <td className="text-xs text-slate-500">{formatDate(String(appeal.created_at ?? ""))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              title="No appeals submitted"
              description="Members can appeal a ban; appeals land here with the bot's decision record."
              icon={<AlertTriangle className="h-6 w-6" />}
            />
          )}
        </Card>
      ) : null}
    </div>
  );
}

function SettingToggle({
  groupId,
  field,
  label,
  value,
}: {
  groupId: number;
  field: string;
  label: string;
  value: boolean;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (next: boolean) => api.patch(`/groups/${groupId}/settings`, { [field]: next }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["group", groupId] });
    },
    onError: (err: Error) => setError(err.message),
  });
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-300">{label}</span>
        <Toggle checked={value} onChange={(next) => mutation.mutate(next)} label={label} />
      </div>
      {error ? <p className="mt-1 text-xs text-rose-400">{error}</p> : null}
    </div>
  );
}

function AntiSpamPanel({ groupId, detail }: { groupId: number; detail: GroupDetail | undefined }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const policy = (detail?.settings.antispam ?? {}) as Record<string, unknown>;
  const mutation = useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.patch(`/groups/${groupId}/settings`, { antispam: patch }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["group", groupId] });
    },
    onError: (err: Error) => setError(err.message),
  });
  const update = (key: string, value: unknown) => mutation.mutate({ ...policy, [key]: value });

  const detectors = [
    { key: "flood_enabled", label: "Flood protection", description: "Too many messages in a short window." },
    { key: "duplicate_enabled", label: "Duplicate messages", description: "Repeated identical messages from one member." },
    { key: "links_enabled", label: "Link filter", description: "Blocks URLs from untrusted members." },
    { key: "invites_enabled", label: "Invite filter", description: "Blocks t.me invite links and other chat links." },
    { key: "mentions_enabled", label: "Mass mention limit", description: "Escalates when a message mentions too many users." },
    { key: "keywords_enabled", label: "Keyword filter", description: "Matches the blocked keyword list below." },
    { key: "caps_enabled", label: "Caps lock filter", description: "Flags messages above the caps threshold." },
    { key: "emoji_enabled", label: "Emoji flood filter", description: "Flags messages with too many emojis." },
    { key: "suspicious_join_enabled", label: "New account joins", description: "Flags accounts younger than the threshold." },
  ];

  const numbers = [
    { key: "flood_messages", label: "Messages / window" },
    { key: "flood_window_seconds", label: "Window (seconds)" },
    { key: "duplicate_threshold", label: "Duplicate threshold" },
    { key: "max_mentions", label: "Max mentions" },
    { key: "caps_percent", label: "Caps percent" },
    { key: "max_emoji", label: "Max emoji" },
    { key: "warn_threshold", label: "Warnings before mute" },
    { key: "mute_seconds", label: "Mute duration (s)" },
    { key: "new_account_seconds", label: "New account age (s)" },
  ];

  const keywords = Array.isArray(policy.blocked_keywords) ? (policy.blocked_keywords as string[]) : [];

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <Card>
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-violet-300" />
          <p className="text-sm font-medium text-white">Anti-spam detectors</p>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Stored per group in <span className="kbd">group_settings.antispam</span> and evaluated by the rule engine
          before any configured rule runs.
        </p>
        <div className="mt-4 grid gap-2 md:grid-cols-2">
          {detectors.map((detector) => (
            <div key={detector.key} className="flex items-start justify-between gap-4 rounded-xl border border-white/[0.05] px-3 py-2.5">
              <div>
                <p className="text-sm text-slate-200">{detector.label}</p>
                <p className="text-xs text-slate-500">{detector.description}</p>
              </div>
              <Toggle checked={Boolean(policy[detector.key])} onChange={(next) => update(detector.key, next)} label={detector.label} />
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <p className="text-sm font-medium text-white">Thresholds</p>
          <p className="mb-3 text-xs text-slate-500">Applied instantly; blanks keep the stored value.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {numbers.map((field) => (
              <Field key={field.key} label={field.label}>
                <input
                  className="input"
                  type="number"
                  min={0}
                  defaultValue={Number(policy[field.key] ?? 0)}
                  onBlur={(event) => {
                    const value = Number(event.target.value);
                    if (Number.isFinite(value) && value !== Number(policy[field.key] ?? 0)) update(field.key, value);
                  }}
                />
              </Field>
            ))}
          </div>
        </Card>

        <Card>
          <p className="text-sm font-medium text-white">Enforcement</p>
          <div className="mt-3 space-y-3">
            <Field label="Action when a detector fires">
              <select className="input" value={String(policy.action ?? "warn")} onChange={(event) => update("action", event.target.value)}>
                <option value="delete">delete</option>
                <option value="warn">warn</option>
                <option value="mute">mute</option>
                <option value="kick">kick</option>
                <option value="ban">ban</option>
              </select>
            </Field>
            <Field label="Allowed links for trusted members">
              <select className="input" value={policy.allow_links ? "yes" : "no"} onChange={(event) => update("allow_links", event.target.value === "yes")}>
                <option value="yes">allow</option>
                <option value="no">block</option>
              </select>
            </Field>
            <Field label="Blocked keywords (comma separated)">
              <input
                className="input"
                defaultValue={keywords.join(", ")}
                key={keywords.join(",")}
                onBlur={(event) =>
                  update(
                    "blocked_keywords",
                    event.target.value
                      .split(",")
                      .map((word) => word.trim())
                      .filter(Boolean),
                  )
                }
              />
            </Field>
          </div>
        </Card>
      </div>
    </div>
  );
}

function RulesPanel({ groupId, rules }: { groupId: number; rules: Rule[] }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    event: "message",
    fact: "text",
    op: "contains",
    value: "",
    action: "delete",
    reason: "",
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Rule>(`/groups/${groupId}/rules`, {
        name: form.name,
        event: form.event,
        conditions: form.fact && form.op ? [{ fact: form.fact, op: form.op, value: form.value }] : [],
        actions: [{ type: form.action, reason: form.reason || undefined }],
        priority: 100,
        enabled: true,
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      setForm({ ...form, name: "", value: "", reason: "" });
      void queryClient.invalidateQueries({ queryKey: ["group-rules", groupId] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/groups/${groupId}/rules/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["group-rules", groupId] }),
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-400">
          Rules follow <span className="text-slate-200">EVENT → CONDITION → ACTION → AUDIT</span>.
        </p>
        <button className="btn-primary" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> New rule
        </button>
      </div>

      {rules.length ? (
        <div className="space-y-2">
          {rules.map((rule) => (
            <Card key={rule.id} className="flex flex-wrap items-center gap-3 py-3">
              <ListOrdered className="h-4 w-4 text-violet-300" />
              <div className="min-w-0 flex-1">
                <p className="text-sm text-slate-100">{rule.name}</p>
                <p className="text-xs text-slate-500">
                  on <span className="font-mono">{rule.event}</span> ·{" "}
                  {rule.conditions.map((condition) => `${condition.fact} ${condition.op} ${condition.value ?? ""}`).join(", ") || "no condition"}{" "}
                  → {rule.actions.map((action) => String(action.type ?? action.action ?? "action")).join(", ")}
                </p>
              </div>
              <Badge status={rule.enabled ? "enabled" : "disabled"}>{rule.priority}</Badge>
              <button className="btn-danger" onClick={() => remove.mutate(rule.id)} aria-label="Delete rule">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No rules for this group"
          description="Rules are validated server-side; only known facts, operators and actions are accepted."
          icon={<Filter className="h-6 w-6" />}
        />
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Create moderation rule"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending || !form.name}>
              Create rule
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Rule name">
            <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Block crypto spam" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Event">
              <select className="input" value={form.event} onChange={(event) => setForm({ ...form, event: event.target.value })}>
                <option value="message">message</option>
                <option value="edited_message">edited_message</option>
                <option value="join">join</option>
                <option value="leave">leave</option>
                <option value="callback">callback</option>
                <option value="reaction">reaction</option>
                <option value="command">command</option>
                <option value="widget_query">widget_query</option>
                <option value="guest_message">guest_message</option>
                <option value="*">* (any event)</option>
              </select>
            </Field>
            <Field label="Fact">
              <select className="input" value={form.fact} onChange={(event) => setForm({ ...form, fact: event.target.value })}>
                <option value="message.text">message.text</option>
                <option value="message.length">message.length</option>
                <option value="message.has_link">message.has_link</option>
                <option value="message.has_invite">message.has_invite</option>
                <option value="message.has_forward">message.has_forward</option>
                <option value="message.has_media">message.has_media</option>
                <option value="message.mentions">message.mentions</option>
                <option value="message.emoji_count">message.emoji_count</option>
                <option value="user.is_new_member">user.is_new_member</option>
                <option value="user.is_trusted">user.is_trusted</option>
                <option value="user.username">user.username</option>
              </select>
            </Field>
            <Field label="Operator">
              <select className="input" value={form.op} onChange={(event) => setForm({ ...form, op: event.target.value })}>
                <option value="contains">contains</option>
                <option value="equals">equals</option>
                <option value="regex">regex</option>
                <option value="startswith">starts with</option>
                <option value="endswith">ends with</option>
                <option value="not_contains">does not contain</option>
                <option value="gt">greater than</option>
                <option value="lt">less than</option>
                <option value="is_true">is true</option>
                <option value="is_false">is false</option>
              </select>
            </Field>
            <Field label="Value">
              <input className="input" value={form.value} onChange={(event) => setForm({ ...form, value: event.target.value })} />
            </Field>
            <Field label="Action">
              <select className="input" value={form.action} onChange={(event) => setForm({ ...form, action: event.target.value })}>
                <option value="delete">delete message</option>
                <option value="warn">warn</option>
                <option value="mute">mute</option>
                <option value="kick">kick</option>
                <option value="ban">ban</option>
                <option value="purge">purge</option>
                <option value="notify_admin">notify admins</option>
                <option value="reply">reply</option>
                <option value="escalate">escalate</option>
                <option value="log">log only</option>
              </select>
            </Field>
            <Field label="Reason">
              <input className="input" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} />
            </Field>
          </div>
          <p className="text-xs text-slate-500">
            Every rule execution writes a moderation row and an audit entry, so the trail is complete.
          </p>
        </div>
      </Modal>
    </div>
  );
}

function ReportsPanel({ reports, groupId }: { reports: Record<string, unknown>[]; groupId: number }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const resolve = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api.patch(`/groups/${groupId}/reports/${id}?status_value=${status}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["group-reports", groupId] }),
    onError: (err: Error) => setError(err.message),
  });

  const counts = useMemo(
    () => reports.reduce<Record<string, number>>((acc, row) => {
      const status = String(row.status ?? "open");
      acc[status] = (acc[status] ?? 0) + 1;
      return acc;
    }, {}),
    [reports],
  );

  if (!reports.length) {
    return (
      <EmptyState
        title="No reports"
        description="Members can report a message with /report; reports appear here for triage."
        icon={<MessageSquareWarning className="h-6 w-6" />}
      />
    );
  }

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <div className="flex flex-wrap gap-2 text-xs text-slate-400">
        {Object.entries(counts).map(([status, count]) => (
          <span key={status} className="badge border-white/10 bg-white/[0.03]">
            {status}: {count}
          </span>
        ))}
      </div>
      <Card className="p-0">
        <table className="table-base">
          <thead>
            <tr>
              <th>Reporter</th>
              <th>Target</th>
              <th>Reason</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {reports.map((report) => (
              <tr key={String(report.id)}>
                <td className="font-mono text-xs">{String(report.reporter_id ?? "—")}</td>
                <td className="font-mono text-xs">{String(report.target_id ?? "—")}</td>
                <td className="max-w-[300px] truncate text-xs">{String(report.reason ?? "")}</td>
                <td>
                  <Badge status={String(report.status)}>{String(report.status)}</Badge>
                </td>
                <td className="text-right">
                  <div className="inline-flex gap-1">
                    <button
                      className="btn-ghost px-2 py-1 text-xs"
                      onClick={() => resolve.mutate({ id: Number(report.id), status: "resolved" })}
                    >
                      Resolve
                    </button>
                    <button
                      className="btn-ghost px-2 py-1 text-xs"
                      onClick={() => resolve.mutate({ id: Number(report.id), status: "dismissed" })}
                    >
                      Dismiss
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <p className="flex items-center gap-1.5 text-xs text-slate-500">
        <Ban className="h-3.5 w-3.5" /> Resolving a report records who acted and when.
      </p>
    </div>
  );
}
