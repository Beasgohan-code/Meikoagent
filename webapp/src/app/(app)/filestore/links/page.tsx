"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import Link from "next/link";
import { Link2, ShieldAlert } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Field, Skeleton } from "@/components/ui";
import { CopyButton } from "@/components/patterns";
import { ModuleShell } from "@/components/modules";
import { ShopPicker, useShop } from "@/components/filestore";
import { filestore } from "@/lib/modules";

export default function LinkGeneratorPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="filestore" title="Link generator" description="Signed deep links for stored files, with expiry and a use cap.">
        {({ botId }) => <Generator botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Generator({ botId }: { botId: number }) {
  const { shops, shop, slug, setSlug, loading, error } = useShop(botId);
  const files = useQuery({ queryKey: ["filestore-files", slug, "", 1], queryFn: () => filestore.files(slug, "", 1), enabled: Boolean(slug) });
  const [fileId, setFileId] = useState("");
  const [ttlDays, setTtlDays] = useState(30);
  const [maxUses, setMaxUses] = useState(0);
  const [issued, setIssued] = useState<{ start: string; file: string; expires_at: number } | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => filestore.link(slug, fileId || (files.data?.files[0]?.id ?? ""), ttlDays, maxUses),
    onSuccess: (result) => {
      setIssued(result.link);
      setNote(null);
    },
    onError: (err: Error) => {
      setIssued(null);
      setNote(err.message);
    },
  });

  if (loading) return <Skeleton className="h-64" />;
  if (error) return <ErrorNote message={error.message} />;
  if (shops.length === 0) {
    return <EmptyState title="No FileStore" description="Create a FileStore for this bot first." action={<Link href="/filestore" className="btn-primary">My bots</Link>} />;
  }

  const rows = files.data?.files ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <ShopPicker shops={shops} value={slug} onChange={setSlug} />
        {shop ? <Badge status="info">{shop.usage.files} files available</Badge> : null}
      </div>
      {note ? <ErrorNote message={note} /> : null}

      <Card>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="File">
            <select className="input" value={fileId} onChange={(event) => setFileId(event.target.value)}>
              {rows.length === 0 ? <option value="">no files</option> : null}
              {rows.map((file) => (
                <option key={file.id} value={file.id}>{file.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Expires in (days)" hint="0 = never expires">
            <input className="input" type="number" min={0} max={365} value={ttlDays} onChange={(event) => setTtlDays(Number(event.target.value))} />
          </Field>
          <Field label="Max uses" hint="0 = unlimited">
            <input className="input" type="number" min={0} value={maxUses} onChange={(event) => setMaxUses(Number(event.target.value))} />
          </Field>
        </div>
        <button className="btn-primary mt-3" onClick={() => create.mutate()} disabled={create.isPending || rows.length === 0}>
          <Link2 className="h-4 w-4" /> Generate link
        </button>
      </Card>

      {issued ? (
        <Card>
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-xs uppercase tracking-wider text-slate-500">Deep link</p>
              <p className="mt-1 break-all font-mono text-xs text-violet-200">{issued.start}</p>
            </div>
            <CopyButton value={issued.start} />
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            {issued.file} · {issued.expires_at ? `expires ${new Date(issued.expires_at * 1000).toLocaleString()}` : "no expiry"} ·
            signed with HMAC; editing any part of the token invalidates it.
          </p>
        </Card>
      ) : null}

      <Card className="border-amber-500/20 bg-amber-500/[0.05]">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
          <p className="text-xs text-amber-200/80">
            Links contain a signed token, not a storage id or a bot token. Anyone with the link can
            redeem it until it expires or reaches its use cap — revoke by deleting the file.
          </p>
        </div>
      </Card>
    </div>
  );
}
