/**
 * Typed client + hooks for the six media modules.
 *
 * Every helper here calls a real backend route; nothing is mocked. Where the
 * backend can refuse (a stopped bot cannot be paused), the shell shows the
 * backend's own reason instead of a fake success.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

// ------------------------------------------------------------------ types
export type QueueTrack = {
  title: string;
  source: string;
  kind: string;
  duration: number;
  uploader: string;
  thumbnail: string;
  requested_by: string;
  requested_by_id: number | string;
  url: string;
  height: number;
  file_id: string;
};

export type RoomSnapshot = {
  chat_id: number;
  current: QueueTrack | null;
  items: QueueTrack[];
  history: QueueTrack[];
  paused: boolean;
  position: number;
  loop_count: number;
  loop_queue: boolean;
  autoplay: boolean;
  volume: number;
  mode: string;
  updates: number;
  updated_at?: number;
};

export type MusicAnalytics = {
  rooms: number;
  active_rooms: number;
  plays: number;
  seconds: number;
  playlists: number;
  listening_rooms: number;
  top_tracks: { title: string; plays: number }[];
};

export type MusicSessionsResponse = {
  rooms: RoomSnapshot[];
  analytics: MusicAnalytics;
  transport_ready: boolean;
};

export type Listener = { user_id: number; name: string; seen: number };

export type Playlist = {
  name: string;
  owner_id: number;
  chat_id: number;
  tracks: QueueTrack[];
  created_at: number;
  updated_at: number;
};

export type TransportResponse = {
  applied: boolean;
  reason?: string;
  action?: string;
  room?: RoomSnapshot;
  state?: RoomSnapshot;
};

export type LedgerJob = {
  id: string;
  module: string;
  kind: string;
  owner_id: number;
  scope: string;
  state: string;
  progress: number;
  detail: string;
  error: string;
  meta: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  finished_at: number;
  seconds: number;
};

export type JobStats = {
  queued: number;
  running: number;
  done: number;
  failed: number;
  cancelled: number;
  total: number;
  avg_seconds: number;
  success_rate: number;
};

export type EncoderJobs = {
  jobs: LedgerJob[];
  active: LedgerJob[];
  stats: JobStats;
  tools: { available: boolean; missing: string[] };
};

export type EncoderProfile = {
  codec: string;
  crf: number;
  preset: string;
  container: string;
  resolution: string;
  audio_codec: string;
  audio_bitrate: string;
  channels: string;
  subtitles: string;
  ten_bit: boolean;
  watermark: string;
  watermark_position: string;
  thumbnail: boolean;
};

export type ShopUsage = {
  files: number;
  bytes: number;
  downloads: number;
  users: number;
  admins: number;
  banned: number;
  batches: number;
  links: number;
};

export type Shop = {
  slug: string;
  bot_id: number;
  title: string;
  active: boolean;
  usage: ShopUsage;
};

export type ShopFile = {
  id: string;
  name: string;
  size: number;
  mime: string;
  visibility: string;
  tags: string[];
  created_at: number;
  expires_at: number;
  downloads: number;
  batch: string;
};

export type ShareLink = {
  code: string;
  url: string;
  owner_id: number;
  bot_id: number;
  title: string;
  created_at: number;
  expires_at: number;
  max_claims: number;
  claims: number;
  refusals: number;
  revoked: boolean;
  batch: string;
  state: string;
  verdicts: Record<string, number>;
  file_name: string;
};

export type LinkshareAnalytics = {
  total: number;
  active: number;
  expired: number;
  revoked: number;
  exhausted: number;
  claims: number;
  refusals: number;
  batches: number;
  verdicts: Record<string, number>;
  note: string;
};

export type LeechLiveTask = {
  id: string;
  url: string;
  user_id: number;
  chat_id: number;
  kind: string;
  state: string;
  downloaded: number;
  total: number;
  percent: number;
  speed: number;
  eta: number;
  destination: string;
  error: string;
};

export type LeechJobs = {
  bot_running: boolean;
  jobs: LedgerJob[];
  stats: JobStats;
  live: LeechLiveTask[];
  engine: {
    downloaders: { name: string; available: boolean }[];
    uploaders: { name: string; available: boolean }[];
  };
};

export type SaverOverview = {
  config: Record<string, unknown>;
  library: { count: number; bytes: number; human: string };
  jobs: LedgerJob[];
  policy: { authorized_sources_only: boolean; note: string };
};

export type SaverHistory = {
  saved: {
    chat: string;
    message_id: number;
    filename: string;
    size: number;
    kind: string;
    saved_at: string;
    unique_id: string;
  }[];
  total: number;
};

export type EffectivePlugins = {
  bots: Record<string, { running: boolean; plugins: Record<string, { state: string; enabled: boolean; scope: string; installation_id: number; running: boolean }> }>;
};

export type PlatformModules = {
  modules: Record<string, { scopes: string[]; events: number }>;
  events: { subscribers: number; topics: Record<string, number>; buffered: number };
  storage: { total: number; total_human: string; modules: Record<string, { bytes: number; human: string }>; owners: { owner_id: number; bytes: number; human: string }[]; owner_count: number };
  mtproto: { available: boolean; client: string; reason: string; streaming: boolean; large_uploads: boolean };
  encoder_tools: { available: boolean; missing: string[] };
  server_time: number;
};

export type PlatformStorage = {
  per_bot: Record<string, { total: number; total_human: string; modules: Record<string, { bytes: number; human: string }> }>;
  platform: { total: number; total_human: string; owner_count: number };
  bots: number[];
};

// ------------------------------------------------------------------ hooks
const Q = { refetchOnWindowFocus: false } as const;

export function useBots() {
  return useQuery({ queryKey: ["bots"], queryFn: () => api.get<import("./types").Page<import("./types").Bot>>("/bots"), ...Q });
}

export function useEffectivePlugins() {
  return useQuery({ queryKey: ["plugins-effective"], queryFn: () => api.get<EffectivePlugins>("/plugins/effective"), ...Q });
}

/** One bot id for the whole app: newest owned bot unless the URL overrides it. */
export function useActiveBot(preferred?: string | null) {
  const bots = useBots();
  const list = bots.data?.items ?? [];
  const chosen = preferred ? list.find((bot) => String(bot.id) === preferred) : list[0];
  return { bots: list, bot: chosen ?? null, loading: bots.isLoading, error: bots.error as Error | null };
}

export const music = {
  sessions: (botId: number) => api.get<MusicSessionsResponse>(`/music/sessions?bot_id=${botId}`),
  room: (botId: number, chatId: number) =>
    api.get<{ room: RoomSnapshot; listeners: Listener[] }>(`/music/room?bot_id=${botId}&chat_id=${chatId}`),
  transport: (botId: number, chatId: number, action: string, value?: unknown) =>
    api.post<TransportResponse>(`/music/transport?bot_id=${botId}`, { chat_id: chatId, action, value }),
  playlists: (botId: number) => api.get<{ playlists: Record<string, Playlist> }>(`/music/playlists?bot_id=${botId}`),
  savePlaylist: (botId: number, name: string, tracks: QueueTrack[], chatId = 0) =>
    api.post<{ saved: boolean; playlist: Playlist }>(`/music/playlists?bot_id=${botId}`, { name, tracks, chat_id: chatId }),
  deletePlaylist: (botId: number, name: string) =>
    api.del<{ deleted: boolean }>(`/music/playlists/${encodeURIComponent(name)}?bot_id=${botId}`),
  analytics: (botId: number) => api.get<MusicAnalytics>(`/music/analytics?bot_id=${botId}`),
};

export const encoder = {
  jobs: (botId: number) => api.get<EncoderJobs>(`/encoder/jobs?bot_id=${botId}`),
  presets: () => api.get<{ codecs: string[]; presets: string[]; containers: string[]; resolutions: string[]; tools: { available: boolean; missing: string[] } }>("/encoder/presets"),
  profile: (botId: number) => api.get<{ profile: Partial<EncoderProfile>; tools: { available: boolean; missing: string[] } }>(`/encoder/profile?bot_id=${botId}`),
  saveProfile: (botId: number, profile: EncoderProfile) =>
    api.patch<{ saved: boolean; profile: EncoderProfile }>(`/encoder/profile?bot_id=${botId}`, profile),
  cancel: (botId: number, jobId: string) =>
    api.post<{ cancelled: boolean; applied: boolean }>(`/encoder/jobs/${jobId}/cancel?bot_id=${botId}`),
};

export const filestore = {
  bots: () => api.get<{ shops: Shop[]; owned_bots: number[] }>("/filestore/bots"),
  detail: (slug: string) => api.get<{ shop: Shop & Record<string, unknown> }>(`/filestore/${slug}`),
  files: (slug: string, query = "", page = 1) =>
    api.get<{ files: ShopFile[]; total: number; page: number }>(`/filestore/${slug}/files?query=${encodeURIComponent(query)}&page=${page}`),
  createShop: (botId: number, title: string) => api.post<{ shop: Shop }>("/filestore/shops", { bot_id: botId, title }),
  link: (slug: string, fileId: string, ttlDays = 30, maxUses = 0) =>
    api.post<{ link: { token: string; start: string; file: string; expires_at: number } }>(`/filestore/${slug}/links`, { file_id: fileId, ttl_days: ttlDays, max_uses: maxUses }),
  users: (slug: string) => api.get<{ users: { user_id: number; role: string }[]; admins: number[]; banned: number[] }>(`/filestore/${slug}/users`),
  settings: (slug: string, settings: Record<string, unknown>, messages: Record<string, string>) =>
    api.patch<{ saved: boolean }>(`/filestore/${slug}/settings`, { settings, messages }),
  admin: (slug: string, userId: number, action: string) =>
    api.post<{ admins: number[]; banned: number[] }>(`/filestore/${slug}/admins`, { user_id: userId, action }),
};

export const linkshare = {
  links: (botId: number, state = "") =>
    api.get<{ links: ShareLink[]; analytics: LinkshareAnalytics }>(`/linkshare/links?bot_id=${botId}&state=${state}`),
  create: (botId: number, url: string, title = "", ttlHours = 24, maxClaims = 0) =>
    api.post<{ link: ShareLink }>(`/linkshare/links?bot_id=${botId}`, { url, title, ttl_hours: ttlHours, max_claims: maxClaims }),
  revoke: (botId: number, code: string) => api.post<{ revoked: boolean }>(`/linkshare/links/${code}/revoke?bot_id=${botId}`),
  batch: (botId: number, urls: string[], ttlHours = 24) =>
    api.post<{ batch: string; links: ShareLink[] }>(`/linkshare/batch?bot_id=${botId}`, { urls, ttl_hours: ttlHours }),
  analytics: (botId: number) => api.get<LinkshareAnalytics>(`/linkshare/analytics?bot_id=${botId}`),
  settings: (botId: number) =>
    api.get<{ settings: Record<string, unknown>; defaults: Record<string, unknown> }>(`/linkshare/settings?bot_id=${botId}`),
  saveSettings: (botId: number, patch: Record<string, unknown>) =>
    api.patch<{ saved: boolean; settings: Record<string, unknown> }>(`/linkshare/settings?bot_id=${botId}`, patch),
};

export const leech = {
  jobs: (botId: number) => api.get<LeechJobs>(`/leech/jobs?bot_id=${botId}`),
  sources: () => api.get<{ engine: LeechJobs["engine"]; mtproto: PlatformModules["mtproto"] }>("/leech/sources"),
  validate: (botId: number, source: string) =>
    api.post<{ valid: boolean; reason?: string; downloader?: string; info?: Record<string, unknown> }>(`/leech/validate?bot_id=${botId}`, { source }),
  retry: (botId: number, jobId: string) => api.post<{ retried: boolean; reason?: string }>(`/leech/jobs/${jobId}/retry?bot_id=${botId}`),
};

export const saver = {
  overview: (botId: number) => api.get<SaverOverview>(`/saver/overview?bot_id=${botId}`),
  history: (botId: number) => api.get<SaverHistory>(`/saver/history?bot_id=${botId}`),
  settings: (botId: number, patch: Record<string, unknown>) => api.patch<{ saved: boolean; config: Record<string, unknown> }>(`/saver/settings?bot_id=${botId}`, patch),
  forget: (botId: number, messageId: number) => api.del<{ removed: boolean }>(`/saver/history/${messageId}?bot_id=${botId}`),
  retention: (botId: number, keepDays: number) => api.post<{ kept: number; removed: number }>(`/saver/retention?bot_id=${botId}&keep_days=${keepDays}`),
  exportData: (botId: number) => api.get<Record<string, unknown>>(`/saver/export?bot_id=${botId}`),
  importData: (botId: number, payload: Record<string, unknown>) =>
    api.post<{ imported: number; skipped: number; total: number }>(`/saver/import?bot_id=${botId}`, payload),
};

export const platform = {
  modules: () => api.get<PlatformModules>("/platform/modules"),
  storage: () => api.get<PlatformStorage>("/platform/storage"),
  jobs: () => api.get<{ jobs: LedgerJob[]; count: number }>("/platform/jobs"),
};

/**
 * Live Music Studio stream. Returns a disposer.
 *
 * Preferred transport is the WebSocket (`/music/live`) because it is
 * bidirectional and cheaper. Some deployments sit behind a proxy that cannot
 * upgrade a connection (the Next.js route handler in front of the API cannot),
 * so the socket is only used when it is reachable on this origin; otherwise the
 * same hub is consumed over Server-Sent Events at `/music/stream`. Both carry
 * identical payloads, so the UI does not care which one is live.
 */
export function openMusicSocket(token: string, onEvent: (event: Record<string, unknown>) => void): () => void {
  if (typeof window === "undefined") return () => {};
  let closed = false;
  const direct = directBackendBase();
  const abort = new AbortController();

  const consume = (payload: string) => {
    try {
      onEvent(JSON.parse(payload) as Record<string, unknown>);
    } catch {
      /* a malformed frame must not kill the stream */
    }
  };

  const runSse = async (base: string) => {
    const response = await fetch(`${base}/music/stream`, {
      headers: { authorization: `Bearer ${token}`, accept: "text/event-stream" },
      signal: abort.signal,
      cache: "no-store",
    });
    if (!response.ok || !response.body) return;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!closed) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("");
        if (data) consume(data);
      }
    }
  };

  const runSocket = (base: string) => {
    const url = `${base.replace(/^http/, "ws")}/music/live?token=${encodeURIComponent(token)}`;
    const socket = new WebSocket(url);
    socket.onmessage = (message) => consume(String(message.data));
    socket.onerror = () => {
      socket.close();
      if (!closed) void runSse(base).catch(() => undefined);
    };
  };

  if (direct) {
    runSocket(direct);
  } else {
    void runSse("/api/backend").catch(() => undefined);
  }

  return () => {
    closed = true;
    abort.abort();
  };
}

/** Optional explicit backend origin for WebSocket use (set at build time). */
function directBackendBase(): string {
  const configured = process.env.NEXT_PUBLIC_MEIKO_WS_URL;
  if (!configured) return "";
  return configured.replace(/\/+$/, "");
}
