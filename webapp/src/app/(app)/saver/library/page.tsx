"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Search, Trash2 } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { SearchInput } from "@/components/patterns";
import { ModuleShell } from "@/components/modules";
import { formatBytes } from "@/lib/api";
import { saver } from "@/lib/modules";

export default function SaverLibraryPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="saver" title="Media library" description="Everything this bot saved, with the source it came from.">
        {({ botId }) => <Library botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Library({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: ["saver-history", botId], queryFn: () => saver.history(botId) });
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const forget = useMutation({
    mutationFn: (messageId: number) => saver.forget(botId, messageId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["saver-history", botId] }),
    onError: (err: Error) => setError(err.message),
  });

  if (history.isLoading) return <Skeleton className="h-64" />;
  if (history.error) return <ErrorNote message={(history.error as Error).message} />;

  const rows = history.data!.saved.filter((row) => {
    if (!query) return true;
    const needle = query.toLowerCase();
    return row.filename.toLowerCase().includes(needle) || row.chat.toLowerCase().includes(needle);
  });

  if (history.data!.total === 0) {
    return <EmptyState title="Nothing saved yet" description="Send a link the bot is allowed to read, or reply to a post with /save." />;
  }

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} /> : null}
      <div className="max-w-sm">
        <SearchInput value={query} onChange={setQuery} placeholder="Filter by file or source…" />
      </div>
      <Card className="p-0">
        <table className="table-base">
          <thead className="bg-white/[0.02]">
            <tr>
              <th>File</th>
              <th>Source</th>
              <th>Kind</th>
              <th>Size</th>
              <th>Saved</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.chat}-${row.message_id}`}>
                <td className="max-w-[260px] truncate">{row.filename || `message ${row.message_id}`}</td>
                <td className="font-mono text-xs text-slate-400">
                  {row.chat}/{row.message_id}
                </td>
                <td><Badge status="info">{row.kind}</Badge></td>
                <td>{formatBytes(row.size)}</td>
                <td className="text-xs text-slate-500">{row.saved_at ? new Date(row.saved_at).toLocaleString() : "—"}</td>
                <td className="text-right">
                  <button className="btn-danger" onClick={() => forget.mutate(row.message_id)} title="Remove from index">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <p className="flex items-center gap-2 text-[11px] text-slate-500">
        <Search className="h-3 w-3" /> Removing an entry only forgets the index record — the Telegram post itself is untouched.
      </p>
    </div>
  );
}
