"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Ban, CheckCircle2, Clock, Film } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { ProgressBar, stateTone } from "@/components/modules";
import { encoder, type EncoderProfile, type LedgerJob } from "@/lib/modules";

export function EncoderToolsNote({ tools }: { tools?: { available: boolean; missing: string[] } }) {
  if (!tools) return null;
  if (tools.available) {
    return (
      <Badge status="ok">
        <CheckCircle2 className="h-3 w-3" /> ffmpeg + ffprobe ready
      </Badge>
    );
  }
  return (
    <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-2.5 text-xs text-amber-200">
      <strong className="font-medium">Encoding is unavailable on this host.</strong> Missing:{" "}
      <span className="font-mono">{tools.missing.join(", ")}</span>. Plans and commands are still generated,
      validation is enforced, and jobs are refused instead of pretending to run.
    </div>
  );
}

export function JobRow({ job, onCancel, onRetry }: { job: LedgerJob; onCancel?: (id: string) => void; onRetry?: (id: string) => void }) {
  const active = job.state === "running" || job.state === "queued";
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge status={stateTone(job.state)}>{job.state}</Badge>
        <span className="font-mono text-xs text-slate-400">{job.id}</span>
        <span className="text-sm text-slate-200">{job.kind}</span>
        <span className="text-xs text-slate-500">{String(job.meta?.file ?? "")}</span>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-slate-500">
          <Clock className="h-3 w-3" />
          {job.seconds ? `${job.seconds}s` : job.created_at ? new Date(job.created_at * 1000).toLocaleTimeString() : "—"}
        </span>
        {active && onCancel ? (
          <button className="btn-danger" onClick={() => onCancel(job.id)}>
            <Ban className="h-3.5 w-3.5" /> Cancel
          </button>
        ) : null}
        {!active && (job.state === "failed" || job.state === "cancelled") && onRetry ? (
          <button className="btn-ghost" onClick={() => onRetry(job.id)}>
            Retry
          </button>
        ) : null}
      </div>
      {active ? (
        <div className="mt-2">
          <ProgressBar percent={job.progress} tone={job.state === "running" ? "violet" : "amber"} />
          <p className="mt-1 text-[11px] text-slate-500">
            {job.progress.toFixed(1)}% {job.detail ? `· ${job.detail}` : ""}
          </p>
        </div>
      ) : null}
      {job.error ? <p className="mt-1 text-xs text-rose-300">{job.error}</p> : null}
      {!active && job.detail ? <p className="mt-1 text-[11px] text-slate-500">{job.detail}</p> : null}
    </div>
  );
}

export function JobsPanel({ botId, activeOnly = false }: { botId: number; activeOnly?: boolean }) {
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["encoder-jobs", botId], queryFn: () => encoder.jobs(botId), refetchInterval: 4000 });
  const [error, setError] = useState<string | null>(null);

  const cancel = useMutation({
    mutationFn: (jobId: string) => encoder.cancel(botId, jobId),
    onSuccess: (result) => {
      setError(result.applied ? null : "The bot reported the job was already gone; the record was closed.");
      void queryClient.invalidateQueries({ queryKey: ["encoder-jobs", botId] });
    },
    onError: (err: Error) => setError(err.message),
  });

  if (jobs.isLoading) return <Skeleton className="h-40" />;
  if (jobs.error) return <ErrorNote message={(jobs.error as Error).message} />;

  const rows: LedgerJob[] = activeOnly ? jobs.data!.active : jobs.data!.jobs;
  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} /> : null}
      <EncoderToolsNote tools={jobs.data!.tools} />
      {rows.length === 0 ? (
        <EmptyState
          icon={<Film className="h-6 w-6" />}
          title={activeOnly ? "No active encodes" : "No encodes yet"}
          description="Reply to a video in Telegram with /encode, or set a preset then run it from the Studio."
        />
      ) : (
        rows.map((job) => <JobRow key={job.id} job={job} onCancel={(id) => cancel.mutate(id)} />)
      )}
    </div>
  );
}

export function ProfileForm({ botId, tools }: { botId: number; tools?: { available: boolean; missing: string[] } }) {
  const queryClient = useQueryClient();
  const presets = useQuery({ queryKey: ["encoder-presets"], queryFn: () => encoder.presets() });
  const profile = useQuery({ queryKey: ["encoder-profile", botId], queryFn: () => encoder.profile(botId) });
  const [note, setNote] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (next: EncoderProfile) => encoder.saveProfile(botId, next),
    onSuccess: () => {
      setNote("Profile saved. The running bot re-reads it before the next encode.");
      void queryClient.invalidateQueries({ queryKey: ["encoder-profile", botId] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  if (profile.isLoading || presets.isLoading) return <Skeleton className="h-56" />;
  if (profile.error || presets.error) {
    return <ErrorNote message={((profile.error || presets.error) as Error).message} />;
  }
  const options = presets.data!;
  const current = {
    codec: "x265",
    crf: 26,
    preset: "medium",
    container: "mkv",
    resolution: "original",
    audio_codec: "copy",
    audio_bitrate: "128k",
    channels: "stereo",
    subtitles: "copy",
    ten_bit: false,
    watermark: "",
    watermark_position: "top-left",
    thumbnail: true,
    ...profile.data!.profile,
  } as EncoderProfile;

  const field = <K extends keyof EncoderProfile>(key: K, value: EncoderProfile[K]) => {
    void save.mutate({ ...current, [key]: value });
  };

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-slate-200">Default encode profile</h3>
        <EncoderToolsNote tools={tools ?? profile.data!.tools} />
      </div>
      {note ? <p className="mb-3 text-xs text-slate-400">{note}</p> : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="block text-xs text-slate-400">
          Codec
          <select className="input mt-1" value={current.codec} onChange={(event) => field("codec", event.target.value)}>
            {options.codecs.map((codec) => <option key={codec} value={codec}>{codec}</option>)}
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          CRF ({current.crf})
          <input type="range" min={0} max={51} value={current.crf} className="mt-3 w-full accent-violet-500"
            onChange={(event) => field("crf", Number(event.target.value))} />
        </label>
        <label className="block text-xs text-slate-400">
          Preset
          <select className="input mt-1" value={current.preset} onChange={(event) => field("preset", event.target.value)}>
            {options.presets.map((preset) => <option key={preset} value={preset}>{preset}</option>)}
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          Container
          <select className="input mt-1" value={current.container} onChange={(event) => field("container", event.target.value)}>
            {options.containers.map((container) => <option key={container} value={container}>{container}</option>)}
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          Resolution
          <select className="input mt-1" value={current.resolution} onChange={(event) => field("resolution", event.target.value)}>
            {options.resolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          10-bit
          <select className="input mt-1" value={current.ten_bit ? "yes" : "no"} onChange={(event) => field("ten_bit", event.target.value === "yes")}>
            <option value="no">8-bit (yuv420p)</option>
            <option value="yes">10-bit (yuv420p10le)</option>
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          Audio
          <select className="input mt-1" value={current.audio_codec} onChange={(event) => field("audio_codec", event.target.value)}>
            {["copy", "aac", "opus", "mp3", "none"].map((codec) => <option key={codec} value={codec}>{codec}</option>)}
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          Audio bitrate
          <input className="input mt-1" value={current.audio_bitrate} onChange={(event) => field("audio_bitrate", event.target.value)} />
        </label>
        <label className="block text-xs text-slate-400">
          Subtitles
          <select className="input mt-1" value={current.subtitles} onChange={(event) => field("subtitles", event.target.value)}>
            {["copy", "burn", "none", "extract"].map((mode) => <option key={mode} value={mode}>{mode}</option>)}
          </select>
        </label>
        <label className="block text-xs text-slate-400">
          Watermark text
          <input className="input mt-1" value={current.watermark} placeholder="© your channel"
            onChange={(event) => field("watermark", event.target.value)} />
        </label>
        <label className="block text-xs text-slate-400">
          Watermark position
          <select className="input mt-1" value={current.watermark_position} onChange={(event) => field("watermark_position", event.target.value)}>
            {["top-left", "top-right", "bottom-left", "bottom-right", "center"].map((spot) => <option key={spot} value={spot}>{spot}</option>)}
          </select>
        </label>
      </div>
      <p className="mt-3 text-[11px] text-slate-500">
        Lower CRF means higher quality and larger files. Settings are
        validated server-side; invalid combinations are rejected, not silently corrected.
      </p>
    </Card>
  );
}
