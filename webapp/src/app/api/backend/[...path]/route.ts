/**
 * Same-origin proxy to the FastAPI control plane.
 *
 * The server resolves the backend URL from `MEIKO_API_URL` at request time, so
 * a single build works locally (127.0.0.1:8000) and in Docker (api:8000) with
 * no browser-side CORS or hostname assumptions. Streaming responses (SSE) are
 * piped through untouched.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function backendBase(): string {
  return (process.env.MEIKO_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function proxy(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = new URL(request.url);
  const target = `${backendBase()}/api/v1/${path.join("/")}${url.search}`;

  const headers = new Headers();
  // The session travels as an httpOnly cookie; the proxy must carry it both ways
  // so the browser never has to hold the token in script-readable storage.
  const cookie = request.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const authorization = request.headers.get("authorization");
  const apiKey = request.headers.get("x-api-key");
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");
  if (authorization) headers.set("authorization", authorization);
  if (apiKey) headers.set("x-api-key", apiKey);
  if (contentType) headers.set("content-type", contentType);
  if (accept) headers.set("accept", accept);

  const hasBody = !["GET", "HEAD", "OPTIONS"].includes(request.method);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return Response.json(
      { detail: { error: "backend_unreachable", message: "the API service is not reachable" } },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of ["content-type", "cache-control", "x-response-time", "www-authenticate"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  const cookies = upstream.headers.getSetCookie?.() ?? [];
  for (const value of cookies) responseHeaders.append("set-cookie", value);
  if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
    responseHeaders.set("x-accel-buffering", "no");
    responseHeaders.set("connection", "keep-alive");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
export { proxy as OPTIONS };
