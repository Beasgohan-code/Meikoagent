"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { Badge, Card, ErrorNote, Skeleton } from "@/components/ui";
import { ModuleShell } from "@/components/modules";
import { ProfileForm } from "@/components/encoder";
import { encoder } from "@/lib/modules";

const REFERENCE = [
  { codec: "x265", label: "H.265 / HEVC", note: "best size per quality; slower; 10-bit supported", crf: "22–28" },
  { codec: "x264", label: "H.264 / AVC", note: "maximum compatibility with old players", crf: "20–26" },
  { codec: "copy", label: "Stream copy", note: "remux only — no re-encode, instant", crf: "—" },
];

export default function EncoderPresetsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="encoder" title="Presets" description="Codecs, CRF ranges and container rules, plus this bot's saved default profile.">
        {({ botId }) => <Presets botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Presets({ botId }: { botId: number }) {
  const presets = useQuery({ queryKey: ["encoder-presets"], queryFn: () => encoder.presets() });
  if (presets.isLoading) return <Skeleton className="h-52" />;
  if (presets.error) return <ErrorNote message={(presets.error as Error).message} />;
  const data = presets.data!;

  return (
    <div className="space-y-4">
      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">Codec reference</h3>
        <div className="grid gap-3 md:grid-cols-3">
          {REFERENCE.map((row) => (
            <div key={row.codec} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-violet-200">{row.codec}</span>
                <Badge status="info">{row.label}</Badge>
              </div>
              <p className="mt-2 text-xs text-slate-400">{row.note}</p>
              <p className="mt-1 text-[11px] text-slate-500">CRF {row.crf}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-slate-200">What this build accepts</h3>
        <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Codecs", data.codecs],
            ["Presets", data.presets],
            ["Containers", data.containers],
            ["Resolutions", data.resolutions],
          ].map(([label, values]) => (
            <div key={String(label)}>
              <dt className="text-slate-500">{String(label)}</dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {(values as string[]).map((value) => (
                  <span key={value} className="kbd">{value}</span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <ProfileForm botId={botId} tools={data.tools} />
    </div>
  );
}
