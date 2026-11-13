<div align="center">

# MEIKO AGENT

**A complete, integrated Telegram ecosystem** — aiogram bot engine, moderation & anti-spam, real plugin
lifecycle, multi-bot hosting, Bot API 10.4 UI engine and a full SaaS control plane.

`Python 3.11+` · `aiogram 3.31` · `FastAPI` · `SQLAlchemy 2` · `Alembic` · `PostgreSQL` + `Redis` · `Next.js 15`

</div>

---

## What this repository is

One platform, not a collection of scripts. The bot is the execution engine, the API is the control
plane, the plugin manager is the extension system and the database is the single source of truth.

| Layer | Path | Responsibility |
| --- | --- | --- |
| Bot engine | `meiko/core`, `meiko/telegram` | aiogram dispatcher, routers, middleware, permissions, command registry, polling **and** webhook, single consumer per token, graceful shutdown |
| Moderation | `meiko/moderation` | anti-spam detectors, escalation, `EVENT → CONDITION → ACTION → AUDIT` rule engine, action executor with guardrails |
| Plugins | `meiko/plugins` | typed manifests, lifecycle states, scope resolution (`group → bot → global`), tenant-isolated execution, 18 built-in plugin **packages** |
| Runtime | `meiko/runtime` | worker manager (capacity, heartbeats, crash-loop protection, recovery), scheduler (cron + retries), watchdog (dead-runtime detection) |
| API | `meiko/api` | FastAPI v1 control plane, bearer sessions + API keys, owner-scoped tenancy, SSE activity stream |
| Data | `meiko/db`, `migrations` | SQLAlchemy 2 async models (32 tables), Alembic migrations, PostgreSQL primary / SQLite fallback |
| UI engine | `meiko/ui` | RichMessage blocks, premium emoji (`Beasgohan-code/Videl0`), buttons/styles, widgets, plain-text fallback |
| Webapp | `webapp` | Next.js 15 console: dashboard, bots, moderation, marketplace, automation, developer, infrastructure, account, owner |

### Integrated by construction

- A plugin that is **disabled registers nothing** — handlers are attached to the dispatcher only while
  the installation is `enabled`/`configured`/`running`.
- Every privileged mutation writes an `audit_logs` row; every moderation action writes a
  `moderation_actions` row *and* an `activity_events` row.
- The webapp calls the FastAPI service through a same-origin proxy (`/api/backend/*`); the browser
  never learns a backend hostname and never handles a bot token.
- Buttons in the console that touch Telegram (start/stop/restart, command sync, plugin enable) call the
  real runtime — when the action cannot succeed the API says so (`running: false`, `token_not_verified`)
  instead of pretending.

---

## Quick start

### 1. Local (SQLite, no external services)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env          # then set AUTH_DEV_MODE=true for local sign-in
python start.py               # API + worker manager + scheduler + watchdog
```

`python start.py` remains the single documented entry point. Open
<http://127.0.0.1:8000/docs> for the API.

Run the console in a second terminal:

```bash
cd webapp
npm install
npm run dev                   # http://localhost:3000
```

Sign in with the development login (Telegram id `1001`) while `AUTH_DEV_MODE=true` and
`ENVIRONMENT` is not `production`.

### 2. Docker Compose (PostgreSQL + Redis + all services)

```bash
cp .env.example .env          # set SECRET_KEY, ENCRYPTION_KEY, BOT_TOKEN, OWNER_TELEGRAM_IDS
docker compose up --build
```

| Service | Command | Port |
| --- | --- | --- |
| `api` | `python start.py --service api` | 8000 |
| `bot` | `python start.py --service bot` | — |
| `scheduler` | `python start.py --service scheduler` | — |
| `watchdog` | `python start.py --service watchdog` | — |
| `migrate` | `python start.py --service migrate` | one-shot |
| `webapp` | `node server.js` (Next standalone) | 3000 |

`AUTO_CREATE_SCHEMA=false` is set in Compose so Alembic owns the schema; the `api` service waits for
the one-shot `migrate` service to complete. Locally the default (`true`) keeps zero-config SQLite
development working.

### 3. Service commands

```bash
python start.py                       # everything (default)
python start.py --service api         # control plane only
python start.py --service bot --bot-id 3
python start.py --service worker      # clone worker: recovers running bots
python start.py --service scheduler
python start.py --service watchdog
python start.py --service migrate     # alembic upgrade head + seed
python start.py --service health      # exit code 0/1 for probes
python start.py --service api --host 0.0.0.0 --port 8001 --reload
```

### 4. Optional demo data

```bash
python scripts/seed_demo.py           # 1 demo bot, 3 groups, moderation history, 4 plugins
python scripts/seed_demo.py --remove  # delete it again
```

Demo rows are real database rows (nothing is mocked in the API) and the bot is flagged `demo_seed`
so it is easy to remove. The demo token is deliberately unverified — the runtime refuses to start it
and reports why.

---

## Architecture

```
                        ┌──────────────────────────────┐
   Telegram  ◀────────▶ │  aiogram runtime (per bot)   │
                        │  dispatcher · routers ·      │
                        │  middleware · permissions    │
                        └───────┬──────────────┬───────┘
                                │              │
                   ┌────────────▼───┐   ┌──────▼────────────┐
                   │ moderation &   │   │ plugin manager    │
                   │ rule engine    │   │ lifecycle + scope │
                   └────────┬───────┘   └──────┬────────────┘
                            │                  │
                        ┌───▼──────────────────▼───┐
                        │ SQLAlchemy 2 (async)     │
                        │ PostgreSQL · SQLite      │
                        └───▲──────────────────▲───┘
                            │                  │
                   ┌────────┴───────┐   ┌──────┴──────────┐
                   │ FastAPI /api/v1│   │ scheduler       │
                   │ control plane  │   │ watchdog        │
                   └────────┬───────┘   └─────────────────┘
                            │  same-origin proxy
                   ┌────────▼────────────────────────────┐
                   │ Next.js console (webapp)            │
                   └─────────────────────────────────────┘
```

### Media modules (`meiko/modules/`)

Six modules sit on one shared infrastructure layer instead of inventing their own:

```
 music    saver    encoder   filestore   linkshare   leech      ← plugins (per bot)
   │        │         │          │            │         │
   └────────┴─────────┴──────────┴────────────┴─────────┘
                        meiko/modules
   store · events · jobs · ledger · progress · media · links · telemetry
                 music.service · filestore · linkshare · leech · mtproto
   │
   ├── FastAPI  /api/v1/{music,encoder,saver,filestore,linkshare,leech,platform}
   └── Next.js  Music Studio · Saver · Encoding Studio · FileStore ·
                LinkShare · Transfer · Platform sections
```

- **One queue, one ledger, one progress renderer, one storage policy** — the spec's "do not build
  six copies" requirement, enforced by `tests/test_modules_contract.py`.
- **A disabled module is inert:** no handlers registered, no background work, and the dashboards
  refuse to show controls (`GET /api/v1/plugins/effective` resolves global → group → bot precedence).
- **Real-time player sync:** WebSocket (`/api/v1/music/live`) with an SSE fallback
  (`/api/v1/music/stream`) for proxies that cannot upgrade; both read the same event hub.
- **Honest degradation:** one resolver (`meiko/modules/toolchain.py`) answers "is ffmpeg here?" for
  the encoder, the player, telemetry and the API; without it a validated plan is printed rather than a
  fake encode. Unconfigured transfer adapters report `available() == false`; link analytics say exactly
  what Telegram can and cannot report.
- Full audit of the six reference repositories, licences, refusals and gaps: **[docs/MODULES.md](docs/MODULES.md)**.
- Deploying it — Compose, a single container, or a Vercel console against a Supabase database: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

### Runtime model

- **One consumer per bot token.** A distributed lock (`bot-start:{id}`) plus a pid file stops two
  processes polling the same token; a second start is refused, not duplicated.
- **Capacity aware.** `max_clone_workers` bounds concurrent bot runtimes on a worker.
- **Crash-loop protection.** Repeated failures inside `clone_crash_loop_window_seconds` mark the bot
  `error` with "manual restart required" instead of restarting forever.
- **Recovery.** `--service worker` re-adopts bots whose database status is `running`.
- **Scheduler.** Cron and one-off tasks are claimed with `scheduled:{id}`, retried with `30s × attempt`
  backoff and finally marked `failed`.
- **Watchdog.** Detects dead runtimes and stale worker heartbeats, recovers bots, writes `watchdog.*`
  runtime events. It deliberately cannot restart its own crashed process — that is the supervisor's job
  (`restart: unless-stopped` / systemd).

### Plugin lifecycle

```
available → installed → enabled → configured → running
                ▲            │
                └── disabled ┘        (uninstall → available)
```

- Manifests are typed (`meiko/plugins/manifest.py`): slug, version, permissions, dependencies, config
  schema, commands, events, resources.
- Scope resolution order is **group → bot → global**; storage keys are `plugin:<slug>:<scope>`.
- `enable` refuses to leave a plugin half-configured: required config triggers `422 config_required`
  with the missing keys.
- Unknown config keys and wrong types are rejected (`422 invalid_config`).

### Moderation & the rule engine

Rules are **data**, not code:

```json
{
  "name": "Block links from new members",
  "event": "message",
  "conditions": [{ "fact": "message.has_link", "op": "is_true" }],
  "actions": [{ "type": "delete" }, { "type": "warn", "reason": "no links" }]
}
```

Facts, operators and action kinds are fixed enumerations; anything else fails validation
(`422 invalid_rule`). Anti-spam detectors (flood, duplicate, links, invites, mentions, keywords, caps,
emoji, suspicious joins) run before configured rules, with escalation thresholds and an action policy.

### Bot API 10.4 & the UI engine

`meiko/ui` builds messages, keyboards and rich blocks; `meiko/ui/premium.py` renders premium emoji
from `Beasgohan-code/Videl0` with a unicode fallback whenever the bot is not eligible. Widget updates
(`widget_query`, `shared_widget_query`) are extracted from raw payloads before aiogram validation so
Bot API 10.4 widgets are handled by a typed adapter, and `allowed_updates` includes them.

### Security model

| Concern | Guarantee |
| --- | --- |
| Identity | Telegram login widget HMAC or Mini App `init_data` signature, verified server-side |
| Sessions | Bearer token, only its hash is stored, expiry + revocation enforced per request |
| Roles | Read from the database row, never from a request body or header |
| Tenancy | Every bot/group/plugin query is scoped by `owner_id`; foreign resources return `404` |
| Secrets | Bot tokens sealed with Fernet; API keys stored hashed; tokens never serialised to the browser |
| Code execution | No uploaded Python, no `eval`/`exec`; declarative commands and workflows are validated data |
| Transport | Same-origin proxy for the console; webhook secret header verified for Telegram updates |
| Module data | JSON documents on disk, or in a Supabase Storage bucket (`MODULE_STORE_URL`) when the host has no durable filesystem — a failed write is reported, never cached as saved |
| Outbound URLs | One policy (`meiko/modules/urls.py`): http(s) only, no credentials, no loopback/private/link-local targets — enforced where the bytes move, and redacted in errors |

---

## API

Base prefix `/api/v1`, OpenAPI at `/openapi.json`, interactive docs at `/docs`. 121 documented paths,
including the media-module surface (`/music`, `/encoder`, `/saver`, `/filestore`, `/linkshare`, `/leech`,
`/platform`) — see `docs/MODULES.md` §7.

```bash
curl -s "$API/api/v1/dashboard" -H "Authorization: Bearer $SESSION"
curl -s "$API/api/v1/bots" -H "X-API-Key: mk_..."
```

Errors use a stable envelope:

```json
{ "detail": { "error": "config_required", "message": "missing required configuration: api_key" } }
```

Common codes: `unauthorized`, `forbidden`, `not_found`, `invalid_rule`, `invalid_workflow`,
`invalid_config`, `config_required`, `token_not_verified`, `rate_limited`.

The console exposes the same surface at `/docs` (grouped operation index) and `/api/spec` (raw spec).

---

## Testing

```bash
python -m pytest -q          # 882 tests
cd webapp && npx tsc --noEmit && npm run build
```

| Suite | Covers |
| --- | --- |
| `tests/test_foundation.py` | settings, crypto, cache adapters |
| `tests/test_ui_engine.py` | premium emoji, message/keyboard/rich builders, widget adapter |
| `tests/test_moderation.py` | detectors, escalation, rule engine, executor guardrails |
| `tests/test_plugins.py` | manifests, registry, context, lifecycle manager, command registry, built-ins |
| `tests/test_services.py` | user/bot/group/moderation/plugin/dashboard services |
| `tests/test_runtime.py` | worker manager, scheduler retries, watchdog recovery |
| `tests/test_api.py` | auth, tenancy, bots, plugins, commands, dashboard, owner, templates |
| `tests/test_plugin_marketplace.py` | source URL policy, static import inspection (never executes setup.py/hooks), licence + dependency allowlists, owner review → approve → install gate |
| `tests/test_plugin_authz.py` | the three plugin authorization levels: global scope is owner-only, another tenant's installation is a 404, group scope needs the owning bot, `/plugins/effective` reports runtime state |
| `tests/test_plugin_packages.py` | package layout, saver links/captions, renamer engine, ID3 writer, post templates, heartbeat, sandbox, toggles + capabilities, command publication, durable state |
| `tests/test_plugin_routing.py` | real `Dispatcher` + real `Update`s: every package answers only its own commands |
| `tests/test_music_player.py` | `music` player: queue state machine, link+flag parsing, playlists, chart, play card, and real `Dispatcher` runs over 50+ commands and the `pb:`/`q:` buttons |
| `tests/test_encoder.py` | `encoder`: FFmpeg argv building (codec/CRF/preset/scale/burn/watermark/audio/subs), profile validation, progress parsing, ffprobe stream parsing, dispatch |
| `tests/test_filestore.py` | `filestore`: per-user + global quotas, visibility, paging, signed share links (forgery/expiry/use-cap), collections, dispatch |
| `tests/test_linkshare_leech.py` | `linkshare` gate verdicts (join/request/pending/blocked/expired/exhausted) and `leech` task engine (progress, capacity, cancel, local transfers) with real dispatch |
| `tests/telegram_harness.py` | shared recording Bot session + Update builders used by the plugin suites |
| `tests/test_saver_pipeline.py` | end-to-end `/save` run: copy, batch, dedupe, progress edits, oversized ranges |
| `tests/test_workflows.py` | **Workflows A–I** end-to-end acceptance |
| `tests/test_startup.py` | `start.py` argument contract, schema ownership, Compose contract |

### Workflows A–I

| | Workflow | Acceptance |
| --- | --- | --- |
| **A** | Bot connect & lifecycle | token verified by `getMe` (skipped in tests, never faked), start/stop/health, activity + runtime events |
| **B** | Multi-bot / clone hosting | clone is independent (deleting the source keeps the clone), plugins copied, token uniqueness enforced |
| **C** | Moderation & rules | rule validation, engine execution, history and warnings persisted |
| **D** | Plugin lifecycle | install → enable → configure → disable → uninstall; unknown config rejected; disabled registers nothing |
| **E** | Group governance | settings round-trip, reports/appeals tenant-scoped |
| **F** | Automation | workflow CRUD with versioning + validation, cron/one-off schedules, cancellation |
| **G** | Developer surface | API keys, webhooks + deliveries, preview that cannot execute input |
| **H** | Observability & owner | dashboard counts match rows, owner restrict/audit, health/readiness |
| **I** | Safety invariants | cross-tenant 404s, auth required, error envelope, webhook secret, tokens encrypted at rest |

---

## Configuration

Everything is environment driven — see `.env.example`. Key settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development` / `testing` / `production` |
| `DATABASE_URL` | SQLite in `data/` | PostgreSQL primary (`postgresql+asyncpg://…`) |
| `REDIS_URL` | empty (memory cache) | Distributed locks, rate limits, plugin state |
| `ENCRYPTION_KEY` | generated | Fernet key for bot tokens at rest |
| `BOT_TOKEN` | empty | Main bot token for `--service bot` / all-in-one |
| `OWNER_TELEGRAM_IDS` | empty | Telegram ids granted the owner role at seed time |
| `AUTH_DEV_MODE` | `false` | Development login (ignored in production) |
| `AUTO_CREATE_SCHEMA` | `true` | `false` in containers — Alembic owns the schema |
| `MAX_CLONE_WORKERS` | `25` | Bot runtimes per worker process |
| `SCHEDULER_ENABLED` / `WATCHDOG_ENABLED` | `true` | Background services in all-in-one mode |

---

## Operations

- **Health:** `GET /health` (dependencies), `GET /health/live` (liveness), `GET /ready` (readiness, 503
  when the database is down), `python start.py --service health` for container probes.
- **Migrations:** `python start.py --service migrate` (or `alembic upgrade head`). Autogenerate with
  `alembic revision --autogenerate -m "…"`.
- **Scaling:** run one `api` (or several behind a proxy), one or more `worker` processes, one
  `scheduler` and one `watchdog`. Distributed locks make duplicate schedulers safe.
- **Maintenance mode:** `POST /api/v1/owner/maintenance?enabled=true` returns `503` for every
  non-health route.

---

## What is deliberately *not* claimed

- No fabricated analytics: charts and counters render exactly what is in the database, and empty
  states say so.
- The demo seed bot has an unverified token; it cannot start, and the API reports why.
- Plugin archives from third parties are validated and sandboxed in spirit, but only built-in plugins
  are shipped here — the marketplace lists what is actually implemented (`implemented: false` is shown
  explicitly in the UI).
- Voice-chat streaming still needs `pytgcalls` plus a signalling path; without it the player says
  `simulation` while the queue and controls remain real.
- aria2 is driven over its JSON-RPC when an operator points `daemon_endpoint` at one; qBittorrent
  (torrents only) and rclone (destinations only) are refused where they cannot do the job rather
  than substituted. `yt-dlp` **is** wired (music search, page-link extraction,
  `/leech -ytdl`) and the bundled `imageio-ffmpeg` wheel gives real encodes on a host with no system
  ffmpeg. Without `ffprobe`, metadata is read from the ffmpeg banner and the Studio says so.
- MTProto (WZgram/Pyrogram) is an optional adapter, never the primary framework, and never a way to
  reach content the bot itself cannot read.

## License

Provided as-is for the MEIKO AGENT platform. Premium emoji identifiers are catalogued from
`Beasgohan-code/Videl0`; rich-message semantics reference `Richgram`, `Wzgram` and `WZML-X`.

### Builtin plugins

Every plugin is a **package**, not a single file: a typed `manifest.py`, one or more
pure logic modules, and a thin `plugin.py` with the aiogram handlers. Logic that
matters (link parsing, rename templating, ID3 writing, post validation, watchdog
state machine, sandbox limits) is importable and unit-tested without a bot, a token
or a database.

| Package | What it does |
| --- | --- |
| `file_store/` | quota-aware vault (`/save`, `/get`, `/files`, `/del`, `/storestats`) |
| `saver/` | post archiving from public/private channels: `t.me/c/…`, ranges `100-120`, lists, forum topics, caption templates, word delete/replace rules, thumbnails, dedupe, dump chat |
| `renamer/` | template engine (`{title} {season:02d} {episode:02d} {quality} …`), release-tag cleaning, previews, re-upload as document/video/audio |
| `music/` | local catalogue with fuzzy search, dependency-free ID3v2 read/write and generated cover art |
| `post_maker/` | HTML post templates with entity validation, URL button rows, scheduled queue (`+10m`) |
| `keep_alive/` | self-ping, latency stats, failure-streak alerts to the owner, recovery notice |
| `code_runner/` | sandboxed python/node/bash execution, **owner-gated** by a capability |

Launching a plugin also publishes its declared commands into the command registry, so
Telegram's menu, `/help` and the Command Studio stay in sync (`/save`, `/autorename`,
`/music`, `/schedule`, `/keepalive`, …) — and unregistering on stop leaves nothing behind.
Plugins that keep bookkeeping (saved index, post queue, heartbeat history) use
`MeikoPlugin.state`, which is cache-first and mirrored to disk, so a restart cannot
silently lose it.

Switches: `GET /plugins/toggles` + `POST /plugins/{slug}/toggle?enabled=` (the webapp
**Plugins → On / Off** tab) install-if-needed and flip state in one click. Dangerous
capabilities (`code_exec`, `remote_fetch`, `file_upload`) live behind
`/owner/capabilities` and are enforced inside the runtime, not just in the API.

