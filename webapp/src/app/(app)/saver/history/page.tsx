"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Badge, Card, ErrorNote, Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { formatBytes } from "@/lib/api";
import { saver } from "@/lib/modules";

export default function SaverHistoryPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="saver" title="History" description="A chronological record of what was saved, when, and from where.">
        {({ botId }) => <History botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function History({ botId }: { botId: number }) {
  const history = useQuery({ queryKey: ["saver-history", botId], queryFn: () => saver.history(botId) });
  if (history.isLoading) return <Skeleton className="h-64" />;
  if (history.error) return <ErrorNote message={(history.error as Error).message} />;
  const rows = [...history.data!.saved].sort((a, b) => (a.saved_at < b.saved_at ? 1 : -1));

  return (
    <Card className="p-0">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
        <h3 className="text-sm font-medium text-slate-200">{history.data!.total} saved items</h3>
        <Badge status="info">{formatBytes(rows.reduce((total, row) => total + row.size, 0))}</Badge>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">No history yet.</p>
      ) : (
        <ul className="divide-y divide-white/[0.05]">
          {rows.map((row) => (
            <li key={`${row.chat}-${row.message_id}-${row.saved_at}`} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
              <Badge status="info">{row.kind}</Badge>
              <span className="min-w-0 flex-1 truncate text-slate-300">{row.filename || `message ${row.message_id}`}</span>
              <span className="font-mono text-[11px] text-slate-500">{row.chat}/{row.message_id}</span>
              <span className="text-xs text-slate-500">{formatBytes(row.size)}</span>
              <span className="text-[11px] text-slate-500">{row.saved_at ? new Date(row.saved_at).toLocaleString() : "—"}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
