"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import Link from "next/link";
import { Bot as BotIcon, FolderPlus, HardDrive } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Field, Skeleton, Stat } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { filestore } from "@/lib/modules";
import { formatBytes } from "@/lib/api";

export default function FileStoreBotsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="filestore" title="My bots" description="Each bot runs its own FileStore: isolated files, users, admins and settings.">
        {({ botId }) => <Shops botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Shops({ botId }: { botId: number }) {
  const queryClient = useQueryClient();
  const shops = useQuery({ queryKey: ["filestore-bots"], queryFn: () => filestore.bots() });
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => filestore.createShop(botId, title || "My FileStore"),
    onSuccess: () => {
      setTitle("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["filestore-bots"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  if (shops.isLoading) return <Skeleton className="h-64" />;
  if (shops.error) return <ErrorNote message={(shops.error as Error).message} />;
  const rows = shops.data!.shops;

  return (
    <div className="space-y-4">
      {error ? <ErrorNote message={error} /> : null}

      <Card>
        <Field label="Create a FileStore for this bot" hint="One shop per bot — bot tokens are never exposed here.">
          <div className="flex gap-2">
            <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Team archive" />
            <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
              <FolderPlus className="h-4 w-4" /> Create
            </button>
          </div>
        </Field>
      </Card>

      {rows.length === 0 ? (
        <EmptyState
          icon={<HardDrive className="h-6 w-6" />}
          title="No FileStore yet"
          description="Create one for this bot, then use /start in the bot to see it work."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {rows.map((shop) => (
            <Card key={shop.slug} hover>
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-medium text-white">{shop.title}</h3>
                  <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                    {shop.slug} · bot {shop.bot_id}
                  </p>
                </div>
                <Badge status={shop.active ? "running" : "stopped"}>{shop.active ? "active" : "paused"}</Badge>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
                <span><BotIcon className="mr-1 inline h-3 w-3" />{shop.usage.files} files</span>
                <span>{formatBytes(shop.usage.bytes)}</span>
                <span>{shop.usage.downloads} downloads</span>
                <span>{shop.usage.links} links</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Link href={`/filestore/files?shop=${shop.slug}`} className="btn-ghost">Files</Link>
                <Link href={`/filestore/links?shop=${shop.slug}`} className="btn-ghost">Links</Link>
                <Link href={`/filestore/settings?shop=${shop.slug}`} className="btn-ghost">Settings</Link>
              </div>
            </Card>
          ))}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Shops" value={rows.length} hint="one per bot" />
        <Stat label="Files" value={rows.reduce((total, shop) => total + shop.usage.files, 0)} />
        <Stat label="Storage" value={formatBytes(rows.reduce((total, shop) => total + shop.usage.bytes, 0))} />
      </div>
    </div>
  );
}
