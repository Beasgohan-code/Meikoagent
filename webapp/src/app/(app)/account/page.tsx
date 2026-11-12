"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { LogOut, ShieldCheck, User as UserIcon } from "lucide-react";
import { api, formatDate } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Badge, Card, ErrorNote, SectionHeader, Skeleton, Stat } from "@/components/ui";
import { initials } from "@/lib/utils";

type Usage = { totals: Record<string, number>; bots: number; clones: number; note: string };

export default function AccountPage() {
  const { user, signOut, refresh } = useSession();
  const [error, setError] = useState<string | null>(null);
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.get<Usage>("/usage") });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.get<Record<string, unknown>[]>("/auth/sessions") });

  const logoutAll = useMutation({
    mutationFn: () => api.post<{ revoked: number }>("/auth/logout-all"),
    onSuccess: async () => {
      await refresh();
      await signOut();
    },
    onError: (err: Error) => setError(err.message),
  });

  if (!user) return <Skeleton className="h-64" />;

  return (
    <div className="space-y-4">
      <SectionHeader title="Account" description="Identity comes from Telegram; roles and status are only ever changed by an owner." />
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-center gap-4">
            <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-violet-500/30 to-indigo-500/20 text-lg font-semibold text-violet-100">
              {initials(user.first_name || user.username || "Meiko")}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <p className="text-lg font-semibold text-white">{user.first_name || "Operator"}</p>
                <Badge status={user.is_owner ? "configured" : "installed"}>{user.is_owner ? "owner" : user.role}</Badge>
                <Badge status={user.status}>{user.status}</Badge>
              </div>
              <p className="text-sm text-slate-500">
                {user.username ? `@${user.username}` : "no username"} · telegram id{" "}
                <span className="font-mono">{user.telegram_id ?? "—"}</span>
              </p>
            </div>
          </div>

          <dl className="mt-5 grid gap-2 sm:grid-cols-2">
            {[
              ["Account id", String(user.id)],
              ["Created", formatDate(user.created_at)],
              ["Last seen", formatDate(user.last_seen_at)],
              ["Role source", "database row (never the browser)"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-white/[0.06] px-3 py-2">
                <dt className="text-[11px] uppercase tracking-wider text-slate-500">{label}</dt>
                <dd className="mt-0.5 text-sm text-slate-200">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card>
          <p className="flex items-center gap-2 text-sm font-medium text-white">
            <ShieldCheck className="h-4 w-4 text-emerald-300" /> Security
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Sessions are bearer tokens stored as hashes with an expiry. Revoking signs every device out.
          </p>
          <div className="mt-3 space-y-2">
            <button className="btn-danger w-full" onClick={() => logoutAll.mutate()} disabled={logoutAll.isPending}>
              <LogOut className="h-4 w-4" /> Revoke all sessions
            </button>
            <button className="btn-ghost w-full" onClick={() => void signOut()}>
              Sign out of this device
            </button>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Bots owned" value={usage.data?.bots ?? "…"} icon={<UserIcon className="h-4 w-4" />} />
        <Stat label="Clones" value={usage.data?.clones ?? "…"} />
        <Stat label="Active sessions" value={sessions.data?.filter((session) => !session.revoked).length ?? "…"} />
      </div>

      <Card className="p-0">
        <div className="px-4 pt-4">
          <p className="text-sm font-medium text-white">Sessions</p>
          <p className="text-xs text-slate-500">Sorted by creation. Revoked sessions stay listed for the audit trail.</p>
        </div>
        {sessions.data?.length ? (
          <table className="table-base mt-2">
            <thead>
              <tr>
                <th>Session</th>
                <th>Created</th>
                <th>Expires</th>
                <th>Client</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {sessions.data.map((session, index) => (
                <tr key={String(session.id ?? index)}>
                  <td className="font-mono text-xs">#{String(session.id)}</td>
                  <td className="text-xs">{formatDate(String(session.created_at ?? ""))}</td>
                  <td className="text-xs">{formatDate(String(session.expires_at ?? ""))}</td>
                  <td className="max-w-[260px] truncate text-xs text-slate-500">
                    {String(session.user_agent ?? "unknown")}
                  </td>
                  <td>{session.revoked ? <Badge status="error">revoked</Badge> : <Badge status="ok">active</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="px-4 py-6 text-sm text-slate-500">No sessions recorded.</p>
        )}
      </Card>
    </div>
  );
}
