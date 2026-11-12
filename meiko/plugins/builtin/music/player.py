"""``music``/player – the full voice-chat playback surface.

Command map (Playeon parity)::

    /play <q|url> [-f]      queue music (or a link)
    /vplay <q|url> [-hd]    queue video at a quality (aliases: /stream)
    /pause /resume          transport
    /skip /prev /end        transport (aliases: /stop)
    /seek <m:ss|±s> /fw /bw scrubbing
    /loop <n|off> /loopq    repeat
    /autoplay [-f]          keep playing after the queue drains
    /queue [page] /q        queue view
    /clear /remove <n> /move <n> <m> /shuffle /pick <n>
    /playing (/np /nowplaying), /style, /mode, /speed, /volume
    /join /leave /room /vc  voice-chat lifecycle

Design notes
-----------
* The queue is durable: it is stored through :meth:`MeikoPlugin.state`, keyed by
  the chat, so a restart does not forget what was playing.
* Playback is *modelled* unless a real backend exists.  :func:`vc_available`
  decides; when it is ``False`` the card says ``simulation`` instead of lying
  about being in a call.
* Controls arrive as callback data (``pb:*`` / ``q:*``) exactly like Playeon's
  play card, so the same buttons work from a card or a queued webapp.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import OwnCommand
from meiko.ui import Btn, esc, keyboard, message, user_mention
from .manifest import MANIFEST
from .player_card import (
    clock,
    play_card,
    player_keyboard,
    queue_card,
    queue_keyboard,
    results_card,
    results_keyboard,
    state_line,
    track_line,
)
from .queue import MAX_QUEUE, PlayerQueue, Track
from .sources import (
    MediaResolver,
    Playlist,
    PlayRequest,
    missing_vc_requirements,
    parse_request,
    quality_label,
    track_from_telegram,
    vc_available,
)

#: Playback names this module answers (the toolkit names stay in ``plugin.py``).
PLAYER_COMMAND_NAMES = [
    "play", "vplay", "stream", "fplay", "pause", "resume", "skip", "prev",
    "seek", "fw", "bw", "end", "stop", "loop", "loopq", "autoplay",
    "queue", "q", "clear", "remove", "move", "shuffle", "pick", "playlist",
    "savequeue", "loadqueue", "delqueue", "chart", "now", "next",
    "playing", "nowplaying", "np", "style", "mode", "speed", "volume",
    "join", "leave", "room", "vc", "vcinfo", "auth", "mode3d",
]
#: Only dispatch the player's own commands – never the whole ``/`` space.
OWN_COMMAND = OwnCommand(PLAYER_COMMAND_NAMES)
#: The ``music`` toolkit owns the audio catalogue; the player reuses it.
MUSIC_INDEX_KEY = "index:tracks"
STYLES = {"default", "nightcore", "vaporwave", "bassboost", "8d", "karaoke", "lofi"}
MODES = {"audio", "video", "both"}
TICK_SECONDS = 5.0


def _is_admin(message_obj: Message) -> bool:
    """Cheap check: private chats are always allowed, groups defer to /next only."""
    return message_obj.chat.type == "private"


def parse_seek(text: str) -> int | None:
    """``1:23`` → 83, ``-10`` → -10, ``+1:00`` → 60; ``None`` when unparsable."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    sign = 1
    if raw[0] in "+-":
        sign = -1 if raw[0] == "-" else 1
        raw = raw[1:]
    parts = raw.split(":")
    if len(parts) == 1:
        if not parts[0].isdigit():
            return None
        return sign * int(parts[0])
    if len(parts) > 3 or not all(part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return sign * seconds


class PlayerMixin:
    """Playback half of :class:`~.plugin.MusicPlugin`.

    Kept as a mixin so the toolkit (catalogue, ID3, covers) and the player
    share one installation, one ``/`` namespace and one durable state file.
    """

    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._resolver: MediaResolver | None = None
        self._locks: dict[int, asyncio.Lock] = {}
        self._tick: asyncio.Task[None] | None = None
        self._last_card: dict[int, int] = {}
        self._sessions: Any = None
        self._mirror = True

    # -- lifecycle (called from MusicPlugin.on_start) ----------------------
    async def start_player(self, ctx: PluginContext) -> None:
        catalogue = None
        if ctx.can("storage.read"):
            from meiko.plugins.builtin.music.library import MusicLibrary

            entries = await ctx.get_state(MUSIC_INDEX_KEY, None) or []
            catalogue = _Catalogue(ctx, MusicLibrary(list(entries)))
        remote = None
        endpoint = str(self.config("resolve_endpoint", "") or "").strip()
        if endpoint and ctx.can("http.outbound"):
            from .sources import RemoteBackend

            remote = RemoteBackend(endpoint, str(self.config("resolve_token", "")))
        self._resolver = MediaResolver(
            catalogue=catalogue,
            remote=remote,
            limit=int(self.config("max_results", 8)),
        )

    def register_player_routes(self, router: Router) -> Router:
        router.message.register(self.on_player_command, F.text.startswith("/"), OWN_COMMAND)
        router.callback_query.register(self.on_callback, F.data.startswith("pb:") | F.data.startswith("q:"))
        return router

    @property
    def chart(self) -> Any:
        """Set by the plugin when ``sources`` are wired (see ``sources.PlaylistManager``)."""
        return getattr(self, "_chart", None)

    async def stop_player(self) -> None:
        if self._tick is not None:
            self._tick.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick
            self._tick = None
        self._resolver = None

    # -- state ------------------------------------------------------------
    def lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def load_queue(self, chat_id: int) -> PlayerQueue:
        data = await self.state.get(f"queue:{chat_id}", None) if self.context else None
        queue = PlayerQueue.from_dict(data) if data else PlayerQueue()
        if not data:
            if self.config("autoplay_default", False):
                queue.set_autoplay(True)
            queue.set_volume(int(self.config("default_volume", 100)))
        return queue

    async def save_queue(self, chat_id: int, queue: PlayerQueue) -> None:
        if self.context is not None:
            await self.state.set(f"queue:{chat_id}", queue.to_dict())
        # one mirror for the dashboard: every mutation (command, callback or
        # webapp action) lands here, so the Music Studio is never stale
        if getattr(self, "_mirror", True):
            await self.publish_room(chat_id, queue)

    async def load_room(self, chat_id: int) -> dict[str, Any]:
        data = await self.state.get(f"room:{chat_id}", None) if self.context else None
        return dict(data or {})

    async def save_room(self, chat_id: int, room: dict[str, Any]) -> None:
        if self.context is not None:
            await self.state.set(f"room:{chat_id}", room)

    # -- webapp transport -------------------------------------------------
    def _session_store(self) -> Any:
        cached = getattr(self, "_sessions", None)
        if cached is None:
            from meiko.modules.music import MusicSessions
            from meiko.modules.store import get_module_store

            cached = MusicSessions(get_module_store())
            self._sessions = cached
        return cached

    def _bot_id(self) -> int:
        return int(getattr(self.context, "bot_id", 0) or 0)

    async def publish_room(self, chat_id: int, queue: PlayerQueue, *, room: dict[str, Any] | None = None) -> None:
        """Mirror the live queue into the shared store so the webapp sees the truth."""
        snapshot = queue.to_dict()
        if room is not None:
            snapshot["room"] = room
        try:
            self._session_store().publish_room(self._bot_id(), chat_id, snapshot)
        except Exception:  # pragma: no cover - the mirror must never break playback
            pass

    async def apply_transport(
        self,
        *,
        bot_id: int,
        chat_id: int,
        action: str,
        value: Any = None,
        actor_id: int = 0,
    ) -> dict[str, Any]:
        """Drive the bot's own queue from the dashboard (same state, same controls)."""
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if action in {"pause", "resume", "skip", "previous", "stop", "seek", "volume", "shuffle", "loop"}:
                if queue.current is None and action not in {"stop"}:
                    return {"reason": "nothing is playing in that room"}
            if action == "pause":
                queue.pause()
            elif action == "resume":
                queue.resume()
            elif action == "skip":
                queue.next()
            elif action == "previous":
                queue.previous()
            elif action == "stop":
                queue.clear()
                room = await self.load_room(chat_id)
                room["stopped_by"] = actor_id
                await self.save_room(chat_id, room)
            elif action == "seek":
                queue.seek(int(value or 0))
            elif action == "volume":
                queue.set_volume(int(value or 100))
            elif action == "shuffle":
                queue.shuffle()
            elif action == "loop":
                queue.set_loop(int(value or 0))
            elif action == "play":
                return await self._remote_play(chat_id, str(value or ""), actor_id)
            await self.save_queue(chat_id, queue)
        await self.publish_room(chat_id, queue)
        return {"state": queue.to_dict(), "action": action}

    async def _remote_play(self, chat_id: int, query: str, actor_id: int) -> dict[str, Any]:
        if not query:
            return {"reason": "no query supplied"}
        resolver = self._resolver
        if resolver is None:
            return {"reason": "the player is not initialised yet"}
        request = parse_request(query)
        outcome = await resolver.resolve(request, requested_by=str(actor_id or ""))
        tracks = [track for track in (getattr(outcome, "tracks", None) or []) if track]
        if not tracks:
            return {"reason": "nothing matched that query"}
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            queue.enqueue(tracks[0])
            if queue.current is None:
                queue.start(tracks[0])
            await self.save_queue(chat_id, queue)
        await self.publish_room(chat_id, queue)
        return {"queued": tracks[0].label, "state": queue.to_dict()}

    # -- dispatch ---------------------------------------------------------
    async def on_player_command(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        raw_command, _, rest = text.partition(" ")
        command = raw_command.split("@")[0].lower()
        argument = rest.strip()
        handler = {
            "/play": self.cmd_play,
            "/fplay": lambda m, a: self.cmd_play(m, f"-f {a}".strip()),
            "/vplay": lambda m, a: self.cmd_vplay(m, a),
            "/stream": lambda m, a: self.cmd_vplay(m, a),
            "/pause": self.cmd_pause,
            "/resume": self.cmd_resume,
            "/skip": self.cmd_skip,
            "/prev": self.cmd_prev,
            "/end": self.cmd_end,
            "/stop": self.cmd_end,
            "/seek": self.cmd_seek,
            "/fw": lambda m, a: self.cmd_jump(m, a, 10),
            "/bw": lambda m, a: self.cmd_jump(m, a, -10),
            "/loop": self.cmd_loop,
            "/loopq": self.cmd_loopq,
            "/autoplay": self.cmd_autoplay,
            "/queue": self.cmd_queue,
            "/q": self.cmd_queue,
            "/clear": self.cmd_clear,
            "/remove": self.cmd_remove,
            "/move": self.cmd_move,
            "/shuffle": self.cmd_shuffle,
            "/pick": self.cmd_pick,
            "/playing": self.cmd_playing,
            "/nowplaying": self.cmd_playing,
            "/np": self.cmd_playing,
            "/style": self.cmd_style,
            "/mode": self.cmd_mode,
            "/speed": self.cmd_speed,
            "/volume": self.cmd_volume,
            "/join": self.cmd_join,
            "/leave": self.cmd_leave,
            "/room": self.cmd_room,
            "/vc": self.cmd_vcinfo,
            "/vcinfo": self.cmd_vcinfo,
            "/auth": self.cmd_auth,
            "/mode3d": self.cmd_mode3d,
            "/playlist": self.cmd_playlist,
            "/savequeue": self.cmd_save_queue,
            "/loadqueue": self.cmd_load_queue,
            "/delqueue": self.cmd_del_queue,
            "/chart": self.cmd_chart,
            "/now": self.cmd_now,
            "/next": self.cmd_next,
        }.get(command)
        if handler is None:
            return False  # not ours – let MusicPlugin handle its own commands
        await handler(message_obj, argument)
        return True

    # -- playback ---------------------------------------------------------
    async def cmd_play(self, message_obj: Message, argument: str, *, video: bool = False) -> None:
        request = parse_request(argument, video=video)
        if video and request.height == 0:
            request.height = int(self.config("default_quality", 1080)) if str(
                self.config("default_quality", 1080)
            ).isdigit() else 1080
        await self._resolve_and_queue(message_obj, request)

    async def cmd_vplay(self, message_obj: Message, argument: str) -> None:
        await self.cmd_play(message_obj, argument, video=True)

    async def _resolve_and_queue(self, message_obj: Message, request: PlayRequest) -> None:
        ctx = self.context
        assert ctx is not None
        chat_id = message_obj.chat.id
        if not request.query and not request.url:
            await message_obj.reply(
                message("Usage", "warn")
                .line("<code>/play &lt;song or link&gt; [-f]</code> – queue music", raw=True)
                .line("<code>/vplay &lt;name or link&gt; [-hd|-fhd|-4k]</code> – queue video", raw=True)
                .line("Reply to a file with <code>/play</code> to add it.", raw=True)
                .build()
            )
            return

        if request.query in ("", " ") and message_obj.reply_to_message is not None:
            request.url = ""

        # a replied audio/video document beats any text argument
        track = self._track_from_reply(message_obj)
        resolver = self._resolver
        layer = "reply"
        if track is None:
            assert resolver is not None
            outcome = await resolver.resolve(request, requested_by=self._display(message_obj))
            if not outcome.ok:
                await message_obj.reply(
                    message(f"Nothing found · {request.query or request.url}"[:60], "search")
                    .line(esc(outcome.reason or "no results"), raw=True)
                    .line(
                        "<i>Set <code>resolve_endpoint</code> in the plugin config to search a "
                        "yt-dlp/relay service, or upload the file and reply <code>/play</code>.</i>",
                        raw=True,
                    )
                    .build()
                )
                return
            layer = outcome.layer
            if len(outcome.tracks) > 1 and not request.play_now:
                await self._offer_results(message_obj, request, outcome)
                return
            track = outcome.first
        assert track is not None

        if track.duration and track.duration > int(self.config("max_duration", 10800)):
            await message_obj.reply(message("Too long", "warn").line("That track exceeds the limit.", raw=True).build())
            return

        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if not self.config("allow_duplicates", False):
                playing = [queue.current] if queue.current is not None else []
                existing = [t.source for t in [*playing, *queue.state.items] if t.source]
                if track.source and track.source in existing:
                    await message_obj.reply(message("Already queued", "warn").build())
                    return
            if request.video:
                track.kind = "video"
                if request.height:
                    track.height = request.height
            if queue.current is None or request.play_now:
                queue.play_now(track) if queue.current is not None else queue.start(track)
                started = True
            else:
                position = queue.enqueue(track)
                if position == 0:
                    await message_obj.reply(message("Queue full", "warn").line(f"Limit is {MAX_QUEUE} tracks.", raw=True).build())
                    return
                started = False
            await self.save_queue(chat_id, queue)

        if started:
            await self._send_card(message_obj, queue, track)
        else:
            await message_obj.reply(
                message("Added to queue", "queue")
                .key_value("Track", track.label, "music")
                .key_value("Position", str(len(queue.state.items)), "stats")
                .key_value("Source", layer, "database")
                .build(),
                reply_markup=self._queue_button(),
            )

    async def _offer_results(self, message_obj: Message, request: PlayRequest, outcome: Any) -> None:
        """Several candidates: let the user pick instead of guessing."""
        chat_id = message_obj.chat.id
        tracks = outcome.tracks[:10]
        if self.context is not None:
            await self.state.set(f"results:{chat_id}", [track.to_dict() for track in tracks])
        heading = request.query or request.url
        await message_obj.reply(
            results_card(heading, tracks, layer=outcome.layer),
            reply_markup=results_keyboard(tracks),
            disable_web_page_preview=True,
        )

    def _track_from_reply(self, message_obj: Message) -> Track | None:
        reply = message_obj.reply_to_message
        if reply is None:
            return None
        if reply.audio is not None:
            audio = reply.audio
            return track_from_telegram(
                audio.file_id,
                title=audio.title or audio.file_name or "audio",
                kind="audio",
                duration=audio.duration,
            )
        if reply.voice is not None:
            return track_from_telegram(reply.voice.file_id, title="voice message", kind="audio", duration=reply.voice.duration)
        if reply.video is not None:
            return track_from_telegram(
                reply.video.file_id,
                title=reply.video.file_name or "video",
                kind="video",
                duration=reply.video.duration,
            )
        if reply.document is not None and (reply.document.mime_type or "").startswith(("audio/", "video/")):
            kind = "video" if reply.document.mime_type.startswith("video/") else "audio"
            return track_from_telegram(
                reply.document.file_id,
                title=reply.document.file_name or "document",
                kind=kind,
            )
        return None

    async def _send_card(self, message_obj: Message, queue: PlayerQueue, track: Track) -> None:
        manager = self.chart
        if manager is not None and track is not None:
            manager.record_play(track)
            await manager.save()
        simulated = not vc_available()
        body = play_card(queue, requested_by=self._display(message_obj))
        if simulated:
            body += "\n<i>simulation – no voice-chat backend installed</i>"
        sent = await message_obj.reply(body, reply_markup=player_keyboard(queue), disable_web_page_preview=True)
        self._last_card[message_obj.chat.id] = sent.message_id

    async def _send_error(self, message_obj: Message, title: str, detail: str = "") -> None:
        builder = message(title, "warn")
        if detail:
            builder.line(esc(detail), raw=True)
        await message_obj.reply(builder.build())

    # -- transport --------------------------------------------------------
    async def cmd_pause(self, message_obj: Message, _argument: str = "") -> None:
        await self._transport(message_obj, "pause")

    async def cmd_resume(self, message_obj: Message, _argument: str = "") -> None:
        await self._transport(message_obj, "resume")

    async def _transport(self, message_obj: Message, action: str) -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if queue.current is None:
                await self._send_error(message_obj, "Nothing is playing", "Use /play first.")
                return
            queue.pause() if action == "pause" else queue.resume()
            await self.save_queue(chat_id, queue)
        label = "Paused" if action == "pause" else "Resumed"
        icon = "vc_pause" if action == "pause" else "vc_play"
        await message_obj.reply(
            message(f"{label} · {queue.current.label}"[:60], icon).line(state_line(queue), raw=True).build(),
            reply_markup=player_keyboard(queue),
        )

    async def cmd_skip(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if queue.current is None:
                await self._send_error(message_obj, "Nothing to skip")
                return
            skipped = queue.current
            following = queue.next(autoplay_pool=(await self._autoplay_pool(chat_id)))
            await self.save_queue(chat_id, queue)
        if following is None:
            await message_obj.reply(
                message(f"Skipped · {skipped.label}"[:60], "vc_skip").line("The queue is empty now.", raw=True).build(),
                reply_markup=keyboard([[Btn("Queue", callback_data="pb:queue", emoji="queue")]]),
            )
            return
        await self._announce(message_obj, queue, following, title="Skipped")

    async def cmd_prev(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            previous = queue.previous()
            await self.save_queue(chat_id, queue)
        if previous is None:
            await self._send_error(message_obj, "No previous track")
            return
        await self._announce(message_obj, queue, previous, title="Previous")

    async def _announce(self, message_obj: Message, queue: PlayerQueue, track: Track, *, title: str = "Now playing") -> None:
        sent = await message_obj.reply(
            play_card(queue, requested_by=track.requested_by).replace("Now playing", title, 1),
            reply_markup=player_keyboard(queue),
            disable_web_page_preview=True,
        )
        self._last_card[message_obj.chat.id] = sent.message_id

    async def cmd_end(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            removed = queue.clear()
            queue.state.current = None
            queue.state.paused = False
            queue.state.position = 0
            await self.save_queue(chat_id, queue)
            room = await self.load_room(chat_id)
            room["joined"] = False
            await self.save_room(chat_id, room)
        await message_obj.reply(
            message("Player stopped", "vc_end_text").line(f"Cleared {removed} queued track(s).", raw=True).build()
        )

    async def cmd_seek(self, message_obj: Message, argument: str) -> None:
        target = parse_seek(argument)
        if target is None:
            await self._send_error(message_obj, "Usage", "/seek 1:30 · /seek +15 · /seek -15")
            return
        await self._apply_seek(message_obj, target)

    async def cmd_jump(self, message_obj: Message, argument: str, default: int) -> None:
        amount = parse_seek(argument)
        delta = abs(amount) * (1 if default > 0 else -1) if amount is not None else default
        await self._apply_seek(message_obj, delta, relative=True)

    async def _apply_seek(self, message_obj: Message, value: int, *, relative: bool = False) -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if queue.current is None:
                await self._send_error(message_obj, "Nothing is playing")
                return
            position = queue.jump(value) if relative else queue.seek(value)
            await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message(f"Seek · {clock(position)}", "clock")
            .line(f"{queue.current.label}", raw=True)
            .line(state_line(queue), raw=True)
            .build(),
            reply_markup=player_keyboard(queue),
        )

    async def cmd_loop(self, message_obj: Message, argument: str) -> None:
        chat_id = message_obj.chat.id
        raw = (argument or "").strip().lower()
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if raw in ("", "on", "yes"):
                count = 1 if not queue.state.loop_count else queue.state.loop_count + 1
            elif raw in ("off", "0", "no"):
                count = 0
            elif raw.isdigit():
                count = int(raw)
            else:
                await self._send_error(message_obj, "Usage", "/loop 3 · /loop off")
                return
            count = queue.set_loop(count)
            await self.save_queue(chat_id, queue)
        label = f"Loop ×{count}" if count else "Loop off"
        await message_obj.reply(message(f"{label}", "refresh").line(state_line(queue), raw=True).build(),
                                reply_markup=player_keyboard(queue))

    async def cmd_loopq(self, message_obj: Message, argument: str) -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            enabled = (argument or "").strip().lower() not in ("off", "0", "no")
            enabled = queue.set_loop_queue(enabled)
            await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message("Queue loop on" if enabled else "Queue loop off", "refresh")
            .line(state_line(queue), raw=True)
            .build(),
            reply_markup=player_keyboard(queue),
        )

    async def cmd_autoplay(self, message_obj: Message, argument: str) -> None:
        chat_id = message_obj.chat.id
        raw = (argument or "").strip().lower()
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            enabled = not queue.state.autoplay if raw in ("", "toggle") else raw not in ("off", "0", "no")
            queue.set_autoplay(enabled)
            await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message("Autoplay on" if enabled else "Autoplay off", "spark")
            .line("Recommendations continue when the queue drains." if enabled else "Playback stops at the end of the queue.", raw=True)
            .build(),
            reply_markup=player_keyboard(queue),
        )

    # -- queue ------------------------------------------------------------
    async def cmd_queue(self, message_obj: Message, argument: str) -> None:
        page = int(argument) if (argument or "").isdigit() else 1
        queue = await self.load_queue(message_obj.chat.id)
        await message_obj.reply(
            queue_card(queue, page=page), reply_markup=queue_keyboard(queue, page=page)
        )

    async def cmd_clear(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            removed = queue.clear(keep_current=True)
            await self.save_queue(chat_id, queue)
        await message_obj.reply(message("Queue cleared", "trash").line(f"{removed} track(s) removed.", raw=True).build())

    async def cmd_remove(self, message_obj: Message, argument: str) -> None:
        if not (argument or "").strip().isdigit():
            await self._send_error(message_obj, "Usage", "/remove 3")
            return
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            removed = queue.remove(int(argument.strip()))
            if removed is not None:
                await self.save_queue(chat_id, queue)
        if removed is None:
            await self._send_error(message_obj, "No such position")
            return
        await message_obj.reply(
            message("Removed", "trash").line(esc(removed.label), raw=True).build(),
            reply_markup=queue_keyboard(queue),
        )

    async def cmd_move(self, message_obj: Message, argument: str) -> None:
        parts = (argument or "").split()
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            await self._send_error(message_obj, "Usage", "/move 4 1")
            return
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            moved = queue.move(int(parts[0]), int(parts[1]))
            if moved:
                await self.save_queue(chat_id, queue)
        if not moved:
            await self._send_error(message_obj, "Those positions are out of range")
            return
        await message_obj.reply(message("Moved", "arrow").build(), reply_markup=queue_keyboard(queue))

    async def cmd_shuffle(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            count = queue.shuffle()
            await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message("Shuffled", "refresh").line(f"{count} track(s) reordered.", raw=True).build(),
            reply_markup=queue_keyboard(queue),
        )

    async def cmd_pick(self, message_obj: Message, argument: str) -> None:
        if not (argument or "").strip().isdigit():
            await self._send_error(message_obj, "Usage", "/pick 2", "Use /play to search first.")
            return
        chat_id = message_obj.chat.id
        index = int(argument.strip()) - 1
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            # picks reference the last /play result list stored per chat
            results = await self.state.get(f"results:{chat_id}", []) if self.context else []
            if not results or index < 0 or index >= len(results):
                await self._send_error(message_obj, "No such result", "Search again with /play.")
                return
            track = Track.from_dict(results[index])
            if queue.current is None:
                queue.start(track)
            else:
                queue.enqueue(track)
            await self.save_queue(chat_id, queue)
            await self.state.delete(f"results:{chat_id}")
        await self._announce(message_obj, queue, track)

    # -- info / settings --------------------------------------------------
    async def cmd_playing(self, message_obj: Message, _argument: str = "") -> None:
        queue = await self.load_queue(message_obj.chat.id)
        if queue.current is None:
            await message_obj.reply(
                message("Nothing is playing", "sleep")
                .line("Use <code>/play &lt;song&gt;</code> to start.", raw=True)
                .build()
            )
            return
        await message_obj.reply(
            play_card(queue, requested_by=queue.current.requested_by),
            reply_markup=player_keyboard(queue),
            disable_web_page_preview=True,
        )

    async def cmd_style(self, message_obj: Message, argument: str) -> None:
        style = (argument or "").strip().lower()
        chat_id = message_obj.chat.id
        if style not in STYLES:
            await message_obj.reply(
                message("Audio styles", "palette")
                .line(" · ".join(sorted(STYLES)), raw=True)
                .line("Use <code>/style bassboost</code>", raw=True)
                .build()
            )
            return
        await self.state.set(f"style:{chat_id}", style)
        await message_obj.reply(message(f"Style · {style}", "palette").line(
            "Applied to the next track that starts.", raw=True).build())

    async def cmd_mode(self, message_obj: Message, argument: str) -> None:
        mode = (argument or "").strip().lower()
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if mode in MODES:
                queue.set_mode(mode)
                await self.save_queue(chat_id, queue)
                current = mode
            else:
                current = queue.state.mode
        await message_obj.reply(
            message(f"Mode · {current}", "video" if current == "video" else "music")
            .line(" · ".join(sorted(MODES)), raw=True)
            .build(),
            reply_markup=keyboard([[Btn("Audio", callback_data="pb:mode", emoji="music"),
                                    Btn("Video", callback_data="pb:mode", emoji="video")]]),
        )

    async def cmd_speed(self, message_obj: Message, argument: str) -> None:
        raw = (argument or "").strip().lower().rstrip("x")
        try:
            speed = float(raw)
        except ValueError:
            speed = 1.0
        speed = max(0.5, min(2.5, speed))
        chat_id = message_obj.chat.id
        if self.context is not None:
            await self.state.set(f"speed:{chat_id}", speed)
        await message_obj.reply(
            message(f"Speed · {speed:g}×", "rocket").line("0.5× – 2.5× supported.", raw=True).build()
        )

    async def cmd_volume(self, message_obj: Message, argument: str) -> None:
        raw = (argument or "").strip().rstrip("%")
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            volume = queue.set_volume(int(raw)) if raw.lstrip("+-").isdigit() else queue.state.volume
            await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message(f"Volume · {volume}%", "unmute").build(), reply_markup=player_keyboard(queue)
        )

    # -- voice chat lifecycle ---------------------------------------------
    async def cmd_join(self, message_obj: Message, _argument: str = "") -> None:
        await self._join(message_obj)

    async def _join(self, message_obj: Message) -> bool:
        chat_id = message_obj.chat.id
        available = vc_available()
        room = await self.load_room(chat_id)
        room.update({"joined": available, "chat_id": chat_id, "requested_by": self._display(message_obj)})
        await self.save_room(chat_id, room)
        if not available:
            missing = ", ".join(missing_vc_requirements()) or "a voice-chat backend"
            await message_obj.reply(
                message("Voice chat unavailable", "warn")
                .line("This host cannot stream into a call yet. Missing:", raw=True)
                .line(f"• {esc(missing)}", raw=True)
                .blank()
                .line("<i>The player still queues, tracks progress and renders the full card – "
                       "it just does not join the call.</i>", raw=True)
                .build()
            )
            return False
        await message_obj.reply(
            message("Joined voice chat", "vc_join")
            .line("Use <code>/play</code> to start the music.", raw=True)
            .build()
        )
        return True

    async def cmd_leave(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        room = await self.load_room(chat_id)
        room["joined"] = False
        await self.save_room(chat_id, room)
        await message_obj.reply(message("Left voice chat", "vc_leave").build())

    async def cmd_room(self, message_obj: Message, _argument: str = "") -> None:
        chat_id = message_obj.chat.id
        queue = await self.load_queue(chat_id)
        room = await self.load_room(chat_id)
        await message_obj.reply(
            message("Room", "link")
            .key_value("Chat", str(chat_id), "message")
            .key_value("In call", "yes" if room.get("joined") else "no", "vc_join" if room.get("joined") else "cross")
            .key_value("Track", queue.current.label if queue.current else "—", "music")
            .key_value("Queued", str(queue.state.remaining), "queue")
            .key_value("Backend", "live" if vc_available() else "simulation", "server")
            .build(),
            reply_markup=keyboard([[Btn("Mention the room", copy_text=f"vc:{chat_id}", emoji="link")]]),
        )

    async def cmd_vcinfo(self, message_obj: Message, _argument: str = "") -> None:
        available = vc_available()
        missing = missing_vc_requirements()
        builder = message("VC status", "vc_play" if available else "warn")
        builder.key_value("Streaming backend", "installed" if available else "missing", "check" if available else "cross")
        if missing:
            builder.line("", raw=True).line("Missing: " + ", ".join(esc(m) for m in missing), raw=True)
        builder.key_value("Queue limit", str(MAX_QUEUE), "queue")
        builder.key_value("Default quality", quality_label(0) if not str(self.config("default_quality", "1080")).isdigit()
                          else f"{self.config('default_quality', '1080')}p", "video")
        await message_obj.reply(builder.build())

    # -- Playeon extras: auth, 3D mode, playlists, chart, now/next ---------
    async def cmd_auth(self, message_obj: Message, argument: str = "") -> None:
        """Playeon's ``/auth``: explain how sessions are authorised, never store one."""
        token = (message_obj.text or "").split(" ", 1)[1].strip() if " " in (message_obj.text or "") else ""
        if token:
            # never echo a secret back into the chat
            if self.context is not None:
                await self.context.delete_state("auth:input")
            await message_obj.reply(
                message("Auth", "key")
                .line("Send session strings in a private chat, not a group.", raw=True)
                .line("Nothing was stored — the bot does not accept credentials by message.", raw=True)
                .build()
            )
            return
        room = await self.load_room(message_obj.chat.id)
        await message_obj.reply(
            message("Auth", "key")
            .key_value("Room", str(message_obj.chat.id), "message")
            .key_value("Backend", "live" if vc_available() else "simulation", "server")
            .key_value("Session", "not required" if not vc_available() else "verified", "lock")
            .key_value("Requested by", str(room.get("requested_by") or "—"), "user")
            .line("Owner sessions live in the platform config, not in chat.", raw=True)
            .build()
        )

    async def cmd_mode3d(self, message_obj: Message, argument: str = "") -> None:
        """Playeon's ``/3d``: an audio-spatialisation preset for the next track."""
        chat_id = message_obj.chat.id
        raw = (argument or "").strip().lower()
        modes = {"off", "3d", "8d", "wide", "binaural"}
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            current = queue.state.current
            if raw not in modes:
                active = (current.extra.get("spatial") if current else "") or "off"
                await message_obj.reply(
                    message("3D audio", "orb")
                    .key_value("Active", active, "orb")
                    .line(" · ".join(sorted(modes)), raw=True)
                    .line("Use <code>/3d 8d</code> before <code>/play</code>.", raw=True)
                    .build()
                )
                return
            if current is not None:
                current.extra["spatial"] = raw
                await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message("3D audio · " + raw, "orb")
            .line("Applied to the current track’s filter chain." if raw != "off" else "Spatial effect disabled.", raw=True)
            .build())
        # ``/mode3d`` is the documented alias of ``/3d``
        if message_obj.text and message_obj.text.lower().startswith("/mode3d"):
            return

    async def cmd_playlist(self, message_obj: Message, argument: str = "") -> None:
        manager = self.chart
        if manager is None:
            await self._send_error(message_obj, "Playlists unavailable", "Storage is not enabled for this scope.")
            return
        action, _, name = (argument or "").strip().partition(" ")
        action = action.lower()
        name = name.strip() or action if action not in {"list", "save", "load", "del"} else name.strip()
        if action in ("", "list"):
            names = manager.names()
            builder = message("Playlists", "folder")
            if not names:
                builder.line("No playlists yet — <code>/savequeue &lt;name&gt;</code>.", raw=True)
            for entry in names:
                playlist = manager.playlists[entry]
                builder.line(
                    f"<code>{esc(entry)}</code> · {len(playlist.tracks)} track(s)", raw=True
                )
            await message_obj.reply(builder.build())
            return
        if action == "save":
            await self.cmd_save_queue(message_obj, name)
            return
        if action in ("load", "play"):
            await self.cmd_load_queue(message_obj, name)
            return
        if action in ("del", "delete", "remove"):
            await self.cmd_del_queue(message_obj, name)
            return
        # ``/playlist <name>`` loads it
        await self.cmd_load_queue(message_obj, argument.strip())

    async def cmd_save_queue(self, message_obj: Message, argument: str = "") -> None:
        manager = self.chart
        name = (argument or "").strip().lower()
        if manager is None:
            await self._send_error(message_obj, "Playlists unavailable", "Storage is not enabled for this scope.")
            return
        if not name:
            await self._send_error(message_obj, "Usage", "/savequeue chill")
            return
        chat_id = message_obj.chat.id
        queue = await self.load_queue(chat_id)
        tracks = ([queue.current] if queue.current else []) + list(queue.state.items)
        if not tracks:
            await self._send_error(message_obj, "Nothing to save", "Queue something first.")
            return
        playlist = manager.put(
            Playlist(
                name=name,
                tracks=tracks,
                owner_id=message_obj.from_user.id if message_obj.from_user else 0,
                created_at=int(asyncio.get_event_loop().time()),
            )
        )
        await manager.save()
        await message_obj.reply(
            message("Playlist saved", "check")
            .key_value("Name", playlist.name, "folder")
            .key_value("Tracks", str(len(playlist.tracks)), "music")
            .build()
        )

    async def cmd_load_queue(self, message_obj: Message, argument: str = "") -> None:
        manager = self.chart
        name = (argument or "").strip().lower()
        if manager is None:
            await self._send_error(message_obj, "Playlists unavailable")
            return
        playlist = manager.get(name)
        if playlist is None or not playlist.tracks:
            await self._send_error(message_obj, "No such playlist", "/playlist to list them.")
            return
        chat_id = message_obj.chat.id
        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if queue.current is None:
                queue.start(playlist.tracks[0])
                rest = playlist.tracks[1:]
            else:
                rest = playlist.tracks
            for track in rest:
                if queue.enqueue(track) == 0:
                    break
            await self.save_queue(chat_id, queue)
        await message_obj.reply(
            message("Playlist loaded", "folder")
            .key_value("Name", playlist.name, "folder")
            .key_value("Queued", str(len(playlist.tracks)), "queue")
            .build(),
            reply_markup=player_keyboard(queue),
        )

    async def cmd_del_queue(self, message_obj: Message, argument: str = "") -> None:
        manager = self.chart
        if manager is None:
            await self._send_error(message_obj, "Playlists unavailable")
            return
        name = (argument or "").strip().lower()
        if not manager.delete(name):
            await self._send_error(message_obj, "No such playlist")
            return
        await manager.save()
        await message_obj.reply(message("Playlist deleted", "trash").line(f"<code>{esc(name)}</code>", raw=True).build())

    async def cmd_chart(self, message_obj: Message, argument: str = "") -> None:
        """Most-played tracks in this scope (a real chart, not a fake one)."""
        manager = self.chart
        chat_id = message_obj.chat.id
        queue = await self.load_queue(chat_id)
        universe = ([queue.current] if queue.current else []) + list(queue.state.history) + list(queue.state.items)
        tracks = manager.chart_tracks(universe) if manager is not None else []
        builder = message("Chart", "graph")
        if not tracks:
            builder.line("No plays recorded yet — play something first.", raw=True)
        for index, track in enumerate(tracks[:10], start=1):
            plays = track.extra.get("plays", 0)
            builder.line(f"<b>{index}.</b> <code>{esc(track.label)}</code> · {plays} play(s)", raw=True)
        await message_obj.reply(builder.build(), reply_markup=queue_keyboard(queue))

    async def cmd_now(self, message_obj: Message, argument: str = "") -> None:
        await self.cmd_playing(message_obj, argument)

    async def cmd_next(self, message_obj: Message, argument: str = "") -> None:
        chat_id = message_obj.chat.id
        queue = await self.load_queue(chat_id)
        upcoming = queue.upcoming(5)
        builder = message("Up next", "queue")
        if not upcoming:
            builder.line("Nothing queued.", raw=True)
        for index, track in enumerate(upcoming, start=1):
            builder.line(track_line(track, index), raw=True)
        if message_obj.reply_to_message is not None and _is_admin(message_obj):
            # ``/next`` as a reply moves the replied track to the front
            track = self._track_from_reply(message_obj)
            if track is not None:
                async with self.lock(chat_id):
                    queue.enqueue(track, play_next=True)
                    await self.save_queue(chat_id, queue)
                builder.line(f"<i>{esc(track.label)} moved to the front.</i>", raw=True)
        await message_obj.reply(builder.build(), reply_markup=queue_keyboard(queue))

    # -- callbacks --------------------------------------------------------
    async def on_callback(self, call: CallbackQuery) -> None:
        data = call.data or ""
        ctx = self.context
        if ctx is None or call.message is None:
            return
        chat_id = call.message.chat.id
        try:
            if data.startswith("q:close"):
                await call.message.delete()
                await call.answer()
                return
            if data.startswith("q:rm:"):
                index = int(data.rsplit(":", 1)[1])
                async with self.lock(chat_id):
                    queue = await self.load_queue(chat_id)
                    removed = queue.remove(index)
                    if removed is not None:
                        await self.save_queue(chat_id, queue)
                await call.answer(f"Removed {removed.label}" if removed else "No such track", show_alert=removed is None)
                if removed is not None:
                    await self._edit_card(call, queue_card(queue), queue_keyboard(queue))
                return
            if data.startswith("pb:qpage:"):
                page = int(data.rsplit(":", 1)[1])
                queue = await self.load_queue(chat_id)
                await self._edit_card(call, queue_card(queue, page=page), queue_keyboard(queue, page=page))
                await call.answer(f"Page {page}")
                return
            if data.startswith("pb:pick:"):
                index = int(data.rsplit(":", 1)[1])
                results = await self.state.get(f"results:{chat_id}", []) if ctx else []
                if index >= len(results):
                    await call.answer("Those results expired – search again.", show_alert=True)
                    return
                track = Track.from_dict(results[index])
                async with self.lock(chat_id):
                    queue = await self.load_queue(chat_id)
                    if queue.current is None:
                        queue.start(track)
                    else:
                        queue.enqueue(track)
                    await self.save_queue(chat_id, queue)
                    await self.state.delete(f"results:{chat_id}")
                await self._edit_card(call, play_card(queue, requested_by=track.requested_by), player_keyboard(queue))
                await call.answer(f"Queued {track.label}"[:180])
                return
        except Exception as exc:  # pragma: no cover - keyboard edge cases
            ctx.log("warning", f"vc_player callback {data!r} failed: {exc}")
            await call.answer("Something went wrong.", show_alert=True)
            return

        await self._handle_control(call, data)

    async def _handle_control(self, call: CallbackQuery, data: str) -> None:
        ctx = self.context
        assert ctx is not None
        chat_id = call.message.chat.id if call.message else 0
        action = data.partition(":")[2]
        notice = ""

        async with self.lock(chat_id):
            queue = await self.load_queue(chat_id)
            if action == "toggle":
                if queue.current is None:
                    notice = "Nothing is playing."
                else:
                    paused = queue.toggle()
                    notice = "Paused" if paused else "Resumed"
            elif action == "skip":
                if queue.current is None:
                    notice = "Nothing to skip."
                else:
                    skipped = queue.current.label
                    following = queue.next(autoplay_pool=await self._autoplay_pool(chat_id))
                    notice = f"Skipped {skipped}" if following else f"Queue finished after {skipped}"
            elif action == "end":
                removed = queue.clear()
                queue.state.current = None
                queue.state.paused = False
                queue.state.position = 0
                room = await self.load_room(chat_id)
                room["joined"] = False
                await self.save_room(chat_id, room)
                notice = f"Stopped · cleared {removed}"
            elif action in ("-10", "+10", "back", "fwd"):
                delta = -10 if action in ("-10", "back") else 10
                if queue.current is None:
                    notice = "Nothing is playing."
                else:
                    queue.jump(delta)
                    notice = f"Seek {clock(queue.state.position)}"
            elif action == "loop":
                count = 0 if queue.state.loop_count >= 5 else queue.state.loop_count + 1
                queue.set_loop(count)
                notice = f"Loop ×{count}" if count else "Loop off"
            elif action == "loopq":
                notice = "Queue loop " + ("on" if queue.set_loop_queue(not queue.state.loop_queue) else "off")
            elif action == "auto":
                notice = "Autoplay " + ("on" if queue.set_autoplay(not queue.state.autoplay) else "off")
            elif action == "mode":
                order = ["audio", "video", "both"]
                mode = order[(order.index(queue.state.mode) + 1) % len(order)]
                queue.set_mode(mode)
                notice = f"Mode · {mode}"
            elif action == "shuffle":
                notice = f"Shuffled {queue.shuffle()}"
            elif action == "prev":
                previous = queue.previous()
                notice = f"Previous · {previous.label}" if previous else "No previous track."
            elif action == "queue":
                await call.answer()
                if call.message is not None:
                    await call.message.edit_text(
                        queue_card(queue), reply_markup=queue_keyboard(queue), disable_web_page_preview=True
                    )
                return
            elif action in ("cancel", "dismiss"):
                await call.message.delete() if call.message is not None else None
                await call.answer()
                return
            elif action in ("download", "replay"):
                track = queue.current
                if track is None:
                    notice = "Nothing is playing."
                else:
                    await self.save_queue(chat_id, queue)
                    if call.message is not None:
                        preview = (
                            message("Replay", "refresh").line(f"<code>{esc(track.label)}</code>", raw=True).build()
                            if action == "replay"
                            else message("Download", "download")
                            .line(f"<code>{esc(track.label)}</code>", raw=True)
                            .line("<i>Send it again with /play as a reply, or use /fsave.</i>", raw=True)
                            .build()
                        )
                        await call.message.answer(preview)
                    notice = ""
            else:
                notice = "Unknown control."
            await self.save_queue(chat_id, queue)

        await call.answer(notice[:190] if notice else None)
        if call.message is not None and queue.current is not None:
            body = play_card(queue, requested_by=queue.current.requested_by)
            if not vc_available():
                body += "\n<i>simulation – no voice-chat backend installed</i>"
            await self._edit_card(call, body, player_keyboard(queue))

    async def _edit_card(self, call: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
        if call.message is None:
            return
        try:
            await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:  # pragma: no cover - "message is not modified"
            pass

    # -- helpers ----------------------------------------------------------
    async def _autoplay_pool(self, chat_id: int) -> list[Track]:
        resolver = self._resolver
        catalogue = getattr(resolver, "catalogue", None)
        if catalogue is None or self.context is None:
            return []
        queue = await self.load_queue(chat_id)
        seen = {t.source for t in queue.state.items} | {t.source for t in queue.state.history}
        if queue.current is not None:
            seen.add(queue.current.source)
        try:
            hits = await catalogue.search("", 30)
        except Exception:  # pragma: no cover - catalogue failures
            return []
        pool = [track for track in hits if track.source not in seen]
        random.shuffle(pool)
        return pool[:10]

    def _display(self, message_obj: Message) -> str:
        user = message_obj.from_user
        if user is None:
            return ""
        return user_mention(user.id, user.full_name)

    def _queue_button(self) -> InlineKeyboardMarkup:
        return keyboard([[Btn("Open queue", callback_data="pb:queue", emoji="queue")]])


class _Catalogue:
    """Adapter from ``music.MusicLibrary`` to the resolver's search protocol."""

    def __init__(self, ctx: PluginContext, library: Any) -> None:
        self.ctx_ref = ctx  # named so it does not shadow PlayerMixin.ctx
        self.library = library
        self.catalogue = True  # marker used by autoplay pool discovery

    async def search(self, query: str, limit: int) -> list[Track]:
        from .sources import CatalogueBackend

        backend = CatalogueBackend(self.library)
        ctx = self.ctx_ref
        hits = await backend.search(query, limit)
        for track in hits:
            track.source = track.source or track.title
        # keep the adapter usable by autoplay even with an empty query
        if not hits and not query:
            return []
        ctx.log("debug", f"vc_player catalogue hits={len(hits)} q={query!r}")
        return hits


__all__ = ["OWN_COMMAND", "PLAYER_COMMAND_NAMES", "PlayerMixin", "parse_seek"]
