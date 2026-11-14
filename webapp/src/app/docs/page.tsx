"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowLeft, Braces, ExternalLink, Lock, Terminal } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, ErrorNote, SectionHeader, Skeleton } from "@/components/ui";
import { CodeBlock } from "@/components/patterns";

type ApiReference = {
  version: string;
  prefix: string;
  bot_api: string;
  auth: string[];
  groups: Record<string, string[]>;
  errors: Record<string, string>;
};

export default function DocsPage() {
  const reference = useQuery({ queryKey: ["api-reference"], queryFn: () => api.get<ApiReference>("/developer/api-reference") });

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <Link href="/dashboard" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
        <ArrowLeft className="h-3 w-3" /> Dashboard
      </Link>
      <SectionHeader
        title="API documentation"
        description="The FastAPI service publishes OpenAPI. This page summarises the surface and links to the generated spec."
        action={
          <div className="flex gap-2">
            <a className="btn-ghost" href="/api/spec" target="_blank" rel="noreferrer">
              <Braces className="h-4 w-4" /> OpenAPI JSON
            </a>
            <a className="btn-ghost" href="/api/docs" target="_blank" rel="noreferrer">
              <ExternalLink className="h-4 w-4" /> Operation index
            </a>
          </div>
        }
      />

      {reference.isError ? <ErrorNote message="API reference unavailable." onRetry={() => reference.refetch()} /> : null}

      <Card>
        <p className="flex items-center gap-2 text-sm font-medium text-white">
          <Lock className="h-4 w-4 text-emerald-300" /> Authentication
        </p>
        <p className="mt-1 text-xs text-slate-500">
          Two credential types are accepted. Identity and role are always resolved from the database row bound to the
          credential — client-supplied ids or roles are ignored.
        </p>
        <div className="mt-3 space-y-2">
          {(reference.data?.auth ?? ["Authorization: Bearer <session>", "X-API-Key: mk_..."]).map((line) => (
            <div key={line} className="rounded-lg border border-white/[0.06] px-3 py-2 font-mono text-xs text-slate-300">
              {line}
            </div>
          ))}
        </div>
        <div className="mt-3">
          <CodeBlock title="curl">{`curl -s $MEIKO_API/api/v1/bots \\
  -H "Authorization: Bearer $MEIKO_SESSION"

curl -s $MEIKO_API/api/v1/dashboard \\
  -H "X-API-Key: $MEIKO_API_KEY"`}</CodeBlock>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {reference.isLoading ? (
          <>
            <Skeleton className="h-40" />
            <Skeleton className="h-40" />
          </>
        ) : (
          Object.entries(reference.data?.groups ?? {}).map(([group, endpoints]) => (
            <Card key={group}>
              <div className="flex items-center justify-between">
                <p className="flex items-center gap-2 text-sm font-medium capitalize text-white">
                  <Terminal className="h-4 w-4 text-violet-300" /> {group}
                </p>
                <Badge status="info">{endpoints.length}</Badge>
              </div>
              <ul className="mt-2 space-y-1">
                {endpoints.map((endpoint) => {
                  const [method, ...rest] = endpoint.split(" ");
                  return (
                    <li key={endpoint} className="flex items-center gap-2 font-mono text-[11.5px]">
                      <span
                        className={
                          method.includes("POST")
                            ? "text-emerald-300"
                            : method.includes("PATCH")
                              ? "text-amber-300"
                              : "text-cyan-300"
                        }
                      >
                        {method}
                      </span>
                      <span className="truncate text-slate-300">{rest.join(" ")}</span>
                    </li>
                  );
                })}
              </ul>
            </Card>
          ))
        )}
      </div>

      <Card>
        <p className="flex items-center gap-2 text-sm font-medium text-white">
          <Braces className="h-4 w-4 text-violet-300" /> Error envelope
        </p>
        <p className="mt-1 text-xs text-slate-500">
          Errors are structured and machine-readable; messages never include tokens, secrets or stack traces.
        </p>
        <div className="mt-3">
          <CodeBlock title="error.json">{`{
  "detail": {
    "error": "config_required",
    "message": "plugin requires configuration before it can be enabled"
  }
}`}</CodeBlock>
        </div>
        <ul className="mt-3 space-y-1 text-xs">
          {Object.entries(reference.data?.errors ?? {}).map(([code, description]) => (
            <li key={code} className="flex gap-2">
              <span className="w-10 shrink-0 font-mono text-rose-300">{code}</span>
              <span className="text-slate-400">{description}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <p className="text-sm font-medium text-white">Platform surface</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {[
            ["Bot API version", reference.data?.bot_api ?? "10.4"],
            ["API version", reference.data?.version ?? "v1"],
            ["Base prefix", reference.data?.prefix ?? "/api/v1"],
            ["Health probes", "/health, /health/live, /ready"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-white/[0.06] px-3 py-2">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
              <p className="mt-0.5 font-mono text-xs text-slate-200">{value}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Plugin authors start at <span className="kbd">meiko/plugins/base.py</span> and publish a typed manifest;
          runtime code is never uploaded from a browser.
        </p>
      </Card>
    </div>
  );
}
