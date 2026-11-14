"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Layers } from "lucide-react";
import { Badge, Card, ErrorNote, Field, Skeleton } from "@/components/ui";
import { CopyButton } from "@/components/patterns";
import { ModuleShell } from "@/components/modules";
import { linkshare, type ShareLink } from "@/lib/modules";

export default function BatchSharingPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="linkshare" title="Batch sharing" description="Create a group of protected links in one action, then revoke them together.">
        {({ botId }) => <Batch botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Batch({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [ttl, setTtl] = useState(24);
  const [result, setResult] = useState<{ batch: string; links: ShareLink[] } | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const urls = text.split("\n").map((line) => line.trim()).filter(Boolean);

  const create = useMutation({
    mutationFn: () => linkshare.batch(botId, urls, ttl),
    onSuccess: (data) => {
      setResult(data);
      setNote(null);
      void queryClient.invalidateQueries({ queryKey: ["linkshare-links", botId] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  const revoke = useMutation({
    mutationFn: async (batch: string) => {
      const rows = await linkshare.links(botId);
      const codes = rows.links.filter((link) => link.batch === batch && link.state === "active").map((link) => link.code);
      await Promise.all(codes.map((code) => linkshare.revoke(botId, code)));
      return codes.length;
    },
    onSuccess: (count) => {
      setNote(`Revoked ${count} link(s) in this batch.`);
      void queryClient.invalidateQueries({ queryKey: ["linkshare-links", botId] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  return (
    <div className="space-y-4">
      {note ? <ErrorNote message={note} /> : null}
      <Card>
        <Field label="URLs (one per line)" hint={`${urls.length} URL(s) — max 100 per batch`}>
          <textarea className="input h-40 font-mono text-xs" value={text} onChange={(event) => setText(event.target.value)}
            placeholder={"https://example.com/a.zip\nhttps://example.com/b.zip"} />
        </Field>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <Field label="Expires in (hours)">
            <input className="input w-32" type="number" min={0} value={ttl} onChange={(event) => setTtl(Number(event.target.value))} />
          </Field>
          <button className="btn-primary" disabled={urls.length === 0 || create.isPending} onClick={() => create.mutate()}>
            <Layers className="h-4 w-4" /> Create {urls.length} link(s)
          </button>
        </div>
      </Card>

      {result ? (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs uppercase tracking-wider text-slate-500">Batch</p>
              <p className="font-mono text-sm text-violet-200">{result.batch}</p>
            </div>
            <div className="flex gap-2">
              <CopyButton value={result.links.map((link) => link.url).join("\n")} label="Copy all" />
              <button className="btn-danger" onClick={() => revoke.mutate(result.batch)}>Revoke batch</button>
            </div>
          </div>
          <ul className="mt-3 divide-y divide-white/[0.05]">
            {result.links.map((link) => (
              <li key={link.code} className="flex items-center gap-2 py-1.5 text-xs">
                <Badge status="info">{link.code}</Badge>
                <span className="min-w-0 flex-1 truncate font-mono text-slate-300">{link.url}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
