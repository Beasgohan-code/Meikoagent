/** Plugin toggle helpers shared by the platform pages. */

"use client";

import { api } from "@/lib/api";
import type { PluginToggle } from "@/lib/types";
import type { EffectivePlugins } from "@/lib/modules";

export type { PluginToggle };

export const pluginsApi = {
  toggles: (botId?: number) =>
    api.get<PluginToggle[]>(`/plugins/toggles${botId ? `?bot_id=${botId}` : ""}`),
  toggle: (slug: string, botId: number | null, enabled: boolean) =>
    api.post<{ slug: string; state: string; enabled: boolean; runtime: Record<string, unknown> }>(
      `/plugins/${slug}/toggle?${botId ? `bot_id=${botId}&` : ""}enabled=${enabled}`,
    ),
};

export const effectiveStates = () => api.get<EffectivePlugins>("/plugins/effective");
