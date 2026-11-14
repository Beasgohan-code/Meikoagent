"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Boxes, Download, Save, ShieldCheck, Sliders } from "lucide-react";
import { api } from "@/lib/api";
import type { Bot, ConfigField, Page, Plugin, PluginInstallation } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, Field, Skeleton, Toggle } from "@/components/ui";
import { CodeBlock } from "@/components/patterns";
import { emoji } from "@/lib/premium-emoji";

type PluginDetail = Plugin & {
  implemented: boolean;
  installable: boolean;
  note: string | null;
  manifest: Record<string, unknown> & { commands?: string[]; events?: string[] };
};

export default function PluginDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [botId, setBotId] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});

  const plugin = useQuery({ queryKey: ["plugin", slug], queryFn: () => api.get<PluginDetail>(`/plugins/${slug}`) });
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const botList = bots.data?.items ?? [];
  // Global scope is a platform-owner capability, so the ordinary marketplace
  // never offers it: an installation always has a bot. Installations that apply
  // to every bot are managed from the Owner Control Center instead.
  const needsBot = !botId;
  const installed = useQuery({
    queryKey: ["plugins-installed", botId],
    queryFn: () => api.get<PluginInstallation[]>(`/plugins/installed${botId ? `?bot_id=${botId}` : ""}`),
  });

  const installation = (installed.data ?? []).find((row) => row.plugin_id === plugin.data?.id);

  const install = useMutation({
    mutationFn: () => {
      if (!botId) throw new Error("Choose the bot this plugin should run on first.");
      return api.post<PluginInstallation>(`/plugins/${slug}/install`, { bot_id: Number(botId) });
    },
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const configure = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.patch<PluginInstallation>(`/plugins/installations/${installation!.id}/configure`, {
        config: payload,
      }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enable }: { id: number; enable: boolean }) =>
      api.post(`/plugins/installations/${id}/${enable ? "enable" : "disable"}`),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  if (plugin.isError) {
    return (
      <div className="space-y-3">
        <Link href="/plugins" className="btn-ghost">
          <ArrowLeft className="h-4 w-4" /> Marketplace
        </Link>
        <ErrorNote message="This plugin could not be loaded." onRetry={() => plugin.refetch()} />
      </div>
    );
  }
  if (plugin.isLoading || !plugin.data) return <Skeleton className="h-64" />;

  const record = plugin.data;
  const schema = Object.entries(record.config_schema ?? {});
  const values = { ...Object.fromEntries(schema.map(([key, field]) => [key, field.default])), ...(installation?.config ?? {}), ...config };

  return (
    <div className="space-y-5">
      <Link href="/plugins" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
        <ArrowLeft className="h-3 w-3" /> Marketplace
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-violet-500/25 to-indigo-500/15 text-2xl">
            {emoji(record.icon)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-white">{record.name}</h1>
              <Badge status={record.status}>{record.status}</Badge>
              {record.is_official ? <Badge status="info">official</Badge> : null}
            </div>
            <p className="mt-1 text-sm text-slate-500">
              v{record.version} · {record.author} · {record.category} · {record.source}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input max-w-[220px]"
            value={botId}
            onChange={(event) => setBotId(event.target.value)}
            aria-label="Bot to install this plugin on"
          >
            <option value="">Select a bot…</option>
            {botList.map((bot) => (
              <option key={bot.id} value={String(bot.id)}>
                {bot.name}
              </option>
            ))}
          </select>
          {installation ? (
            <button
              className={["enabled", "configured", "running"].includes(installation.state) ? "btn-danger" : "btn-primary"}
              onClick={() => toggle.mutate({ id: installation.id, enable: !["enabled", "configured", "running"].includes(installation.state) })}
            >
              {["enabled", "configured", "running"].includes(installation.state) ? "Disable" : "Enable"}
            </button>
          ) : (
            <button
              className="btn-primary"
              onClick={() => install.mutate()}
              disabled={install.isPending || !record.installable || needsBot}
              title={needsBot ? "Pick a bot first" : undefined}
            >
              <Download className="h-4 w-4" /> Install
            </button>
          )}
        </div>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      {!record.implemented ? (
        <Card className="border-amber-500/25 bg-amber-500/[0.04]">
          <p className="text-sm font-medium text-amber-200">No runtime implementation is registered</p>
          <p className="mt-1 text-sm text-amber-100/70">{record.note}</p>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <p className="text-sm font-medium text-white">About</p>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{record.description}</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <div className="rounded-xl border border-white/[0.06] px-3 py-2">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Permissions</p>
                <p className="mt-1 text-xs text-slate-300">{record.permissions.join(", ") || "none requested"}</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] px-3 py-2">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Dependencies</p>
                <p className="mt-1 text-xs text-slate-300">{record.dependencies.join(", ") || "none"}</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] px-3 py-2">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Commands</p>
                <p className="mt-1 font-mono text-xs text-slate-300">
                  {(record.manifest?.commands ?? []).map((command) => `/${command}`).join(" ") || "—"}
                </p>
              </div>
              <div className="rounded-xl border border-white/[0.06] px-3 py-2">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Events</p>
                <p className="mt-1 font-mono text-xs text-slate-300">{(record.manifest?.events ?? []).join(", ") || "—"}</p>
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center gap-2">
              <Sliders className="h-4 w-4 text-violet-300" />
              <p className="text-sm font-medium text-white">Configuration</p>
            </div>
            {!installation ? (
              <p className="mt-2 text-sm text-slate-500">Install this plugin to edit its configuration.</p>
            ) : schema.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">This plugin has no configurable options.</p>
            ) : (
              <div className="mt-4 space-y-3">
                {schema.map(([key, field]) => (
                  <ConfigInput
                    key={key}
                    name={key}
                    field={field}
                    value={values[key]}
                    onChange={(value) => setConfig((current) => ({ ...current, [key]: value }))}
                  />
                ))}
                <button
                  className="btn-primary"
                  onClick={() => configure.mutate({ ...(installation.config ?? {}), ...config })}
                  disabled={configure.isPending || Object.keys(config).length === 0}
                >
                  <Save className="h-4 w-4" /> Save configuration
                </button>
                <p className="text-xs text-slate-500">
                  Configuration is validated against the manifest schema; unknown keys and wrong types are rejected.
                </p>
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <p className="text-sm font-medium text-white">Lifecycle</p>
            <ol className="mt-3 space-y-2 text-xs">
              {["available", "installed", "enabled", "configured", "running"].map((state, index) => {
                const order = ["available", "installed", "enabled", "configured", "running"];
                const current = installation?.state ?? "available";
                const reached = order.indexOf(current) >= index;
                return (
                  <li key={state} className="flex items-center gap-2">
                    <span
                      className={
                        reached
                          ? "grid h-5 w-5 place-items-center rounded-full bg-violet-500/25 text-[10px] text-violet-200"
                          : "grid h-5 w-5 place-items-center rounded-full border border-white/10 text-[10px] text-slate-500"
                      }
                    >
                      {index + 1}
                    </span>
                    <span className={reached ? "text-slate-200" : "text-slate-500"}>{state}</span>
                  </li>
                );
              })}
            </ol>
            <p className="mt-3 flex items-start gap-2 text-xs text-slate-500">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 text-emerald-300" />
              Scope resolution order is group → bot → global.
            </p>
          </Card>

          <Card>
            <p className="text-sm font-medium text-white">Manifest</p>
            <p className="mb-2 text-xs text-slate-500">Typed manifest as stored in the database.</p>
            <CodeBlock title="manifest.json">{JSON.stringify(record.manifest ?? {}, null, 2)}</CodeBlock>
          </Card>
        </div>
      </div>
    </div>
  );
}

function ConfigInput({
  name,
  field,
  value,
  onChange,
}: {
  name: string;
  field: ConfigField;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const label = field.label ?? name;
  if (field.type === "bool" || field.type === "boolean") {
    return (
      <div className="flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2.5">
        <div>
          <p className="text-sm text-slate-200">{label}</p>
          <p className="font-mono text-[11px] text-slate-500">{name}</p>
        </div>
        <Toggle checked={Boolean(value)} onChange={onChange} label={label} />
      </div>
    );
  }
  if (field.choices?.length) {
    return (
      <Field label={label} hint={name}>
        <select className="input" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
          {field.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      </Field>
    );
  }
  if (field.type === "int" || field.type === "integer" || field.type === "float" || field.type === "number") {
    return (
      <Field label={label} hint={name}>
        <input
          className="input"
          type="number"
          value={value === undefined || value === null ? "" : Number(value)}
          onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
        />
      </Field>
    );
  }
  if (field.type === "list" || field.type === "array" || field.type === "text" || field.type === "textarea") {
    return (
      <Field label={label} hint={name}>
        <textarea
          className="input min-h-[72px]"
          value={Array.isArray(value) ? value.join("\n") : String(value ?? "")}
          onChange={(event) =>
            onChange(field.type === "list" || field.type === "array" ? event.target.value.split("\n") : event.target.value)
          }
        />
      </Field>
    );
  }
  return (
    <Field label={label} hint={name}>
      <input className="input" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />
    </Field>
  );
}
