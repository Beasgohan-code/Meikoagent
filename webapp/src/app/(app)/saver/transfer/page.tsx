"use client";

import { useMutation } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Download, Upload } from "lucide-react";
import { Card, ErrorNote, Skeleton } from "@/components/ui";
import { CodeBlock } from "@/components/patterns";
import { ModuleShell } from "@/components/modules";
import { saver } from "@/lib/modules";

export default function SaverTransferPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <ModuleShell slug="saver" title="Import / export" description="Move this bot's settings and index between deployments as a portable file.">
        {({ botId }) => <Transfer botId={botId} />}
      </ModuleShell>
    </Suspense>
  );
}

function Transfer({ botId }: { botId: number }) {
  const [exported, setExported] = useState<string | null>(null);
  const [payload, setPayload] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const exportData = useMutation({
    mutationFn: () => saver.exportData(botId),
    onSuccess: (data) => {
      setExported(JSON.stringify(data, null, 2));
      const rows = Array.isArray(data.index) ? (data.index as unknown[]).length : 0;
      setNote(`Export ready with ${rows} index entries and no credentials.`);
    },
    onError: (err: Error) => setNote(err.message),
  });

  const importData = useMutation({
    mutationFn: (document: Record<string, unknown>) => saver.importData(botId, document),
    onSuccess: (result) => setNote(`Imported ${result.imported} entries (${result.skipped} skipped); ${result.total} in the index now.`),
    onError: (err: Error) => setNote(err.message),
  });

  const runImport = () => {
    try {
      const parsed = JSON.parse(payload) as Record<string, unknown>;
      importData.mutate(parsed);
    } catch {
      setNote("That is not valid JSON.");
    }
  };

  return (
    <div className="space-y-4">
      {note ? <p className="text-xs text-slate-400">{note}</p> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <Download className="h-4 w-4" /> Export
          </h3>
          <p className="mb-3 text-xs text-slate-400">
            Includes settings, word rules and the saved index. Credentials, bot tokens and dump chat ids
            are not part of the export.
          </p>
          <button className="btn-primary" onClick={() => exportData.mutate()} disabled={exportData.isPending}>
            Build export
          </button>
          {exported ? (
            <div className="mt-3">
              <CodeBlock title="meiko.saver.v1">{exported.slice(0, 4000)}</CodeBlock>
              <button
                className="btn-ghost mt-2"
                onClick={() => void navigator.clipboard.writeText(exported)}
              >
                Copy JSON
              </button>
            </div>
          ) : null}
        </Card>

        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <Upload className="h-4 w-4" /> Import
          </h3>
          <p className="mb-2 text-xs text-slate-400">
            Paste a <span className="kbd">meiko.saver.v1</span> export. Local settings win on conflicts,
            and duplicate index entries are skipped.
          </p>
          <textarea
            className="input h-48 font-mono text-xs"
            value={payload}
            onChange={(event) => setPayload(event.target.value)}
            placeholder='{"format": "meiko.saver.v1", "config": {}, "index": []}'
          />
          <button className="btn-primary mt-3" onClick={runImport} disabled={!payload.trim() || importData.isPending}>
            Import
          </button>
        </Card>
      </div>
      {importData.error ? <ErrorNote message={(importData.error as Error).message} /> : null}
    </div>
  );
}
