# Meiko media modules — architecture and integration record

Six substantial modules now live inside Meikoagent (not beside it): **music**,
**saver**, **encoder**, **filestore**, **linkshare** and **leech**, on top of one
shared infrastructure package (`meiko/modules/`), one aiogram core, one database,
one plugin manager, one worker manager, one permission system and one webapp
design system.

This document is the integration record the reference-repository brief asked for:
what each upstream project actually contained, what was reused, what was
re-implemented, and what is deliberately absent.

---

## 1. Shared infrastructure (`meiko/modules/`)

Building six copies of "queue + progress + storage + events" would have been the
expensive mistake. One package owns it:

| file | responsibility | why shared |
| --- | --- | --- |
| `store.py` | atomic JSON per `(module, scope)`, corrupt-file quarantine, scope index | the API process has no plugin context; both sides need the same documents |
| `events.py` | topic/scope fan-out, replay ring buffer, slow-client dropping | the webapp is fed from here (WebSocket *and* SSE) |
| `jobs.py` | priority queue, per-owner + global concurrency, retries, cancel, restart recovery | one queue semantics instead of six |
| `ledger.py` | durable job history + per-owner/module storage quotas | dashboards read history that survives a restart |
| `progress.py` | throttled Telegram progress rendering, premium-emoji bar, ETA only when reliable | Telegram's edit limits are a hard constraint |
| `media.py` | MIME/extension policy, temp workspaces, hashing, sweeps | one audit point for "what files may we accept" |
| `leech.py` | typed `Downloader`/`Uploader` interfaces, HTTP/local/Telegram/cloud adapters | the transfer seam the spec asks for |
| `filestore.py` | multi-tenant shops, ownership checks, signed deep links | tenant isolation in one place |
| `linkshare.py` | link lifecycle, batches, revocation, *honest* analytics | statistics must match what the backend can observe |
| `music.py` | mirrored room sessions, presence, playlists, listening stats, transport | the bot and the dashboard read/write one truth |
| `links.py` | HMAC-signed tokens (no JWT, no `alg: none`) | shared by FileStore links and Linkshare |
| `mtproto.py` | optional WZgram/Pyrogram adapter boundary | see §8 |
| `telemetry.py` | "what this build can do", rendered verbatim by the UI | no guessing about missing capabilities |

Two rules encoded in the code:

1. **Nothing pretends.** A stopped bot answers `applied: false` with a reason; an
   unconfigured adapter reports `available() == False`; ffmpeg absence yields a
   plan, not a fake encode; linkshare never claims "views".
2. **A disabled module is inert.** No router is registered, no background task
   starts, and the dashboards refuse to render controls for it
   (`ModuleShell` + `GET /api/v1/plugins/effective`).

---

## 2. Command surfaces

| module | commands |
| --- | --- |
| music | 53: `/play /vplay /stream /fplay /pause /resume /skip /prev /seek /fw /bw /end /stop /loop /loopq /autoplay /queue /q /clear /remove /move /shuffle /pick /playlist /savequeue /loadqueue /delqueue /chart /playing /np /now /next /nowplaying /style /mode /mode3d /speed /volume /join /leave /room /vc /auth` + catalogue toolkit |
| saver | `/save /batch /saved /settings /panel /setcaption /resetcaption /delword /replword /setthumb /delthumb /setdump /sendmode` … |
| encoder | `/encode /fastencode /encodehelp /encpreset /enccrf /enccodec /encaudio /encsubs /encresolution /encwatermark /encthumb /encinfo /enccancel /encstats` |
| filestore | `/start /genlink /flink /my_bots /batch /settings /users /fcancel` … (14) |
| linkshare | `/genlink /batch /settings /approve /route /start` (12) |
| leech | `/leech /mirror /tasklist /taskstatus /cancel /leechstats /taskbackend` |

Collisions and renames are listed in §9 — the brief requires telling the user
about every one.

---

## 3. Upstream audit: what was inspected

All seven repositories were cloned and read at source level (`/tmp/refs`), not
just their READMEs.

### 3.1 `abe_/playeon` + `Beasgohan-code/Videl0` → **music**

*Inspected*: `playeon`'s `player/`, `queue.py`, card rendering, callback protocol,
premium-emoji ids; `Videl0`'s plugin layout, `-f/-sd/-hd/-fhd/-2k/-4k` flags,
`site` argument handling, playlist/chart.

*Reused (re-implemented on aiogram)*: playback state machine (`Track`,
`QueueState`, `PlayerQueue`), quality flags, `pb:`/`q:` callback protocol, card
layout, styles (`nightcore`, `vaporwave`, `8b`, `karaoke`…), seek clamping,
loop/loop-queue/autoplay interaction, playlists, chart.

*Not copied*: Playeon's licence restricts redistribution/rebranding/commercial
use, so **no code was copied** — only architecture and interaction patterns were
studied. Premium emoji ids are document/asset identifiers, replicated with
unicode fallbacks in `meiko/ui/emoji.py`.

*Added beyond upstream*: durable queues (a restart keeps the queue), webapp
transport (`apply_transport`) driving the same queue, room mirroring for the
dashboard, listening statistics that only count actual track changes.

### 3.2 `abhinai2244/ENCODING-BOT` → **encoder**

*Inspected*: `VideoEncoder/utils/encoding.py` (codec `libx265|libx264`, `-crf`
default 26, `-preset` uf…slow, `-tune animation|film`, CABAC, refs,
`-pix_fmt yuv420p|yuv420p10le`, `-r` list, `-aspect`, metadata watermark,
subtitles `copy|burn|none`, `mov_text` for MP4 / `copy` for MKV / none for AVI,
`-map 0:v? -map_chapters 0 -map_metadata 0`), `audio_selector.py` (multi-audio
`-i` picker, `audiosel` callback, 180 s timeout), the 40-odd commands.

*Reused*: the whole encode plan — `plans.py` builds the same argv
(`ffmpeg -hide_banner -y -i in.mkv -vf scale=…,drawtext=…,subtitles=… -c:v libx265
-crf 24 -preset slow -tune film -pix_fmt yuv420p -map 0:v:0 -c:a aac -b:a 128k
-map 0:a:0? -movflags +faststart -progress pipe:1 -nostats out.mp4`), track
selection, subtitle modes, watermark, thumbnail, `ffprobe` info.

*Added beyond upstream*: output validation (a reported 100% is never trusted —
the output is probed before delivery), a durable job ledger the Encoding Studio
reads, dashboard cancellation of the real ffmpeg process, workspace TTL cleanup,
argv-only execution (no shell), settings validated server-side.

*Not copied*: the mongo/pyrogram session layer and the "userbot" upload path.

### 3.3 `abhinai2244/Multi-FileStoreBot` → **filestore**

*Inspected*: `main_bot/plugins/{bans,bot_settings,broadcast,create_bot,my_bots,start}`,
`utils/{caption_logic,helpers,security,shortener}`, the Cloudflare worker backend,
commands (`ban batch broadcast check custom_batch flink genlink ping start sysstats
unban users`), and the child-bot token flow.

*Reused*: the tenancy model (per-bot files, users, admins, settings, logs),
custom captions and thumbnails, batch + custom-batch link generation, force-sub,
admin management, bans, broadcast, storage analytics, export.

*Re-implemented rather than ported*: the child-bot runner. Meikoagent already is
a multi-bot platform with a central worker manager, so a "FileStore shop" is a
tenant inside the existing manager — not a second bot runner. That is the brief's
"central bot manager, not a competing worker manager" requirement.

*Security*: bot tokens are never returned by any route; they live in
`BotAccount.token_encrypted` (Fernet) and are masked in the shop record.

### 3.4 `abhinai2244/LINK-SHAREBOT` → **linkshare**

*Inspected*: `plugins/{approve,route,settings,start}`, `route.py` (an aiohttp
health endpoint, not a router), commands (`batch broadcast genlink settings
start`).

*Reused*: protected links with an access gate, join requests and admin approval,
claim caps, expiry, per-user verify tokens, batch generation.

*Deliberately different*: the analytics only report claims and refusals that the
bot actually observed — Telegram's Bot API does not report whether an external
link was opened, and the UI says so in the panel itself.

### 3.5 `abhinai2244/CBML` → **leech**

*Inspected*: `bot/helper/telegram_helper/bot_commands.py` (the full alias map:
`mirror/m`, `qbmirror/qm`, `jdmirror/jm`, `ytdl/y`, `socialdl/sdl`, `sdt`,
`uphoster/up`, `nzbmirror/nm`, `leech/l/lh`, `qbleech/ql`, `jdleech/jl`,
`ytdlleech/yl`, `nzbleech/nl`, `seedrlink/slink`, `clone/cl`, `count`, `del`,
`list`, `search`, `users`, `cancel/c`, `cancelall/call`, `forcestart/fs`,
`status/s`, `stream/sl`, `ping`, `restart/r`, `broadcast/bc`, `stats/st`,
`help/h`, `log`, `shell`, `aexec`, `exec`, `clearlocals`, `rss`, `addimage/ai`,
`images/img`, `authorize/a`, `unauthorize/ua`, `addsudo/as`, `rmsudo/rs`,
`blacklist/bl`, `rmblacklist/rbl`, `bsetting/bs`, `usetting/us`, `addbot/ab`,
`select/sel`, `category/ctsel`, `gdclean/gdc`, `plugins`, `memory/mem`,
`requestff/reqff`, `taskm/tm`, `taskuser/tu`, the `CMD_SUFFIX` mechanism and the
plugin-command merge with collision renaming), `downloaders/`, `uploaders/`,
`modules/`, the mongo layer, the `pyarmor`-obfuscated segments, the rclone/aria2/
qBittorrent/SABnzbd plant.

*Reused*: task engine semantics (flags, progress/ETA/speed, per-user + global
concurrency, cancel, stats), the leech/mirror split, destination concepts.

*Not copied*: the daemon plant (aria2/qBittorrent/SABnzbd/rclone), the mongo
layer, the pyrogram session, `shell`/`aexec`/`exec` (an RCE surface), the
obfuscated modules (unreadable, therefore unreviewable). Instead
`meiko/modules/leech.py` defines the typed seam the brief asks for
(`Downloader.validate/download(source, destination, progress)` and
`Uploader.validate/upload(file_path, destination, progress)`) with concrete
HTTP, local, Telegram and cloud implementations; cloud reports unavailable until
rclone and a remote exist.

### 3.6 `cantarella/save-restricted-content-bot` → **saver**

*Inspected*: the full command list (`add_premium, add_unsubscribe, ban,
broadcast, cancel, cancellogin, cmd, commands, dblink, del_caption, del_thumb,
del_unsubscribe, delete_thumb, help, login, logout, myplan, plan, premium,
rem_del_word, rem_repl_word, remove_premium, see_caption, see_thumb, set_caption,
set_del_word, set_dump, set_thumb, setchat, settings, start, thumb_mode, unban,
users, view_thumb`) and its session-string login flow.

*Reused*: caption templating, delete/replace word rules, thumbnails, dump chat,
send modes, dedup index, batch link parsing (ranges, lists, open ranges, private
`t.me/c/` links, forum topics), settings panel, stats.

*Refused on purpose*: `login`/`logout`/`cancellogin` — accepting a user session
string to reach chats the bot is not a member of is exactly the bypass the brief
forbids. `/auth` explains where a session belongs (owner config, never chat) and
`/save` keeps using the platform's ordinary copy path. `premium`/`plan`/
`myplan` billing was not ported: it is a subscription business, not a media
feature, and Meiko has no payment processor.

### 3.7 `rjriajul/wzgram` → **MTProto adapter**

*Inspected*: `pyrogram/` (the fork keeps upstream's tree) plus a parallel
`wzgram/` package, `compiler/`, `examples/`, `tests/`, `COPYING` +
`COPYING.lesser` (LGPL-3.0) and `NOTICE`; `requires-python >= 3.10`.

*Decision*: WZgram is a Pyrogram fork, i.e. an MTProto *user* client. Using it as
the primary framework would mean a second client consuming the same update
stream (forbidden by the brief) and would put user-session credentials in the hot
path of every command. It is therefore an **optional dependency** behind
`meiko/modules/mtproto.py`, used only where the Bot API genuinely cannot work
(voice-chat streaming, large uploads). The adapter:
never receives a bot token, never consumes command updates, checks membership
*through the session* (so it cannot "read" what the account is not a member of),
and reports unavailability with an actionable reason. Nothing is vendored; LGPL
notices stay with the installed package.

---

## 4. Dependency and framework conflicts resolved

| conflict | resolution |
| --- | --- |
| pyrogram/WZgram vs aiogram update streams | aiogram owns updates; MTProto is a library-only adapter |
| mongo (CBML, FileStoreBot) vs Meiko's SQLAlchemy | module state goes through `ModuleStore` (JSON, atomic) / the existing tables; no second database |
| redis/aria2/qBittorrent daemons | optional; adapters probe and report `available()` |
| `yt_dlp` (Playeon, CBML) | not installed here; `sources.py` resolves direct links and the local catalogue, and an owner-configured `resolve_endpoint` when `http.outbound` is granted |
| duplicate command names across modules | see §9 |
| six copies of queue/progress/storage | `meiko/modules/` |

---

### 4.1 Media toolchain (optional, resolved at runtime)

| requirement | why it is optional | what happens without it |
| --- | --- | --- |
| `ffmpeg` (system or `imageio-ffmpeg` wheel) | encoding is the only feature that truly needs it | `/encode` prints the exact command and refuses to start; telemetry says `ffmpeg: false` |
| `ffprobe` | ships with system ffmpeg, **absent** from the wheel | metadata falls back to the banner; the Studio shows a "metadata from banner" note instead of a red error |
| `yt-dlp` | unpinned extractors break; operators may prefer their own relay | search falls back to catalogue → relay; `/leech -ytdl` fails with the install hint |
| `mutagen` | pure-Python, only for non-MP3 containers | `/tag` on an M4A says which package to install; MP3 keeps the in-tree writer |

The `imageio-ffmpeg` wheel is deliberately in `requirements.txt`: it makes
"clone and run" produce real encodes on a machine with no system packages, which
is the difference between a CI test and a toy.

### 4.2 URL policy (`meiko/modules/urls.py`)

Six modules accept a link from a stranger. The upstream bots each answered
"is this URL safe?" differently, and three answers were wrong. One policy now:

| rule | why | example refused |
| --- | --- | --- |
| `http`/`https` only | every consumer pipes the URL into a downloader or an outbound client | `file:///etc/passwd` |
| no userinfo | `https://user:pass@host` leaks secrets into logs and chat | `https://a:b@example.com/` |
| no loopback / private / link-local / reserved literal | SSRF: a bot that fetches `127.0.0.1` is a confused deputy | `http://169.254.169.254/latest/meta-data/` |
| no `.local`/`.internal`/`.lan`/`.home.arpa` hosts | orchestration and printer names are not the public internet | `https://db.internal/` |
| `t.me` only where the bot resolves it | the player hands Telegram posts to the saver instead of fetching them | `https://t.me/c/1/2` in `/leech` |
| no control characters, ≤ 2048 chars | header injection and log spam | `https://example.com/\r\nHost: x` |

Enforced where the bytes move, not in a helper nobody calls: linkshare creation
(so a stored link cannot become a later SSRF), leech's HTTP downloader *before*
it connects (non-retryable), the music resolver, and the API batch endpoint
(which refuses the whole batch rather than leaving half of it behind).

Error strings **redact** the URL (userinfo and query removed) because they are
sent to chat and written to logs. `GET /api/v1/linkshare/validate?url=…` exposes
the same verdict, and the Create-link form uses it while the user types.

Deliberately **not** done: DNS resolution at validation time. A public hostname
that resolves to a private address is a network-level problem (egress policy,
DNS pinning), not something a pure validator can decide without making every
call slow and flaky. That limit is stated here rather than papered over.

## 5. Licensing

| upstream | licence | what Meiko may do |
| --- | --- | --- |
| Playeon | restrictive (no redistribution/rebrand/commercial) | architecture study only; **no code copied** |
| Videl0 | repository licence as published | patterns re-implemented on aiogram |
| ENCODING-BOT | as published | plan/argv re-implemented, attribution in `docs/FEATURE_SUGGESTIONS.md` |
| Multi-FileStoreBot | as published | tenancy model re-implemented |
| LINK-SHAREBOT | as published | link gate re-implemented |
| CBML | as published | task semantics re-implemented; obfuscated parts unreadable → not reused |
| save-restricted-content-bot | as published | parser/caption rules re-implemented; session login refused |
| wzgram | LGPL-3.0 | used as an optional dependency, never vendored |

No upstream source file is copied into this repository. Where an idea is
directly traceable (encode argv, flag names, card layout) the attribution is in
the module docstring and in `docs/FEATURE_SUGGESTIONS.md`.

---

## 6. Security posture

* **No bypass, anywhere.** The saver validates authorization before every
  operation; `login`-style session features were refused; the MTProto adapter
  cannot read a chat its account is not a member of.
* **Secrets.** Bot tokens are Fernet-encrypted, fingerprinted and never returned
  by an API; shorteners/API keys are stored masked; FileStore exports omit
  credentials; logs redact tokens.
* **Input.** ffmpeg runs as an argv list, never through a shell; paths are
  resolved inside a per-job workspace; uploads are checked for extension, MIME
  and size before bytes move.
* **Tenancy.** Every store scope is derived from a bot id the caller owns; a bot
  that is not yours is a 404, not a 403 that leaks its existence.
* **Rate limits.** Progress edits are throttled; the event hub drops slow
  WebSocket clients rather than blocking a bot.

---

## 7. API surface (new)

```
GET/POST  /api/v1/music/{sessions,room,transport,playlists,analytics}
WS/SSE    /api/v1/music/live  ·  /api/v1/music/stream
GET/PATCH /api/v1/encoder/{jobs,presets,profile}
POST      /api/v1/encoder/jobs/{id}/cancel
GET/POST  /api/v1/saver/{overview,settings,history,retention,export,import}
GET/POST/PATCH /api/v1/filestore/{bots,shops,{slug}/files,{slug}/links,{slug}/settings,{slug}/users,{slug}/admins}
GET/POST  /api/v1/linkshare/{links,batch,settings,analytics}
GET/POST  /api/v1/leech/{jobs,sources,validate,jobs/{id}/retry}
GET       /api/v1/platform/{modules,storage,jobs}
GET       /api/v1/plugins/effective
```

---

## 8. Webapp sections

MUSIC (Player, Lounge, Queue, Playlists, Rooms, Analytics) · SAVER (Backup Jobs,
Media Library, Import/Export, Settings, History) · ENCODER (Studio, Presets,
Active Jobs, History, Settings) · FILESTORE (My Bots, File Manager, Link
Generator, Storage, Bot Settings) · LINKSHARE (Create Link, Batch Sharing, Link
Manager, Analytics) · LEECH (Transfer Dashboard, Downloads, Uploads, Queue,
Destinations, History) · PLATFORM (Shared Workers, Storage Usage, Module
Management, Resource Monitoring, System Logs).

Every page reads a real endpoint. `ModuleShell` refuses to render module controls
when the module is disabled for the selected bot, and shows whether the bot is
running, because "enabled but stopped" and "running" are different states.

Real-time sync uses a direct WebSocket where the deployment allows an upgrade and
falls back to Server-Sent Events (`/music/stream`) behind a proxy that does not.
Both are fed by the same hub, so the payloads are identical.

---

## 9. Command collisions and renames

The brief requires the same command names as upstream and an explicit note for
every change.

| command | modules | resolution |
| --- | --- | --- |
| `/start` | saver, filestore, linkshare | each plugin answers only in its own scope; the platform's dispatcher picks the enabled plugin for that chat. Not renamed: it is Telegram's entry point. |
| `/settings` | saver, linkshare, encoder (`/enc…`), filestore (panel) | scoped per plugin; filestore's panel is `/settings` inside its own shop context |
| `/batch` | saver (link ranges), filestore (batch links), linkshare (batch share) | scoped: filestore uses `/batch` for its own files, linkshare for arbitrary URLs, saver for post ranges |
| `/genlink` | filestore, linkshare | filestore issues a deep link for a stored file; linkshare issues a protected URL |
| `/cancel` | leech (task), encoder uses `/enccancel` | encoder was renamed **`/enccancel`** to avoid swallowing the leech job cancellation |
| CBML's `/mirror`, `/leech` | leech | kept verbatim |
| CBML's `shell`, `exec`, `aexec`, `log` | — | **not implemented**: running arbitrary shell commands from a chat is an RCE surface |
| CBML's `/addbot`, `/clone`, `/authorize`, `/addsudo` | — | not implemented: Meiko's own bot manager and permission system own those flows |
| saver's `/login`, `/logout`, `/cancellogin` | — | **refused**: user-session login is the bypass the brief forbids |

---

## 10. Explicit gaps (not silently missing)

* **Real voice-chat streaming** needs `pytgcalls` + a signalling path; neither is
  installed. The player reports `simulation` and the queue/controls are real.
  Everything up to the call (queue, transport, resolution, mirror) is live.
* **daemon transfers**: aria2 is driven over JSON-RPC (add-then-poll, with the
  RPC secret) when `daemon_endpoint` answers; qBittorrent is only reachable for
  torrents and rclone only as a *destination*, so a URL job with `-qbit`/`-rclone`
  is refused with that reason. Nothing degrades silently to an HTTP copy, and
  `/taskbackend` pings each daemon instead of listing a hopeful boolean.
* **MTProto features** (WZgram) require the dependency plus an owner session.
* **Only `ffmpeg` ships in the bundled wheel** (`imageio-ffmpeg`). Metadata then
  comes from the `ffmpeg -i` banner — same dict shape, no ffprobe — and the
  encoder panel says so. Exporting from YouTube needs an egress-allowed host
  (the sandbox blocks it); the code path is exercised against a stub in
  `tests/test_toolchain.py`.
* **Payments/subscriptions** from the saver upstream were not ported.
* **Postgres-specific behaviour** is untested here (SQLite in the sandbox).

### 10.1 What became real

Closing the gaps that used to be listed here changed three modules. The media
binaries now come from **one resolver**, `meiko/modules/toolchain.py`, consulted
by the encoder runner, the player's voice-chat check, telemetry and the API — so
the dashboard can never claim a capability the encoder does not have.

| layer | before | now |
| --- | --- | --- |
| `shutil.which("ffmpeg")` in 3 places | could disagree; a bundled binary was invisible | `toolchain.resolve()`: `PATH` → `MEIKO_FFMPEG_BIN`/`MEIKO_FFPROBE_BIN` → `imageio-ffmpeg` wheel, each answer records its `source` |
| probes | refused to run without `ffprobe` | `toolchain.probe()` uses ffprobe when present, else parses the `ffmpeg -i` banner into the identical JSON shape (`parse_banner`, pure and tested) |
| encoder gate | required both binaries | requires `ffmpeg`; `probe_fallback` reports the missing half, `/encinfo` and the Studio say which mode they are in |
| music search | catalogue + owner relay only | adds `YtDlpBackend` (yt-dlp) as the **last** layer, so an operator's relay still wins; searches and extraction run in a worker thread |
| page links (`/play <youtube url>`) | queued as an opaque URL with no stream | extracted to a real `stream_url` before queueing, with the direct track kept if extraction fails |
| leech | local copy / HTTP only | `-ytdl` selects yt-dlp for this task; `-aria2/-qbit/-rclone` fail closed with the reason |
| `/musictags`, `/tag` | MP3 (ID3v2) only | MP3 stays in-tree; M4A/MP4/FLAC/OGG/Opus go through `mutagen` when installed, and an unsupported container is named instead of mangled |

Every one of those behaves identically when the optional packages are absent:
the layer reports itself unavailable, the reason names the install step, and no
test asserts a capability that the host might not have.

---

## 11. Tests

| file | covers |
| --- | --- |
| `tests/test_modules_infra.py` | store, events, jobs, ledgers, media, progress, mtproto |
| `tests/test_modules_services.py` | tokens, FileStore tenancy, Linkshare lifecycle, Leech adapters, Music sessions/transport |
| `tests/test_api_modules.py` | every module route, tenancy, saver mirror, effective plugin state, SSE auth |
| `tests/test_modules_contract.py` | manifests, disabled-module inertness, route coverage, page/nav coverage, honesty, tenant isolation |
| `tests/test_toolchain.py` | binary resolution order, banner parsing, a real encode/probe, yt-dlp plumbing (stubbed), daemon flags failing closed, mutagen round-trips, telemetry/encoder agreement |
| `tests/test_url_policy.py` | 19 refusals and 5 acceptances, then the same rules asserted through linkshare, leech, music and the API validator |
| `tests/test_webapp_transport.py` | a dashboard action (pause/skip/stop/remote play) drives the *real* started plugin and the bot's own queue; room isolation |
| `tests/test_music_player.py`, `test_encoder.py`, `test_filestore.py`, `test_linkshare_leech.py`, `test_saver_pipeline.py` | per-module behaviour through the Telegram harness |

Acceptance scenarios from the brief are exercised as: music session + queue +
controls + per-group isolation + socket auth; saver authorized import/export +
retention + permission checks; encoder probe/preset/encode-plan/validation +
safe cancel; filestore register/ownership/link/independent settings; linkshare
generate/validate/revoke/access; leech authorized transfer/progress/cancel/retry;
platform disable-module → no handlers/jobs, tenant isolation, quotas.
