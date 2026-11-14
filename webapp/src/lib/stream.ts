"use client";

import { getToken } from "@/lib/api";

type StreamHandlers = {
  onEvent: (payload: Record<string, unknown>) => void;
  onStatus?: (status: "connecting" | "open" | "closed" | "error") => void;
};

const BASE = "/api/backend";

/**
 * Server-Sent Events consumer.
 *
 * The browser `EventSource` API cannot set an Authorization header, so the
 * stream is consumed through `fetch` with the session bearer token. This keeps
 * the SSE endpoint authenticated exactly like the rest of the API.
 */
export function openActivityStream({ onEvent, onStatus }: StreamHandlers): () => void {
  const controller = new AbortController();
  let closed = false;

  async function run() {
    onStatus?.("connecting");
    try {
      const token = getToken();
      const response = await fetch(`${BASE}/activity/stream`, {
        headers: token ? { authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok || !response.body) {
        onStatus?.("error");
        return;
      }
      onStatus?.("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const dataLines = frame
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim());
          if (!dataLines.length) continue;
          const raw = dataLines.join("\n");
          try {
            onEvent(JSON.parse(raw) as Record<string, unknown>);
          } catch {
            /* keep-alive or malformed frame – ignore */
          }
        }
      }
      onStatus?.("closed");
    } catch (error) {
      if (!closed && (error as Error).name !== "AbortError") onStatus?.("error");
    }
  }

  void run();

  return () => {
    closed = true;
    controller.abort();
    onStatus?.("closed");
  };
}
