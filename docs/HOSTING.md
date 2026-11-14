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
3. **Pooler vs migrations.** Port **5432** on the pooler host is *session* mode and
   does everything; port **6543** is *transaction* mode, which cannot run
   multi-statement DDL and moves prepared statements between backends. Use 5432, and
   from a phone use the pooler host at all: `db.<ref>.supabase.co` is IPv6-only on the
   free tier and simply times out over IPv4.

```bash
# Supabase dashboard → Connect → Connection method: Session pooler
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require

python start.py --service migrate     # 5432 handles DDL, so no MIGRATION_URL needed
python start.py --service api         # same URL
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

## Free options, honestly ranked

The question is always "can I run this 24/7 for free". The honest answer for a
long-polling Telegram bot is **your own device**, not a cloud free tier — and that is
not a downgrade: a worker that runs where you control the network is exactly the shape
that works.

| Where | Free 24/7? | What actually happens | Use for |
| --- | --- | --- | --- |
| **Termux on Android** | **yes** | real Linux, no sleep, no egress limits. Android may kill the session when it reclaims memory; `termux-wake-lock` plus the Termux:Boot recipe here brings it back | the bot workers, scheduler, SQLite database — the best free option |
| **Old laptop / Raspberry Pi** | **yes** | `docker compose up -d` and forget it; systemd restarts it | the whole stack including Postgres and Redis |
| Render free / Railway free / Fly free | no | sleeps on idle; keeping it awake is what suspended the accounts (see above) | the *console* only, if you want a URL |
| Oracle Cloud "Always Free" VM | usually | an actual always-on VM at no cost, but capacity is often unavailable in popular regions and accounts get re-reviewed for "idle" resources | the whole stack, if you can get one |
| Koyeb / Zeabur / Northflank free | flaky | free web instances still sleep or are time-limited; a bot that sleeps is a bot that is down | experiments |
| GitHub Actions cron | no | jobs are capped (minutes per month) and are killed after 6h; a bot cannot live there | nothing here |
| Vercel free | console only | a Next.js console stays up; it cannot run Python or a poller | the console in front of an API you host elsewhere |

### Termux (the one to use if "free" is a hard requirement)

`scripts/termux_setup.sh` installs the toolchain the wheels need (clang, rust,
openssl, sqlite), builds the dependencies, creates a data directory outside the app
sandbox, starts Redis and writes a `~600` env file with generated keys. Then:

```bash
termux-wake-lock                 # stop Android from freezing the process
bash scripts/termux_boot.sh      # migrate + start, idempotent, logs to ~/meiko.log
```

Put `scripts/termux_boot.sh` in `~/.termux/boot/` (Termux:Boot app) and the bot comes
back after a reboot on its own. Two honest limits: the phone is not a server, so
expect restarts after Android updates; and do **not** expose it — `AUTH_DEV_MODE=true`
means the console trusts anyone who can reach it, so keep it on `127.0.0.1` and reach
it over Tailscale or an SSH tunnel if you need it from another device.

The web console on a phone needs Node and a production build; it is easier to keep the
API on the phone and open `/docs`, or host the console on Vercel pointing
`MEIKO_API_URL` at a tunnel to the phone.

## If you only want the console online

The Next.js console can be hosted on the free tier indefinitely, because it is a web
service that receives real traffic. It needs `MEIKO_API_URL` pointing at wherever the
API runs. Without a running API behind it every page shows its empty state — which is
honest, but it is not a working bot platform.
