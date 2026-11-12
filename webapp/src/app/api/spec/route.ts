/** Same-origin OpenAPI document, proxied from the FastAPI control plane. */

export const dynamic = "force-dynamic";

export async function GET() {
  const base = (process.env.MEIKO_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
  try {
    const upstream = await fetch(`${base}/openapi.json`, { cache: "no-store" });
    if (!upstream.ok) throw new Error(String(upstream.status));
    return new Response(await upstream.text(), {
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch {
    return Response.json(
      { detail: { error: "backend_unreachable", message: "the API service is not reachable" } },
      { status: 502 },
    );
  }
}
