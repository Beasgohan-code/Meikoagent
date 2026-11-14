# Hosting this project

This page exists because it is easy to lose hosting accounts to this kind of
software, and the reason is almost never the code quality. It is what the service
*does* once it is running, and what a reviewer sees when they look at the repository.

## Read this before you deploy to a free tier

**A free tier is not built for a Telegram bot that is always on.** Free web-service
plans (Render, Railway, Fly, Heroku-style dynos) sleep an instance after a period
with no inbound HTTP traffic, and they generally count *service-initiated* traffic
against a threshold. A Telegram bot connects **out** to Telegram by long polling
and receives almost no inbound HTTP, so it is the exact shape those policies target.

**Do not "fix" that.** Keeping an instance awake with a self-ping, a cron hit every
five minutes, or the `keep_alive` plugin's timer is circumventing an idle policy.
Render's Acceptable Use Policy prohibits using the service to "bypass, circumvent,
or defeat any access or usage restrictions", and enforcement there is account-wide
and can be immediate, without a per-app warning. Three accounts suspended in a row is
what that looks like in practice: each new account is linked to the same person and
the same behaviour.

`keep_alive` still ships, because heartbeat/latency reporting is genuinely useful on
a host you control, but its self-ping timer is **off by default** and it is documented
as reporting, not as an idle defeat.

## What a reviewer looks for

| Signal | In this repository |
| --- | --- |
| "Save restricted content" in the name or README | The word *restricted* appears only in the source-attribution table (`docs/MODULES.md` §3.6), describing the upstream it was compared against. The plugin is called **saver** and its own text says *public and private channels*. |
| Session-string login (reaches chats the bot is not in) | Refused on purpose and tested — `docs/MODULES.md` §6. This is the single most important line to be able to point at. |
| Bulk media download / mirroring of third-party sites | `leech` exists as a typed task engine with adapters; `yt-dlp` is used by `music` for audio playback. If you enable them, you are responsible for what they fetch and for the rights to it. |
| Account farms, spam, mass messaging | Not implemented. There is no broadcast-to-unknown-chats feature; `automation`/`integrations` are owner-scoped webhooks. |

If a reviewer has already decided, the thing that helps is a short reply that points
at `docs/MODULES.md` §6 and says what the service is *for* (running your own bots in
your own chats), not an argument. Do not create another account to work around a
suspension — that is what turns one suspension into a pattern.

## Why "just copy Videl1's keep-alive" is not the answer

`Beasgohan-code/Videl1` bundles two things that look like one:

| Videl1 | What it does | Verdict |
| --- | --- | --- |
| `VidelBot/keep_alive.py` | serves `/` and `/health` on `$PORT` | **Good, and now copied in spirit**: the API already serves `/health`, `/health/live`, `/ready`; it now also adopts `$PORT` and reports per-bot liveness |
| the same file's self-ping | `GET {KEEP_ALIVE_URL}/ping` every `KEEP_ALIVE_INTERVAL` | **Not copied.** Its own docstring says *"so the platform never idles the process"*, and `render.yaml` says the free plan *"never sleeps"* because the server *"pings itself"*. That is the behaviour Render's AUP calls circumventing a usage restriction — the reason the accounts went. |
| `render.yaml` | `plan: free`, `healthCheckPath: /health` | partially copied: the health check idea yes, the free plan no (`plan: starter`) |

So the "keep-alive" that is actually broken on a free tier cannot be fixed by a
better timer. What *can* be fixed is that nothing noticed the bot was dead. That is
now real:

* **`BotApplication.liveness()`** = not stopping **and** the polling task is alive
  **and** the loop ran within `3 × telegram_polling_timeout + 60s` **and** the
  runtime is not marked failed. `healthy` alone is not accepted: it is set once at
  start while aiogram keeps retrying internally, which is how a bot looks fine and
  answers nothing for hours.
* **`GET /health`** now includes a `bots` block (`workers`, `alive`, `stale`,
  `errors`, `serving`).
* **`GET /health/bots`** returns `503` when any running bot is not being serviced —
  point an uptime monitor or a platform health check at it, and the restart is
  automated instead of invisible.
* **`$PORT` is adopted** (Render/Railway/Heroku/Koyeb assign one). Before this the
  API always listened on 8000, so a platform probing the assigned port saw an
  unhealthy service and cycled it — another way a "running" bot is really down.
* **`telegram_polling_timeout`** is now explicit, so the staleness window scales with
  how long a long-poll is expected to block.

## Connecting a managed Postgres (Supabase, Neon, Render)

A managed database is the right place for this app's state — but three details make
a copied connection string fail, and all three are now handled explicitly:

1. **The scheme.** Supabase and Render hand out `postgresql://…`. SQLAlchemy reads
   that as the *synchronous* psycopg driver, which is not installed → the service dies
   at boot with `ModuleNotFoundError: No module named 'psycopg'`. The URL is normalised
   to `postgresql+asyncpg://` automatically, so paste what the dashboard gives you.
2. **TLS.** `?sslmode=require` is libpq syntax; the asyncpg dialect forwards unknown
   query parameters straight into `asyncpg.connect()` and it rejects them. The
   parameter is moved into a real connect argument (`ssl=True`), so the URL still says
   `sslmode=require` and the connection actually uses TLS. A **remote** Postgres
   without TLS refuses to boot in production instead of failing mid-query.
3. **Pooler vs migrations.** Supabase's pooler (`…pooler.supabase.com:6543`,
   transaction mode) cannot run multi-statement DDL. Set `MIGRATION_URL` to the direct
   connection (`db.<ref>.supabase.co:5432`) and run migrations first:

```bash
# Supabase dashboard → Project Settings → Database → Connection string
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
MIGRATION_URL=postgresql://postgres.<ref>:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require

python start.py --service migrate     # uses MIGRATION_URL
python start.py --service api         # uses DATABASE_URL
```

`python start.py --service migrate` also seeds the plugin catalogue, so a fresh
database needs no manual step. Redis is still required for more than one process;
Supabase does not provide it — use Upstash or the Compose service.

## A deployment shape that is not a policy problem

Every long-running process here is a normal worker, not a web service:

```bash
python start.py --service api        # HTTP control plane + console
python start.py --service worker     # bot workers (long polling)
python start.py --service scheduler
python start.py --service watchdog
```

* Run the API on the host's web-service product; the console (`webapp`, Next.js) is
  also a web service and is what Vercel deploys.
* Run workers on any host that sells an always-on process: a $5 VPS with Docker
  Compose, Fly machines, Railway with no sleep, or your own box. `docker compose up`
  in this repository is a complete, tested stack.
* Set `SECRET_KEY` (32+ chars), `ENCRYPTION_KEY`, `DATABASE_URL` (Postgres) and
  `REDIS_URL`. Startup now refuses to run in production without the ones it cannot
  work without — see `docs/DEPLOYMENT.md` §5.
* Telegram itself is the thing that must reach you, so nothing needs to be public
  except the API/console. Do not open the bot worker to the internet.

## If you only want the console online

The Next.js console can be hosted on the free tier indefinitely, because it is a web
service that receives real traffic. It needs `MEIKO_API_URL` pointing at wherever the
API runs. Without a running API behind it every page shows its empty state — which is
honest, but it is not a working bot platform.
