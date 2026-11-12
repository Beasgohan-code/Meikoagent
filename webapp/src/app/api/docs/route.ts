/**
 * Minimal, dependency-free OpenAPI viewer.
 *
 * FastAPI's own Swagger UI is served at the API origin; embedding it through a
 * proxy breaks its relative asset paths. This page lists the documented
 * operations straight from the proxied spec so the console stays self-contained.
 */

export const dynamic = "force-dynamic";

type Operation = { method: string; path: string; summary?: string; tags?: string[] };

async function loadOperations(): Promise<{ operations: Operation[]; title: string; version: string }> {
  const base = (process.env.MEIKO_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
  const upstream = await fetch(`${base}/openapi.json`, { cache: "no-store" });
  if (!upstream.ok) throw new Error(String(upstream.status));
  const spec = (await upstream.json()) as {
    info: { title: string; version: string };
    paths: Record<string, Record<string, { summary?: string; tags?: string[] }>>;
  };
  const operations: Operation[] = [];
  for (const [path, methods] of Object.entries(spec.paths)) {
    for (const [method, operation] of Object.entries(methods)) {
      if (!["get", "post", "patch", "put", "delete"].includes(method)) continue;
      operations.push({
        method: method.toUpperCase(),
        path,
        summary: operation.summary,
        tags: operation.tags ?? [],
      });
    }
  }
  return { operations, title: spec.info.title, version: spec.info.version };
}

export async function GET() {
  let operations: Operation[] = [];
  let title = "MEIKO AGENT API";
  let version = "";
  let error = "";
  try {
    const loaded = await loadOperations();
    operations = loaded.operations;
    title = loaded.title;
    version = loaded.version;
  } catch {
    error = "The API service is not reachable; start it with `python start.py --service api`.";
  }

  const rows = operations
    .map(
      (operation) =>
        `<tr><td class="m">${operation.method}</td><td class="p">${operation.path}</td><td>${operation.summary ?? ""}</td></tr>`,
    )
    .join("");

  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>${title} – OpenAPI</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; padding:40px 24px; background:#0c0d12; color:#e6e8ef;
         font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { color:#94a3b8; font-size:13px; margin:0 0 24px; }
  table { border-collapse:collapse; width:100%; max-width:1100px; font-size:12.5px; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid rgba(148,163,184,.12); vertical-align:top; }
  th { color:#64748b; font-weight:500; text-transform:uppercase; letter-spacing:.08em; font-size:10.5px; }
  td.m { font-family: ui-monospace, monospace; color:#67e8f9; width:70px; }
  td.p { font-family: ui-monospace, monospace; color:#cbd5e1; width:380px; }
  .err { border:1px solid rgba(244,63,94,.3); background:rgba(244,63,94,.08); color:#fda4af;
         padding:10px 14px; border-radius:10px; font-size:13px; margin-bottom:18px; }
  a { color:#a78bfa; }
</style></head>
<body>
  <h1>${title}</h1>
  <p class="sub">OpenAPI ${version} · ${operations.length} documented operations · <a href="/api/spec">raw spec</a></p>
  ${error ? `<div class="err">${error}</div>` : ""}
  <table><thead><tr><th>Method</th><th>Path</th><th>Summary</th></tr></thead><tbody>${rows}</tbody></table>
</body></html>`;

  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}
