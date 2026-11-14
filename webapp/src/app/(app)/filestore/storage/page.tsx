"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import Link from "next/link";
import { Database } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton, Stat } from "@/components/ui";
import { ProgressBar, ModuleShell } from "@/components/modules";
import { ShopPicker, useShop } from "@/components/filestore";
import { filestore, platform } from "@/lib/modules";
import { formatBytes } from "@/lib/api";

export default function FileStoreStoragePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="filestore" title="Storage" description="Per-shop usage alongside the platform storage ledger.">
        {({ botId }) => <Storage botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Storage({ botId }: { botId: number }) {
  const { shops, shop, slug, setSlug, loading, error } = useShop(botId);
  const usage = useQuery({ queryKey: ["platform-storage"], queryFn: () => platform.storage() });

  if (loading) return <Skeleton className="h-64" />;
  if (error) return <ErrorNote message={error.message} />;
  if (shops.length === 0) {
    return <EmptyState icon={<Database className="h-6 w-6" />} title="No FileStore" description="Create a shop to start tracking storage." action={<Link href="/filestore" className="btn-primary">My bots</Link>} />;
  }

  const perBot = usage.data?.per_bot?.[String(botId)];
  const modules = perBot?.modules ?? {};
  const maxBytes = Math.max(1, ...Object.values(modules).map((row) => row.bytes));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <ShopPicker shops={shops} value={slug} onChange={setSlug} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Files" value={shop?.usage.files ?? 0} />
        <Stat label="Stored" value={formatBytes(shop?.usage.bytes ?? 0)} hint="sum of file sizes" />
        <Stat label="Downloads" value={shop?.usage.downloads ?? 0} />
        <Stat label="Ledger" value={perBot?.total_human ?? "0 B"} hint="reserved by all modules" />
      </div>

      <Card>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
          <Database className="h-4 w-4" /> Storage ledger for this bot
        </h3>
        {Object.keys(modules).length === 0 ? (
          <p className="text-sm text-slate-500">Nothing reserved by any module yet.</p>
        ) : (
          <ul className="space-y-3">
            {Object.entries(modules).map(([module, row]) => (
              <li key={module} className="space-y-1">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-300">{module}</span>
                  <span className="text-xs text-slate-500">{row.human}</span>
                </div>
                <ProgressBar percent={(row.bytes / maxBytes) * 100} />
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-[11px] text-slate-500">
          Reserved bytes come from the shared ledger the modules write when they accept a file. Quotas are
          enforced per owner, so one shop cannot consume another one&apos;s allowance.
        </p>
      </Card>

      <Badge status="info">{usage.data?.platform.owner_count ?? 0} owners tracked platform-wide</Badge>
    </div>
  );
}
