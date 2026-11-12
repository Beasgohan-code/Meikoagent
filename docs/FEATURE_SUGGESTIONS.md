# MEIKO AGENT — Feature Suggestions

> **Status note (2026-11-12):** item **14** (durable plugin state) has been implemented
> as part of the plugin-package work and is kept below for context. While building the
> packages, four defects surfaced that the list did not anticipate and that are worth
> reading as a warning rather than a backlog item:
>
> * **command hand-off** — aiogram filters receive the *event*, not the text, unless the
>   magic filter is on the left; a predicate written either way silently mis-fires,
> * **catch-all handlers** — `F.text` on a plugin router swallows other plugins' commands,
>   with no error anywhere (fixed by `support/filters.py`),
> * **unbound sent objects** — `send_message(...).edit_text(...)` raises `RuntimeError` in
>   aiogram 3.x; progress messages never updated (fixed by `MeikoPlugin.edit_text`),
> * **falsy registry** — an empty `CommandRegistry` is falsy (it defines `__len__`), so
>   `commands or default` silently replaced it. Audit the codebase for the same pattern on
>   any object that defines `__len__`.

*Curated, costed and ranked. Every item below is something this codebase does not
do today; each one names the file it would live in, the risk it carries, and how
you would know it works. Filler ("add dark mode", "add more emojis") is excluded
on purpose.*

Legend — **effort**: S (<1 day) · M (1–3 days) · L (1–2 weeks) · XL (needs infra).
**priority**: P0 = do next, P1 = next quarter, P2 = nice to have.

---

## 1. Plugin packs as signed archives (P0 · M)

Today a "third-party plugin" is a manifest row with an `approved` flag; there is no
way to actually ship one. Add `meiko/plugins/loader.py` that reads a `.zip`, verifies
a detached Ed25519 signature over `sha256(tree)`, extracts to
`DATA_DIR/plugins/installed/<slug>/<version>/`, then imports it through the same
`registry` path builtins use. The manifest already carries `checksum`, `approved`,
`min_platform_version` — the loader just has to honour them.

*Why it matters*: it turns the marketplace tab from a list into a distribution channel.
*Test*: install a signed pack, tamper one byte of the archive, assert load is refused and
the failure is audited.

## 2. Per-installation secret vault (P0 · S)

Right now plugin config lives in `plugin_installations.config` as plain JSON —
including API keys for the `ai` plugin. Move `type: "secret"` fields into
`meiko/security/vault.py` using the existing `cryptography` Fernet key, store only a
`vault:<uuid>` reference in the config, and resolve it at `PluginContext.config` read
time. Rotating the key must re-encrypt without touching plugin code.

*Test*: a config dump of an installed plugin must not contain the secret value.

## 3. Dry-run mode as a first-class plugin capability (P0 · M)

Every destructive plugin action (saver copy, renamer re-upload, automation action,
moderation ban) should be able to run with `dry_run` in its config and report the plan
without touching Telegram. Start by making `PluginContext` expose `ctx.dry_run` derived
from config and have the `saver`/`renamer`/`automation` packages print their plan when
set. This is the single best defence against a mis-configured plugin on a live channel.

## 4. Quota + retention console per plugin scope (P1 · S)

`meiko/plugins/support/files.py` already tracks quota, TTL and can `sweep()`. Surface
it: `GET /plugins/{slug}/storage` returning entries/usage/TTL, plus a "purge now"
button on the plugin detail page. Storage that users cannot see is storage they will
eventually run out of.

## 5. Plugin dependency graph with automatic ordering (P1 · M)

`PluginManifest.dependencies` exists and `PluginService._check_dependencies` refuses to
enable a plugin whose dependency is off — but the resolution is per-scope and has no
cycle detection. Add a topological sort in `meiko/plugins/manager.py` so
`reconcile()` starts dependencies first and stopping a plugin warns about dependents.

*Test*: a 3-node dependency chain boots in order; a cycle raises `PluginLifecycleError`
with the cycle path.

## 6. Saver: incremental channel sync (P1 · M)

The `saver` package copies explicit links/ranges. Add `/savesync <channel>` that stores
the last seen message id per source and copies anything newer on a schedule
(`ctx.schedule`, already supported). This is what turns the saver into a mirror bot.

## 7. Renamer: per-chat template profiles (P1 · S)

Currently one template per bot scope. Store `profiles: {chat_id: template}` so an anime
channel and a movie channel can format differently without installing the plugin twice.

## 8. Renamer: metadata-driven naming via ffprobe when present (P2 · S)

`renamer/engine.py` parses filenames only. If `ffprobe` exists on the host (the
code_runner already probes binaries with `shutil.which`), read duration/resolution and
use them as `{quality}`/`{duration}` fallbacks — with a hard timeout and no shell.

## 9. Music: playlist export (P1 · S)

Build an `.m3u8` (or JSON) from a `MusicLibrary` filter and send it as a document. Since
the catalogue already stores chat + message ids, the playlist doubles as a set of deep
links.

## 10. Music: duplicate detection by audio fingerprint (P2 · L)

The ID3 writer strips and rewrites tags, so the raw audio bytes after `strip_existing()`
are a stable identity. Hash the first and last 64 KB of the audio payload → cheap
"same song, different tags" detection. No external service needed.

## 11. Post maker: entity-aware character counter (P1 · S)

`templates.py` validates HTML, but not the real published length. Add a counter that
strips tags the way Telegram does and shows text / caption / 1024 / 4096 budgets in the
preview panel — the most common cause of a rejected scheduled post.

## 12. Post maker: recurring posts (P1 · M)

`PostQueue` stores one-shot `send_at`. Add `rrule`-style repeats using `croniter`
(already a dependency) so `/schedule +1d weekly` posts a digest every day until
cancelled, with a `max_sends` guard.

## 13. Keep-alive: multi-region heartbeat (P2 · M)

Free hosts die silently. Let the plugin POST a heartbeat to a public uptime endpoint
(opt-in, `http.outbound`) and additionally expose `GET /health` already present — the
watchdog then distinguishes "Telegram unreachable" from "this process is frozen".

## 14. ~~Cache-layer plugin state with Redis fallback semantics~~ — **done**

`meiko/plugins/support/state.py` now mirrors every `PluginState` write to
`DATA_DIR/plugins/<slug>/<scope>/_state/*.json` (atomic rename) and falls back to that
mirror on a cache miss. `MeikoPlugin.state` exposes it to plugins, and `saver`,
`post_maker`, `keep_alive` and `music` use it, so a restart no longer wipes the saved
index, the scheduled queue or the heartbeat history. Covered by
`TestDurablePluginState` (including a cache-wipe simulation).

## 15. Command-level audit trail (P1 · S)

`AuditService` records API mutations. Bot-side commands are not audited at all. Add a
`CommandAuditMiddleware` that logs `(bot, chat, user, command, plugin, outcome)` for
every handler registered through `PluginContext.add_router`. One middleware, and the
"who ran /save on production" question becomes answerable.

## 16. Rate limiting per plugin, not per chat (P1 · S)

`RateLimitMiddleware` is chat-scoped. A plugin that fans out (saver over 500 messages) is
the real Telegram-429 risk. Wrap `ctx.bot` in a token-bucket proxy with a configurable
`requests_per_second` per installation and surface 429 retries in the plugin log.

## 17. Moderation: escalating policy ladders (P1 · M)

`meiko/moderation` decides per message. Add a per-user strike model that escalates
warn → mute → kick → ban across a rolling window, configured as a list of steps in the
`security` plugin config, with an `undo` endpoint that reverses the last applied step.

## 18. Anti-spam: ML-lite similarity clustering (P2 · M)

`anti_spam` uses regex rules. Add shingled MinHash over recent messages (pure python,
no deps) to catch copy-paste spam across users — the classic attack on Indian community
groups that keyword rules always miss.

## 19. Analytics: cohort retention instead of counters (P1 · M)

`analytics` publishes digests of totals. Add day-1/day-7/day-30 retention per join
cohort, computed from `ActivityEvent`, because totals never tell you whether the group
is healthy.

## 20. Developer plugin: live OpenAPI playground for plugin APIs (P2 · S)

`/developer` already lists endpoints. Add a form-driven "try it" panel that calls the
API with the current session token and shows request/response — the fastest way for a
plugin author to iterate.

## 21. Scheduler: catch-up execution (P0 · S)

`PluginManager._job_loop` now supports `run_immediately`, but after downtime a job still
waits a full interval. Record `last_run_at` per job and run overdue jobs once at boot
(bounded catch-up of one iteration) so a 6-hourly sweep does not drift by hours.

## 22. Plugin health score in the UI (P2 · S)

Combine: last error, log error rate, restart count, and heartbeat status into a 0–100
score shown on the card. Saves the owner from reading logs on 18 plugins.

## 23. Code runner: resource profile presets (P1 · S)

`RunLimits` is per-config. Add named presets (`strict` / `normal` / `lab`) selectable per
run (`/run --profile lab …`), so a trusted test does not need the owner to loosen global
limits permanently.

## 24. Code runner: output diffing for repeated snippets (P2 · M)

Store `sha256(source) → last output` per scope and show a unified diff when the same
snippet runs again. Cheap, and it turns the runner into a regression detector for
automation snippets.

## 25. Backup/restore of a bot as a single bundle (P1 · M)

`BotService.delete()` is explicit about cascades. Build the inverse: export a bot
(token excluded, encrypted separately), its plugins, configs, groups and templates to a
`.zip`; import creates a clone with re-mapped ids. This is the feature that makes
self-hosting sane.

## 26. Per-group plugin overrides in the webapp (P1 · M)

Scopes are `group → bot → global` in the backend but the UI only exposes bot/global.
Add a group selector on the plugin detail page writing to `PluginInstallation.group_id`
— no backend work beyond exposing the existing resolution.

## 27. Localisation of user-facing strings (P2 · M)

All UI strings are HTML literals in `meiko/ui`. Extract them into
`meiko/ui/i18n.py` with `en`/`ml`/`hi` catalogues; Malayalam and Hindi matter for this
user base, and the `esc()` boundary already exists.

## 28. Webhook transport as an alternative to polling (P2 · L)

`meiko/telegram/client.py` polls. For hosts that allow inbound traffic, webhook mode with
secret-token verification removes the need for keep-alive pings entirely — and makes
the heartbeat plugin a monitoring tool rather than a necessity.

---

### Suggested order of work

1. **P0 block** — 1 (signed packs), 2 (secret vault), 21 (scheduler catch-up),
   3 (dry-run).  *(#14 durable plugin state shipped.)*
2. **P1 block** — 6, 12, 26 (user-visible features), then 15, 16, 22 (operability).
3. **P2 block** — 10, 18, 24, 27, 28.

Each item above is written so it can become a pull request of its own with its own
tests; none of them requires a new external service the sandbox cannot run.
