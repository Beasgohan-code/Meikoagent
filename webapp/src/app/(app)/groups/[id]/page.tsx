"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Save, ShieldCheck, Users } from "lucide-react";
import { api, formatDate, relativeTime } from "@/lib/api";
import type { Group, ModerationAction } from "@/lib/types";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  SectionHeader,
  Skeleton,
  Stat,
  Toggle,
} from "@/components/ui";
import { SearchInput, Tabs } from "@/components/patterns";

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

type Member = {
  id: number;
  telegram_id: number;
  username: string | null;
  first_name: string;
  is_admin: boolean;
  is_trusted: boolean;
  is_muted: boolean;
  warnings_count: number;
  messages_count: number;
  joined_at: string | null;
  note: string;
};

type Warning = { id: number; telegram_id: number; reason: string; created_at: string; moderator_id: number | null };

type Tab = "settings" | "members" | "history" | "warnings";

export default function GroupDetailPage() {
  const params = useParams<{ id: string }>();
  const groupId = Number(params.id);
  const [tab, setTab] = useState<Tab>("settings");
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const detail = useQuery({ queryKey: ["group", groupId], queryFn: () => api.get<GroupDetail>(`/groups/${groupId}`) });
  const members = useQuery({
    queryKey: ["group-members", groupId],
    queryFn: () => api.get<Member[]>(`/groups/${groupId}/members?limit=100`),
    enabled: tab === "members",
  });
  const history = useQuery({
    queryKey: ["group-moderation", groupId],
    queryFn: () => api.get<ModerationAction[]>(`/groups/${groupId}/moderation?limit=100`),
    enabled: tab === "history",
  });
  const warnings = useQuery({
    queryKey: ["group-warnings", groupId],
    queryFn: () => api.get<Warning[]>(`/groups/${groupId}/warnings`),
    enabled: tab === "warnings",
  });

  const [draft, setDraft] = useState<Partial<GroupDetail["settings"]>>({});
  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.patch(`/groups/${groupId}/settings`, payload),
    onSuccess: () => {
      setNotice("Settings saved");
      setDraft({});
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["group", groupId] });
    },
    onError: (err: Error) => setError(err.message),
  });

  if (detail.isError) {
    return (
      <div className="space-y-3">
        <Link href="/groups" className="btn-ghost">
          <ArrowLeft className="h-4 w-4" /> Groups
        </Link>
        <ErrorNote message="This group is unavailable or belongs to another account." onRetry={() => detail.refetch()} />
      </div>
    );
  }
  if (detail.isLoading || !detail.data) return <Skeleton className="h-64" />;

  const group = detail.data;
  const settings = { ...group.settings, ...draft };
  const set = <K extends keyof GroupDetail["settings"]>(key: K, value: GroupDetail["settings"][K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const dirty = Object.keys(draft).length > 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/groups" className="mb-2 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
            <ArrowLeft className="h-3 w-3" /> Groups
          </Link>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-white">{group.title || `chat ${group.chat_id}`}</h1>
            <Badge status={group.bot_status === "administrator" ? "enabled" : "installed"}>{group.bot_status}</Badge>
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {group.username ? `@${group.username} · ` : ""}
            <span className="font-mono">{group.chat_id}</span> · last activity {relativeTime(group.last_activity_at)}
          </p>
        </div>
        {dirty ? (
          <button className="btn-primary" onClick={() => save.mutate(draft)} disabled={save.isPending}>
            <Save className="h-4 w-4" /> Save changes
          </button>
        ) : null}
      </div>

      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      {notice ? (
        <p className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">{notice}</p>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Tracked members" value={group.tracked_members} icon={<Users className="h-4 w-4" />} />
        <Stat label="Moderation actions" value={group.moderation_actions} icon={<ShieldCheck className="h-4 w-4" />} />
        <Stat label="Verification" value={settings.verification_enabled ? "on" : "off"} hint="Captcha gate for new members" />
      </div>

      <Tabs
        tabs={[
          { id: "settings" as Tab, label: "Settings" },
          { id: "members" as Tab, label: "Members" },
          { id: "history" as Tab, label: "History" },
          { id: "warnings" as Tab, label: "Warnings" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "settings" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <p className="text-sm font-medium text-white">Welcome & goodbye</p>
            <p className="mb-3 text-xs text-slate-500">
              Templates support the same placeholders as the bot: <span className="kbd">{"{user}"}</span>,{" "}
              <span className="kbd">{"{chat}"}</span>.
            </p>
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2.5">
                <span className="text-sm text-slate-300">Welcome new members</span>
                <Toggle checked={settings.welcome_enabled} onChange={(next) => set("welcome_enabled", next)} />
              </div>
              <Field label="Welcome text">
                <textarea
                  className="input min-h-[80px]"
                  value={settings.welcome_text}
                  onChange={(event) => set("welcome_text", event.target.value)}
                  placeholder="Welcome {user} to {chat}!"
                />
              </Field>
              <div className="flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2.5">
                <span className="text-sm text-slate-300">Goodbye message</span>
                <Toggle checked={settings.goodbye_enabled} onChange={(next) => set("goodbye_enabled", next)} />
              </div>
              <Field label="Goodbye text">
                <textarea
                  className="input min-h-[60px]"
                  value={settings.goodbye_text}
                  onChange={(event) => set("goodbye_text", event.target.value)}
                />
              </Field>
            </div>
          </Card>

          <Card>
            <p className="text-sm font-medium text-white">Protection & logging</p>
            <div className="mt-3 space-y-3">
              <div className="flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2.5">
                <div>
                  <p className="text-sm text-slate-300">Verification gate</p>
                  <p className="text-xs text-slate-500">New members must confirm before speaking.</p>
                </div>
                <Toggle checked={settings.verification_enabled} onChange={(next) => set("verification_enabled", next)} />
              </div>
              <div className="flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2.5">
                <div>
                  <p className="text-sm text-slate-300">Admin event logging</p>
                  <p className="text-xs text-slate-500">Writes audit rows for joins, leaves and edits.</p>
                </div>
                <Toggle checked={settings.logging_enabled} onChange={(next) => set("logging_enabled", next)} />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Auto-delete (seconds)" hint="0 disables auto-deletion.">
                  <input
                    className="input"
                    type="number"
                    min={0}
                    max={86400}
                    value={settings.auto_delete_seconds}
                    onChange={(event) => set("auto_delete_seconds", Number(event.target.value))}
                  />
                </Field>
                <Field label="Slow mode (seconds)" hint="Applied through Telegram restrictions.">
                  <input
                    className="input"
                    type="number"
                    min={0}
                    max={3600}
                    value={settings.slow_mode_seconds}
                    onChange={(event) => set("slow_mode_seconds", Number(event.target.value))}
                  />
                </Field>
              </div>
              <Link href={`/moderation?tab=antispam&group=${group.id}`} className="btn-ghost w-full">
                Configure anti-spam
              </Link>
            </div>
          </Card>
        </div>
      ) : null}

      {tab === "members" ? <MembersPanel groupId={groupId} members={members.data} loading={members.isLoading} /> : null}

      {tab === "history" ? (
        history.data && history.data.length ? (
          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Target</th>
                  <th>Moderator</th>
                  <th>Reason</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <Badge status={row.action}>{row.action}</Badge>
                    </td>
                    <td className="font-mono text-xs">{row.target_id ?? "—"}</td>
                    <td className="font-mono text-xs">{row.moderator_id ?? "—"}</td>
                    <td className="max-w-[320px] truncate text-xs text-slate-400">{row.reason || "—"}</td>
                    <td className="text-xs text-slate-500">{formatDate(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState title="No moderation history" description="Actions taken in this group will be listed here." />
        )
      ) : null}

      {tab === "warnings" ? (
        warnings.data && warnings.data.length ? (
          <Card className="p-0">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Reason</th>
                  <th>Issued by</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {warnings.data.map((row) => (
                  <tr key={row.id}>
                    <td className="font-mono text-xs">{row.telegram_id}</td>
                    <td className="text-xs">{row.reason || "—"}</td>
                    <td className="font-mono text-xs">{row.moderator_id ?? "automation"}</td>
                    <td className="text-xs text-slate-500">{formatDate(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState title="No active warnings" description="Warnings issued by the bot or moderators appear here." />
        )
      ) : null}
    </div>
  );
}

function MembersPanel({ groupId, members, loading }: { groupId: number; members: Member[] | undefined; loading: boolean }) {
  const [search, setSearch] = useState("");
  if (loading) return <Skeleton className="h-48" />;
  const filtered = (members ?? []).filter((member) =>
    [member.first_name, member.username ?? "", String(member.telegram_id)].join(" ").toLowerCase().includes(search.toLowerCase()),
  );
  if (!members?.length) {
    return (
      <EmptyState
        title="No members tracked yet"
        description="Members are recorded as the bot observes messages, joins and moderation actions."
        icon={<Users className="h-6 w-6" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <div className="w-64">
        <SearchInput value={search} onChange={setSearch} placeholder="Search members…" />
      </div>
      <Card className="p-0">
        <table className="table-base">
          <thead>
            <tr>
              <th>Member</th>
              <th>Status</th>
              <th>Warnings</th>
              <th>Messages</th>
              <th>Joined</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((member) => (
              <tr key={member.id}>
                <td>
                  <p className="text-slate-100">{member.first_name || "unknown"}</p>
                  <p className="font-mono text-[11px] text-slate-500">
                    {member.username ? `@${member.username}` : member.telegram_id}
                  </p>
                </td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {member.is_admin ? <Badge status="enabled">admin</Badge> : null}
                    {member.is_trusted ? <Badge status="configured">trusted</Badge> : null}
                    {member.is_muted ? <Badge status="warning">muted</Badge> : null}
                    {!member.is_admin && !member.is_trusted && !member.is_muted ? (
                      <span className="text-xs text-slate-500">member</span>
                    ) : null}
                  </div>
                </td>
                <td>{member.warnings_count}</td>
                <td>{member.messages_count}</td>
                <td className="text-xs text-slate-500">{member.joined_at ? formatDate(member.joined_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <p className="text-xs text-slate-500">
        group <span className="font-mono">{groupId}</span> · member actions are performed by the bot runtime, not the browser
      </p>
    </div>
  );
}
