"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { Badge, Card, ErrorNote, Field, Skeleton, Toggle } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { saver } from "@/lib/modules";

export default function SaverSettingsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="saver" title="Saver settings" description="Captions, delivery mode, batching and duplicate handling.">
        {({ botId }) => <Settings botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Settings({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: ["saver-overview", botId], queryFn: () => saver.overview(botId) });
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (overview.data) setForm({ ...overview.data.config });
  }, [overview.data]);

  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) => saver.settings(botId, patch),
    onSuccess: (result) => {
      setForm({ ...result.config });
      setNote("Saved. A running bot picks this up immediately.");
      void queryClient.invalidateQueries({ queryKey: ["saver-overview", botId] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  const retention = useMutation({
    mutationFn: (days: number) => saver.retention(botId, days),
    onSuccess: (result) => setNote(`Retention applied: kept ${result.kept}, removed ${result.removed}.`),
    onError: (err: Error) => setNote(err.message),
  });

  if (overview.isLoading) return <Skeleton className="h-64" />;
  if (overview.error) return <ErrorNote message={(overview.error as Error).message} />;

  return (
    <div className="space-y-4">
      {note ? <p className="text-xs text-slate-400">{note}</p> : null}
      {overview.data!.jobs.length === 0 && overview.data!.library.count === 0 ? (
        <Badge status="info">This bot has not saved anything yet — the settings below are its defaults.</Badge>
      ) : null}

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Delivery</h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Send mode" hint="copy keeps media without re-uploading">
            <select className="input" value={String(form.send_mode ?? "copy")}
              onChange={(event) => setForm({ ...form, send_mode: event.target.value })}>
              {["copy", "document", "video"].map((mode) => <option key={mode} value={mode}>{mode}</option>)}
            </select>
          </Field>
          <Field label="Max batch" hint="posts per /batch run">
            <select className="input" value={String(form.max_batch ?? 20)}
              onChange={(event) => setForm({ ...form, max_batch: Number(event.target.value) })}>
              {[1, 5, 10, 20, 50].map((size) => <option key={size} value={size}>{size}</option>)}
            </select>
          </Field>
          <Field label="Delay between saves (s)">
            <input className="input" type="number" step="0.1" min="0" max="5"
              value={String(form.delay_seconds ?? 0.4)}
              onChange={(event) => setForm({ ...form, delay_seconds: Number(event.target.value) })} />
          </Field>
        </div>
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Captions</h3>
        <Field label="Caption template" hint="Use {title}, {source}, {size}, {index}">
          <textarea className="input h-24 font-mono text-xs" value={String(form.caption_template ?? "")}
            onChange={(event) => setForm({ ...form, caption_template: event.target.value })} />
        </Field>
        <div className="mt-3 flex items-center gap-2">
          <Toggle checked={Boolean(form.use_caption)} onChange={(value) => setForm({ ...form, use_caption: value })} label="custom captions" />
          <span className="text-xs text-slate-400">Apply the custom caption when re-delivering</span>
        </div>
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Behaviour</h3>
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Toggle checked={Boolean(form.skip_duplicates)} onChange={(value) => setForm({ ...form, skip_duplicates: value })} label="skip duplicates" />
            <span className="text-xs text-slate-400">Skip posts already in the index</span>
          </div>
          <div className="flex items-center gap-2">
            <Toggle checked={Boolean(form.notify_each)} onChange={(value) => setForm({ ...form, notify_each: value })} label="notify each" />
            <span className="text-xs text-slate-400">Report every saved post instead of a single summary</span>
          </div>
        </div>
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Retention</h3>
        <p className="mb-2 text-xs text-slate-400">
          Prune index records older than a number of days. This never deletes media from Telegram.
        </p>
        <div className="flex flex-wrap gap-2">
          {[7, 30, 90, 365].map((days) => (
            <button key={days} className="btn-ghost" onClick={() => retention.mutate(days)} disabled={retention.isPending}>
              Keep {days} days
            </button>
          ))}
        </div>
      </Card>

      <div className="flex justify-end">
        <button className="btn-primary" onClick={() => save.mutate(form)} disabled={save.isPending}>
          Save settings
        </button>
      </div>
    </div>
  );
}
