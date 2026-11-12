"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { KeyRound, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import type { Bot } from "@/lib/types";
import { Card, ErrorNote, Field, SectionHeader } from "@/components/ui";

type Template = { id: number; slug: string; name: string; description: string; plugins: string[] };

export default function CreateBotPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("");
  const [mode, setMode] = useState<"polling" | "webhook">("polling");
  const [error, setError] = useState<string | null>(null);

  const templates = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.get<Template[]>("/bot-templates"),
    retry: false,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Bot>("/bots", {
        token: token.trim(),
        name: name.trim() || undefined,
        runtime_mode: mode,
        template_slug: template || undefined,
      }),
    onSuccess: (bot) => router.push(`/bots/${bot.id}`),
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <SectionHeader
        title="Connect a bot"
        description="The token is verified with Telegram getMe on the backend, encrypted with Fernet and never returned to the browser."
      />
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}

      <Card>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            setError(null);
            if (!token.includes(":")) {
              setError("A bot token has the form 123456789:AA…");
              return;
            }
            create.mutate();
          }}
        >
          <Field
            label="Bot token"
            hint="Create or copy it from @BotFather. It is stored encrypted and never displayed again."
          >
            <input
              className="input font-mono"
              type="password"
              autoComplete="off"
              placeholder="123456789:AAH…"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
            />
          </Field>
          <Field label="Display name" hint="Optional – defaults to the Telegram bot name.">
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Runtime mode" hint="Polling is simplest. Webhook mode needs a public HTTPS endpoint.">
            <select className="input" value={mode} onChange={(event) => setMode(event.target.value as "polling" | "webhook")}>
              <option value="polling">Long polling</option>
              <option value="webhook">Webhook</option>
            </select>
          </Field>
          <Field label="Template" hint="Preinstall a curated plugin set and configuration.">
            <select className="input" value={template} onChange={(event) => setTemplate(event.target.value)}>
              <option value="">No template</option>
              {(templates.data ?? []).map((item) => (
                <option key={item.slug} value={item.slug}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>

          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs text-slate-400">
            <p className="flex items-center gap-2 font-medium text-slate-300">
              <KeyRound className="h-3.5 w-3.5 text-violet-300" /> Ownership is proven with Telegram
            </p>
            <p className="mt-1">
              The backend calls <span className="kbd">getMe</span> with your token; the bot id, username and name
              all come from Telegram. Nothing in this form can claim an identity.
            </p>
          </div>

          <button className="btn-primary w-full" type="submit" disabled={create.isPending}>
            <Sparkles className="h-4 w-4" />
            {create.isPending ? "Verifying with Telegram…" : "Connect bot"}
          </button>
        </form>
      </Card>
    </div>
  );
}
