"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Ban } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { Tabs } from "@/components/patterns";
import { ModuleShell, stateTone } from "@/components/modules";
import { linkshare } from "@/lib/modules";

type Tab = "all" | "active" | "expired" | "revoked" | "exhausted";

export default function LinkManagerPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="linkshare" title="Link manager" description="Every link this bot issued, with its state and the reasons it refused access.">
        {({ botId }) => <Manager botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Manager({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const links = useQuery({ queryKey: ["linkshare-links", botId], queryFn: () => linkshare.links(botId) });
  const [tab, setTab] = useState<Tab>("all");
  const [note, setNote] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: (code: string) => linkshare.revoke(botId, code),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["linkshare-links", botId] }),
    onError: (err: Error) => setNote(err.message),
  });

  if (links.isLoading) return <Skeleton className="h-64" />;
  if (links.error) return <ErrorNote message={(links.error as Error).message} />;

  const rows = links.data!.links;
  const counts = {
    all: rows.length,
    active: rows.filter((row) => row.state === "active").length,
    expired: rows.filter((row) => row.state === "expired").length,
    revoked: rows.filter((row) => row.state === "revoked").length,
    exhausted: rows.filter((row) => row.state === "exhausted").length,
  };
  const shown = tab === "all" ? rows : rows.filter((row) => row.state === tab);

  return (
    <div className="space-y-3">
      {note ? <ErrorNote message={note} /> : null}
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "all", label: "All", count: counts.all },
          { id: "active", label: "Active", count: counts.active },
          { id: "expired", label: "Expired", count: counts.expired },
          { id: "revoked", label: "Revoked", count: counts.revoked },
          { id: "exhausted", label: "Exhausted", count: counts.exhausted },
        ]}
      />

      {shown.length === 0 ? (
        <EmptyState title="No links here" description="Create a link or a batch to see it listed." />
      ) : (
        <Card className="p-0">
          <table className="table-base">
            <thead className="bg-white/[0.02]">
              <tr>
                <th>Code</th>
                <th>Label / URL</th>
                <th>State</th>
                <th>Claims</th>
                <th>Refusals</th>
                <th>Expires</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((link) => (
                <tr key={link.code}>
                  <td className="font-mono text-xs">{link.code}</td>
                  <td className="max-w-[280px]">
                    <span className="block truncate text-slate-200">{link.title || link.file_name || "—"}</span>
                    <span className="block truncate font-mono text-[10px] text-slate-500">{link.url}</span>
                  </td>
                  <td><Badge status={stateTone(link.state)}>{link.state}</Badge></td>
                  <td>
                    {link.claims}
                    {link.max_claims ? <span className="text-slate-500">/{link.max_claims}</span> : null}
                  </td>
                  <td>{link.refusals}</td>
                  <td className="text-xs text-slate-500">
                    {link.expires_at ? new Date(link.expires_at * 1000).toLocaleString() : "never"}
                  </td>
                  <td className="text-right">
                    {link.state === "active" ? (
                      <button className="btn-danger" onClick={() => revoke.mutate(link.code)} title="Revoke">
                        <Ban className="h-3.5 w-3.5" />
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
