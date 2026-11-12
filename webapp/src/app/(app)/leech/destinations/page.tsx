"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Badge, Card, ErrorNote, Field, Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { leech } from "@/lib/modules";

export default function LeechDestinationsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="leech" title="Destinations" description="Where transfers can land, and a validator that says yes or why not.">
        {({ botId }) => <Destinations botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Destinations({ botId }: { botId: number }) {
  const sources = useQuery({ queryKey: ["leech-sources"], queryFn: () => leech.sources() });
  const [source, setSource] = useState("");
  const [verdict, setVerdict] = useState<{ valid: boolean; reason?: string; downloader?: string } | null>(null);
  const check = useMutation({
    mutationFn: () => leech.validate(botId, source.trim()),
    onSuccess: setVerdict,
    onError: (err: Error) => setVerdict({ valid: false, reason: err.message }),
  });

  if (sources.isLoading) return <Skeleton className="h-64" />;
  if (sources.error) return <ErrorNote message={(sources.error as Error).message} />;
  const data = sources.data!;

  return (
    <div className="space-y-4">
      <Card>
        <Field label="Validate a source before transferring" hint="Checks scheme, reachability and size ceiling — no bytes are moved.">
          <div className="flex gap-2">
            <input className="input" value={source} onChange={(event) => setSource(event.target.value)}
              placeholder="https://example.com/big.iso or /srv/media/file.mkv" />
            <button className="btn-primary" disabled={!source.trim() || check.isPending} onClick={() => check.mutate()}>Check</button>
          </div>
        </Field>
        {verdict ? (
          <div className="mt-3">
            <Badge status={verdict.valid ? "ok" : "error"}>{verdict.valid ? `handled by ${verdict.downloader}` : "refused"}</Badge>
            {verdict.reason ? <p className="mt-1 text-xs text-rose-300">{verdict.reason}</p> : null}
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">Adapters</h3>
          <ul className="space-y-2 text-xs">
            {[...data.engine.downloaders.map((row) => ({ ...row, role: "download" })), ...data.engine.uploaders.map((row) => ({ ...row, role: "upload" }))].map((row) => (
              <li key={`${row.role}-${row.name}`} className="flex items-center justify-between">
                <span className="font-mono text-slate-300">{row.role}:{row.name}</span>
                <Badge status={row.available ? "ok" : "disabled"}>{row.available ? "ready" : "needs setup"}</Badge>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h3 className="mb-3 text-sm font-medium text-slate-200">MTProto (optional)</h3>
          <Badge status={data.mtproto.available ? "ok" : "disabled"}>
            {data.mtproto.available ? data.mtproto.client : "not installed"}
          </Badge>
          <p className="mt-2 text-xs text-slate-400">{data.mtproto.reason || "Streaming and large uploads are available."}</p>
          <p className="mt-2 text-[11px] text-slate-500">
            The adapter is only for what the Bot API genuinely cannot do. It never shares a token with the
            bot and never uses a user session to reach restricted content.
          </p>
        </Card>
      </div>
    </div>
  );
}
