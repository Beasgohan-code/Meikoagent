"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Link2 } from "lucide-react";
import { Badge, Card, ErrorNote, Field, Skeleton, Stat } from "@/components/ui";
import { CopyButton } from "@/components/patterns";
import { ModuleShell } from "@/components/modules";
import { linkshare, type ShareLink } from "@/lib/modules";

export default function CreateLinkPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="linkshare" title="Create link" description="Share a file through a protected, expiring link and track what actually happened.">
        {({ botId }) => <CreateLink botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function CreateLink({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const links = useQuery({ queryKey: ["linkshare-links", botId], queryFn: () => linkshare.links(botId) });
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [ttl, setTtl] = useState(24);
  const [maxClaims, setMaxClaims] = useState(0);
  const [created, setCreated] = useState<ShareLink | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // ask the backend policy while typing, so a refusal is explained before submit
  const check = useQuery({
    queryKey: ["linkshare-validate", url],
    queryFn: () => linkshare.validate(url.trim()),
    enabled: /^https?:\/\//i.test(url.trim()) && url.trim().length > 10,
    staleTime: 60_000,
    retry: false,
  });

  const create = useMutation({
    mutationFn: () => linkshare.create(botId, url.trim(), title.trim(), ttl, maxClaims),
    onSuccess: (result) => {
      setCreated(result.link);
      setUrl("");
      setTitle("");
      setNote(null);
      void queryClient.invalidateQueries({ queryKey: ["linkshare-links", botId] });
    },
    onError: (err: Error) => {
      setCreated(null);
      setNote(err.message);
    },
  });

  if (links.isLoading) return <Skeleton className="h-64" />;
  if (links.error) return <ErrorNote message={(links.error as Error).message} />;
  const analytics = links.data!.analytics;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Links" value={analytics.total} />
        <Stat label="Active" value={analytics.active} hint={`${analytics.expired} expired`} />
        <Stat label="Claims" value={analytics.claims} hint="gate passes observed" />
        <Stat label="Refusals" value={analytics.refusals} hint="expired / revoked / capped" />
      </div>

      {note ? <ErrorNote message={note} /> : null}

      <Card>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="URL to share"
            hint={
              check.data && !check.data.allowed
                ? check.data.reason
                : "Public http(s) only — loopback, private hosts and credentials are refused."
            }
          >
            <input
              className={`input ${check.data && !check.data.allowed ? "border-rose-500/50" : ""}`}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/file.zip"
            />
          </Field>
          <Field label="Label" hint="Only you see this in the manager.">
            <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Q3 report" />
          </Field>
          <Field label="Expires in (hours)" hint="0 = never expires">
            <input className="input" type="number" min={0} value={ttl} onChange={(event) => setTtl(Number(event.target.value))} />
          </Field>
          <Field label="Max claims" hint="0 = unlimited">
            <input className="input" type="number" min={0} value={maxClaims} onChange={(event) => setMaxClaims(Number(event.target.value))} />
          </Field>
        </div>
        <button
          className="btn-primary mt-3"
          disabled={!url.trim() || create.isPending || check.data?.allowed === false}
          onClick={() => create.mutate()}
        >
          <Link2 className="h-4 w-4" /> Create protected link
        </button>
      </Card>

      {created ? (
        <Card>
          <p className="text-xs uppercase tracking-wider text-slate-500">New link</p>
          <p className="mt-1 break-all font-mono text-xs text-violet-200">{created.url}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge status="ok">{created.code}</Badge>
            <span className="text-[11px] text-slate-500">
              {created.expires_at ? `expires ${new Date(created.expires_at * 1000).toLocaleString()}` : "no expiry"} ·{" "}
              {created.max_claims ? `${created.max_claims} claims max` : "unlimited claims"}
            </span>
            <CopyButton value={created.url} />
          </div>
        </Card>
      ) : null}

      <p className="text-[11px] text-slate-500">{analytics.note}</p>
    </div>
  );
}
