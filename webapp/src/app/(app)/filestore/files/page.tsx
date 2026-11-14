"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import Link from "next/link";
import { FileArchive, HardDrive } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { SearchInput } from "@/components/patterns";
import { ModuleShell } from "@/components/modules";
import { ShopPicker, useShop } from "@/components/filestore";
import { filestore } from "@/lib/modules";
import { formatBytes } from "@/lib/api";

export default function FileManagerPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="filestore" title="File manager" description="Browse and filter the files a shop holds. Visibility decides who can see what.">
        {({ botId }) => <Files botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Files({ botId }: { botId: number }) {
  const { shops, shop, slug, setSlug, loading, error } = useShop(botId);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const files = useQuery({
    queryKey: ["filestore-files", slug, query, page],
    queryFn: () => filestore.files(slug, query, page),
    enabled: Boolean(slug),
  });

  if (loading) return <Skeleton className="h-64" />;
  if (error) return <ErrorNote message={error.message} />;
  if (shops.length === 0) {
    return (
      <EmptyState
        icon={<HardDrive className="h-6 w-6" />}
        title="No FileStore for this bot"
        description="Create one first — then files land here as users upload them through the bot."
        action={<Link href="/filestore" className="btn-primary">Open My bots</Link>}
      />
    );
  }

  const rows = files.data?.files ?? [];
  const total = files.data?.total ?? 0;
  const perPage = 10;
  const pages = Math.max(1, Math.ceil(total / perPage));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ShopPicker shops={shops} value={slug} onChange={setSlug} />
        <div className="w-56">
          <SearchInput value={query} onChange={(value) => { setQuery(value); setPage(1); }} placeholder="Filter files…" />
        </div>
        <Badge status="info">{total} files</Badge>
      </div>

      {files.error ? <ErrorNote message={(files.error as Error).message} /> : null}

      {rows.length === 0 ? (
        <EmptyState icon={<FileArchive className="h-6 w-6" />} title="No files" description="Files appear once users send them to the bot." />
      ) : (
        <Card className="p-0">
          <table className="table-base">
            <thead className="bg-white/[0.02]">
              <tr>
                <th>Name</th>
                <th>Visibility</th>
                <th>Size</th>
                <th>Downloads</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((file) => (
                <tr key={file.id}>
                  <td className="max-w-[280px] truncate">
                    {file.name}
                    <span className="ml-2 font-mono text-[10px] text-slate-600">{file.id}</span>
                  </td>
                  <td>
                    <Badge status={file.visibility === "public" ? "ok" : file.visibility === "link" ? "info" : "inactive"}>
                      {file.visibility}
                    </Badge>
                  </td>
                  <td>{formatBytes(file.size)}</td>
                  <td>{file.downloads}</td>
                  <td className="text-xs text-slate-500">
                    {file.expires_at ? new Date(file.expires_at * 1000).toLocaleDateString() : "never"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {pages > 1 ? (
        <div className="flex items-center justify-center gap-2 text-xs text-slate-400">
          <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span>page {page} / {pages}</span>
          <button className="btn-ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      ) : null}
    </div>
  );
}
