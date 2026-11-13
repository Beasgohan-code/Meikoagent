export type User = {
  id: number;
  telegram_id: number | null;
  username: string | null;
  first_name: string;
  last_name: string;
  photo_url: string | null;
  role: string;
  status: string;
  is_owner: boolean;
  created_at: string;
  last_seen_at: string | null;
};

export type Bot = {
  id: number;
  owner_id: number;
  telegram_bot_id: number | null;
  username: string | null;
  name: string;
  description: string;
  avatar_url: string | null;
  status: "draft" | "stopped" | "starting" | "running" | "error" | string;
  runtime_mode: "polling" | "webhook" | string;
  token_valid: boolean;
  is_clone: boolean;
  source_bot_id: number | null;
  last_started_at: string | null;
  last_stopped_at: string | null;
  last_error: string | null;
  restart_count: number;
  created_at: string;
  settings: Record<string, unknown>;
  limits: Record<string, unknown>;
};

export type Page<T> = { items: T[]; total: number; limit: number; offset: number; has_more: boolean };

export type BotHealth = {
  bot_id: number;
  status: string;
  mode: string;
  running: boolean;
  uptime_seconds: number;
  plugin_count: number;
  last_error: string | null;
  worker: Record<string, unknown> | null;
  available: boolean;
  note: string | null;
};

export type BotLogEntry = {
  id: number;
  kind: string;
  severity: string;
  message: string;
  worker_id: number | null;
  data: Record<string, unknown>;
  created_at: string | null;
};

export type BotLogs = {
  bot_id: number;
  count: number;
  kinds: Record<string, number>;
  items: BotLogEntry[];
};

export type BotUsage = {
  bot_id: number;
  runtime: {
    hosted_here: boolean;
    worker_id: number | null;
    uptime_seconds: number;
    plugins: number;
    handlers: number | null;
    process: { max_rss_mb: number; cpu_seconds: number; pid: number; note: string } | null;
  };
  storage: {
    disk: Record<string, { exists: boolean; bytes: number }>;
    disk_total_bytes: number;
    accounted_bytes: Record<string, number>;
    accounted_total_bytes: number;
  };
  events: { runtime_events: number };
};

export type BotTask = {
  id: number;
  name: string;
  kind: string | null;
  status: string | null;
  cron: string | null;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
};

export type BotTasks = { bot_id: number; count: number; items: BotTask[] };

export type Group = {
  id: number;
  bot_id: number;
  chat_id: number;
  title: string;
  username: string | null;
  chat_type: string;
  member_count: number;
  bot_status: string;
  is_active: boolean;
  last_activity_at: string | null;
};

export type Plugin = {
  id: number;
  slug: string;
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  icon: string;
  source: string;
  status: string;
  is_official: boolean;
  permissions: string[];
  dependencies: string[];
  config_schema: Record<string, ConfigField>;
};

export type ConfigField = {
  type?: string;
  default?: unknown;
  required?: boolean;
  label?: string;
  choices?: string[];
};

export type PluginInstallation = {
  id: number;
  plugin_id: number;
  bot_id: number | null;
  group_id: number | null;
  scope: string;
  state: string;
  version: string;
  config: Record<string, unknown>;
  enabled_at: string | null;
  last_error: string | null;
  plugin?: Plugin | null;
};

export type PluginToggle = {
  slug: string;
  name: string;
  icon: string;
  category: string;
  description: string;
  state: string;
  enabled: boolean;
  installation_id: number | null;
  scope: string | null;
  missing_config: string[];
};

export type CapabilityState = {
  capability: string;
  label: string;
  description: string;
  risk: string;
  plugin: string;
  enabled: boolean;
  allowed_telegram_ids: number[];
  allowed_bot_ids: number[];
  max_runs_per_day: number;
  updated_by: number | null;
  updated_at: string;
};

export type ActivityItem = {
  id: number;
  kind: string;
  title: string;
  severity: string;
  bot_id: number | null;
  group_id: number | null;
  created_at: string;
  data: Record<string, unknown>;
};

export type Dashboard = {
  bots: { total: number; clones: number; running: number; stopped: number; error: number };
  groups: number;
  plugins_enabled: number;
  moderation_actions: number;
  activity_series: { date: string; count: number }[];
};

export type ModerationAction = {
  id: number;
  bot_id: number;
  group_id: number | null;
  chat_id: number | null;
  target_id: number | null;
  moderator_id: number | null;
  action: string;
  reason: string;
  duration_seconds: number;
  source: string;
  created_at: string;
};

export type RuntimeHealth = {
  status: string;
  services: {
    database: { status: string; dialect?: string };
    cache: { status: string; backend?: string };
    workers: { local: string; running_bots: number; capacity: number };
    storage: { data_dir: string; free_bytes: number; total_bytes: number };
    process: { uptime_seconds: number; cpu_percent?: number; rss_bytes?: number };
  };
  version: string;
  bot_api: string;
  timestamp: string;
};

export type ApiKey = {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
};

export type Workflow = {
  id: number;
  bot_id: number;
  name: string;
  description: string;
  enabled: boolean;
  trigger: Record<string, unknown>;
  conditions: unknown[];
  actions: unknown[];
  version: number;
  run_count: number;
  last_run_at: string | null;
};

export type Schedule = {
  id: number;
  bot_id: number | null;
  name: string;
  kind: string;
  cron: string | null;
  next_run_at: string | null;
  status: string;
  attempts: number;
  last_error: string | null;
};
