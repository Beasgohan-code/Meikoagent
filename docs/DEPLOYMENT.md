# Deploying this platform

Three shapes, in order of how much of the product you get.

| shape | what runs where | module data lives in | use it for |
| --- | --- | --- | --- |
| **Compose** (recommended) | API + bot runtime + webapp + Postgres + Redis + the watchdog service, all on one host | volume `/data/modules` | anything real: bots need a long-lived polling process |
| **Single container** | `python start.py` (bot + API), one process | volume `/data/modules` | a small deployment, a VPS, a Render/Fly service |
| **Split: API on one host, console on Vercel** | FastAPI + bots somewhere durable, Next.js serverless | Supabase Storage (`MODULE_STORE_URL`) | a hosted console with a managed database |

The third shape is the one with traps, so it gets the detail. The first two are
`docker compose up` and a volume.

---

## 1. Supabase (Postgres + object storage)

1. **Project must be active.** A paused project has no database endpoint — the
   management API answers `Database is possibly not accessible at the moment`
   and every connection fails. Resume it in the dashboard first; nothing in this
   repository will resume it for you.
2. **Connection string.** Use the *pooled* connection for the app and the direct
   one for migrations:

   ```
   # app (pgbouncer, transaction mode) — note the port
   DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   # migrations (direct)
   MIGRATION_URL=postgresql+asyncpg://postgres.<ref>:<password>@db.<ref>.supabase.co:5432/postgres
   ```

   pgbouncer in transaction mode cannot run multi-statement DDL reliably, so run
   `python -m alembic upgrade head` against the direct URL, then start the app on
   the pooled one.
3. **Schema.** `AUTO_CREATE_SCHEMA=0` in a hosted deployment: Alembic owns the
   schema. On first boot run the migration; afterwards `alembic check` should say
   *No new upgrade operations detected*.
4. **Storage bucket** for module documents: create a **private** bucket
   (`meiko-modules`) and give the server key access. Then set

   ```
   MODULE_STORE_URL=supabase://<ref>/meiko-modules
   MODULE_STORE_TOKEN=<service-role key>     # server-to-server only, never NEXT_PUBLIC_*
   ```

   Without this, a serverless host writes to `/tmp`: settings look saved and are
   gone after the next cold start. With it, `ModuleStore` keeps queues, links,
   shops and job history in the bucket.

## 2. The console on Vercel

The browser only ever talks to its own origin; `/api/backend/*` is a Next route
handler that proxies to the API. So:

| setting | value |
| --- | --- |
| `MEIKO_API_URL` | public URL of the FastAPI service (no trailing slash) |
| `NEXT_PUBLIC_MEIKO_WS_URL` | *optional* — only when the API is separately reachable for WebSocket upgrades |

Set `MEIKO_API_URL` **without** `NEXT_PUBLIC_`: it is read server-side in the
route handler, so the browser never learns the API address. WebSocket upgrades
cannot pass through a Next route handler, which is why the music player falls
back to Server-Sent Events (`/api/v1/music/stream`) unless
`NEXT_PUBLIC_MEIKO_WS_URL` points straight at the API.

Vercel cannot run the bots: polling and long-lived worker runtimes need a
process, not a function. Run `start.py --service worker` (or the combined
`start.py`) on a durable host and leave Vercel to the console.

## 3. Environment variables that matter

| variable | why it exists |
| --- | --- |
| `SECRET_KEY` | session signing. **Required in production** (32+ chars): the API refuses to boot without it, because a generated key logs everyone out on restart and is not shared between workers |
| `ENCRYPTION_KEY` | Fernet key sealing bot tokens and signing link claim tokens; if it changes, stored tokens cannot be decrypted and outstanding claim links stop verifying. When unset, a generated key is read from `DATA_DIR/.encryption_key` — the app never falls back to a public constant |
| `DATABASE_URL` | `postgresql+asyncpg://…` in production, `sqlite+aiosqlite://…` locally |
| `REDIS_URL` | distributed locks, job deduplication, worker registry and cache. **Required for any multi-process deployment** — in production (or with `REQUIRE_REDIS=1`) the API refuses to boot without it rather than silently running on per-process memory |
| `REQUIRE_REDIS` | forces the strict behaviour above for a non-production deployment that still runs more than one process |
| `COOKIE_SECURE` | marks the session cookie `Secure` when TLS terminates in front of a non-production deployment (production implies it) |
| `CSRF_ENFORCE` | default on: unsafe methods are refused when the browser reports an untrusted `Origin`/`Referer` |
| `AUTO_CREATE_SCHEMA` | `0` when Alembic owns the schema |
| `MODULE_STORE_URL` / `MODULE_STORE_TOKEN` | module documents in object storage |
| `MEIKO_API_URL` | console → API proxy target (server-side only) |
| `AUTH_DEV_MODE` | local `/auth/dev` login; the API refuses it in production regardless |
| `MEIKO_FFMPEG_BIN` / `MEIKO_FFPROBE_BIN` | paths to media binaries when they are not on `PATH` |

## 4. Order of operations

```bash
# 1. schema (direct connection, not the pooler)
python -m alembic upgrade head

# 2. API + bots (durable host)
python start.py --service api      # and --service worker, or the combined default
# 2b. the watchdog MUST be its own process: a crashed process cannot restart itself
python start.py --service watchdog

# 3. console (Vercel or `cd webapp && npm run build && npm start`)
#    with MEIKO_API_URL pointing at step 2
```

Verify with `GET /health` (unauthenticated), then a session:

```bash
curl -s -X POST "$API/api/v1/auth/dev" -H 'content-type: application/json' \
  -d '{"telegram_id":1,"username":"smoke"}'
```

### 5.1 Web dependencies

Vercel gates deployment on dependency advisories: the build can succeed and the
deployment still be rejected ("Vulnerable version of Next.js detected"), which is
how this repository found out that its `next` pin was stale. Two rules follow:

* `next` is pinned to an exact, patched version in `webapp/package.json`; bump it
  deliberately and run `npx next build` in the same change.
* `postcss` is overridden to a patched version at every depth, because Next.js
  bundles its own copy that the top-level version does not cover.

`tests/test_docs_contract.py::TestWebDependenciesStayPatched` enforces both from
the lockfile, so a stale pin fails the suite instead of only failing a deploy.

## 5. Schema safety at startup

`create_all()` creates missing *tables* and never alters existing ones, so a
deployment that upgrades against an old database would otherwise boot "healthy" and
then return 500 on every query that touches a new column. Startup now verifies the
schema against the models:

| situation | behaviour |
| --- | --- |
| schema matches | normal boot |
| a table is missing, `AUTO_CREATE_SCHEMA=1` | the table is created |
| a column is missing, `AUTO_CREATE_SCHEMA=0` (containers) | **boot fails** with `SchemaMismatch` naming the columns; run `--service migrate` first |
| a column is missing, local SQLite with `AUTO_CREATE_SCHEMA=1` | the disposable local schema is recreated and the data loss is logged |

That is why the documented order is *migrate, then start*: `python start.py
--service migrate` (or `alembic upgrade head`) before the API, and
`alembic check` should report no pending operations.

## 6. Data retention

Every log table grows with usage, and nothing used to delete from them:
`purge_activity()` was documented as "used by the scheduler/watchdog" and had no
caller at all. On a small managed database that ends with a full volume, which fails
*writes* — the bot keeps polling and silently stops recording.

The watchdog now prunes on a timer (`RETENTION_ENABLED`, default on) with separate
windows per table, deleting in batches of 5 000 rows per tick so a first run against
a large table does not hold one long transaction:

| table | default window | setting |
| --- | --- | --- |
| `activity_events` | 90 days | `RETENTION_ACTIVITY_DAYS` |
| `command_executions` | 30 days | `RETENTION_COMMAND_LOG_DAYS` |
| `runtime_events` | 30 days | `RETENTION_RUNTIME_EVENT_DAYS` |
| `plugin_logs` | 30 days | `RETENTION_PLUGIN_LOG_DAYS` |

`RETENTION_INTERVAL_SECONDS` (default 6h) controls how often it runs. Audit logs and
moderation records are deliberately **not** pruned: they are the record of who did
what, and losing them to save disk would be the wrong trade.

## 7. What this deployment shape still cannot do

* **No WebSocket through Next.** Use SSE, or expose the API directly.
* **No bot runtime on Vercel.** Functions do not poll Telegram for a token.
* **No watchdog in the main process.** Recovery runs as `start.py --service watchdog`
  (its own container in Compose); folding it into the API would mean the process
  that crashed is the one expected to notice.
* **No in-memory Redis substitution in production.** Rate limits, the one-poller
  bot claim and the worker registry all assume shared state.
* **No media on a read-only filesystem.** `imageio-ffmpeg`'s binary needs to be
  executable; a container that mounts `/tmp` read-only needs `MEIKO_FFMPEG_BIN`
  pointed at a real install.
* **No long transfers in a function.** Leech/mirror jobs run in the worker, not
  in a request; a serverless console only watches them.
