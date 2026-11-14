"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { Ban, ShieldCheck, UserPlus } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Field, Skeleton, Toggle } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { ShopPicker, useShop } from "@/components/filestore";
import { filestore } from "@/lib/modules";

const MESSAGE_KEYS = ["start", "caption", "no_access", "force_sub"] as const;

export default function FileStoreSettingsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="filestore" title="Bot settings" description="Messages, force-subscription, admins and bans — isolated per shop.">
        {({ botId }) => <Settings botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Settings({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const { shops, shop, slug, setSlug, loading, error } = useShop(botId);
  const detail = useQuery({ queryKey: ["filestore-detail", slug], queryFn: () => filestore.detail(slug), enabled: Boolean(slug) });
  const people = useQuery({ queryKey: ["filestore-users", slug], queryFn: () => filestore.users(slug), enabled: Boolean(slug) });
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [userId, setUserId] = useState("");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!detail.data) return;
    const record = detail.data.shop as Record<string, unknown>;
    setMessages((record.messages as Record<string, string>) ?? {});
    setSettings((record.settings as Record<string, unknown>) ?? {});
  }, [detail.data]);

  const save = useMutation({
    mutationFn: () => filestore.settings(slug, settings, messages),
    onSuccess: () => {
      setNote("Saved.");
      void queryClient.invalidateQueries({ queryKey: ["filestore-detail", slug] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  const admin = useMutation({
    mutationFn: ({ id, action }: { id: number; action: string }) => filestore.admin(slug, id, action),
    onSuccess: () => {
      setUserId("");
      void queryClient.invalidateQueries({ queryKey: ["filestore-users", slug] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  if (loading) return <Skeleton className="h-64" />;
  if (error) return <ErrorNote message={error.message} />;
  if (shops.length === 0) {
    return <EmptyState title="No FileStore" description="Create a shop before configuring it." action={<Link href="/filestore" className="btn-primary">My bots</Link>} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <ShopPicker shops={shops} value={slug} onChange={setSlug} />
      </div>
      {note ? <p className="text-xs text-slate-400">{note}</p> : null}

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Messages</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {MESSAGE_KEYS.map((key) => (
            <Field key={key} label={key.replace("_", " ")}>
              <textarea
                className="input h-20 text-xs"
                value={messages[key] ?? ""}
                placeholder={key === "start" ? "Welcome to {shop}!" : ""}
                onChange={(event) => setMessages({ ...messages, [key]: event.target.value })}
              />
            </Field>
          ))}
        </div>
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Behaviour</h3>
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Toggle checked={Boolean(settings.allow_public)} onChange={(value) => setSettings({ ...settings, allow_public: value })} label="allow public files" />
            <span className="text-xs text-slate-400">Let owners publish a file to everyone</span>
          </div>
          <div className="flex items-center gap-2">
            <Toggle checked={Boolean(settings.require_force_sub)} onChange={(value) => setSettings({ ...settings, require_force_sub: value })} label="force subscription" />
            <span className="text-xs text-slate-400">Require joining the configured chats before downloads</span>
          </div>
          <Field label="Default TTL (days)" hint="0 keeps files forever">
            <input className="input max-w-[160px]" type="number" min={0}
              value={Number(settings.default_ttl_days ?? 30)}
              onChange={(event) => setSettings({ ...settings, default_ttl_days: Number(event.target.value) })} />
          </Field>
        </div>
        <button className="btn-primary mt-3" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <ShieldCheck className="h-4 w-4" /> Admins ({people.data?.admins.length ?? 0})
          </h3>
          <div className="flex gap-2">
            <input className="input" placeholder="Telegram user id" value={userId} onChange={(event) => setUserId(event.target.value)} />
            <button className="btn-primary" disabled={!userId.trim()} onClick={() => admin.mutate({ id: Number(userId), action: "add_admin" })}>
              <UserPlus className="h-4 w-4" />
            </button>
          </div>
          <ul className="mt-3 space-y-1.5">
            {(people.data?.admins ?? []).map((id) => (
              <li key={id} className="flex items-center justify-between rounded-lg border border-white/[0.06] px-2.5 py-1.5 text-xs">
                <span className="font-mono">{id}</span>
                <button className="btn-ghost" onClick={() => admin.mutate({ id, action: "remove_admin" })}>Remove</button>
              </li>
            ))}
            {(people.data?.admins.length ?? 0) === 0 ? <li className="text-xs text-slate-500">Only the owner can manage this shop.</li> : null}
          </ul>
        </Card>

        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <Ban className="h-4 w-4" /> Banned ({people.data?.banned.length ?? 0})
          </h3>
          <div className="flex gap-2">
            <input className="input" placeholder="Telegram user id" value={userId} onChange={(event) => setUserId(event.target.value)} />
            <button className="btn-danger" disabled={!userId.trim()} onClick={() => admin.mutate({ id: Number(userId), action: "ban" })}>Ban</button>
            <button className="btn-ghost" disabled={!userId.trim()} onClick={() => admin.mutate({ id: Number(userId), action: "unban" })}>Unban</button>
          </div>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {(people.data?.banned ?? []).map((id) => (
              <li key={id}><Badge status="error">{id}</Badge></li>
            ))}
            {(people.data?.banned.length ?? 0) === 0 ? <li className="text-xs text-slate-500">Nobody is banned.</li> : null}
          </ul>
        </Card>
      </div>
    </div>
  );
}
