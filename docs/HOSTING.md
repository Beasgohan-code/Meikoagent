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
