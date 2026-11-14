"""``encoder`` handlers – the ENCODING-BOT surface on aiogram.

Commands::

    /encode [-f]        reply to a video/audio file with an encode profile
    /fastencode         quick x264 CRF 28 / veryfast pass
    /encodehelp         the settings panel + which ffmpeg pieces are installed
    /encpreset <name>   ultramax…veryslow
    /enccrf <0-51>
    /enccodec x264|x265|copy
    /encaudio <codec> [bitrate] [mono|stereo]
    /encsubs copy|burn|none|extract
    /encresolution 720p|1080p|original
    /encwatermark <text>|off
    /encthumb          reply to a photo → thumbnail for the next upload
    /encinfo           reply to a file → streams, codecs, channels (ffprobe)
    /enccancel         stop the running job in this chat
    /encstats          jobs, failures and settings for this scope
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.modules import toolchain
from meiko.plugins.support import OwnCommand, Workspace, human_size
from meiko.ui import Btn, esc, keyboard, message

from .manifest import MANIFEST
from .plans import (
    AUDIO_CODECS,
    CODECS,
    CONTAINERS,
    PRESETS,
    RESOLUTIONS,
    SUBTITLE_MODES,
    EncodeSettings,
    build_command,
    duration_of,
    output_name,
    parse_audio_streams,
)
from .runner import FFmpegMissing, encode, missing_tools, probe, tools_available

OWN_COMMAND = OwnCommand(MANIFEST.commands)


def _tool_label(state: dict[str, Any], name: str) -> str:
    """``ready · path`` / ``ready · bundled`` / ``missing`` — never a bare bool."""
    if not state.get(name):
        return "missing"
    source = (state.get("sources") or {}).get(name, "")
    return f"ready · {source}" if source else "ready"
MB = 1024 * 1024
PROGRESS_KEY = "progress:{chat}:{job}"


def _bar(percent: float, width: int = 10) -> str:
    filled = int(round(min(100.0, max(0.0, percent)) / 100 * width))
    return "▰" * filled + "▱" * (width - filled)


@plugin
class EncoderPlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._workspace: Workspace | None = None
        self._jobs: dict[int, asyncio.Task[Any]] = {}
        self._cancelled: set[int] = set()
        self._ledger_ids: dict[int, str] = {}
        self._ledger: Any = None

    # -- dashboard integration ---------------------------------------------
    def job_ledger(self) -> Any:
        """The shared ledger the Encoding Studio reads (one source of truth)."""
        if self._ledger is None:
            from meiko.modules.ledger import JobLedger
            from meiko.modules.store import get_module_store

            self._ledger = JobLedger(get_module_store(), MANIFEST.slug, limit=4)
        return self._ledger

    def _scope_key(self) -> str:
        return self.context.scope.key if self.context is not None else "global"

    async def reload_settings(self) -> None:
        """Re-read state other surfaces wrote (used by the webapp settings forms)."""
        return None

    async def cancel_remote_job(self, *, job_id: str, scope: str) -> bool:
        """Called by the API when the dashboard cancels an encode."""
        if scope != self._scope_key():
            return False
        chat_id = next((chat for chat, ident in self._ledger_ids.items() if ident == job_id), None)
        if chat_id is None:
            return False
        task = self._jobs.get(chat_id)
        if task is None or task.done():
            return False
        self._cancelled.add(chat_id)
        task.cancel()
        return True

    async def on_start(self, ctx: PluginContext) -> None:
        self._workspace = Workspace(
            MANIFEST.slug,
            scope=ctx.scope.key,
            quota_bytes=3 * 1024 * MB,
            max_file_bytes=int(self.config("max_file_mb", 1900)) * MB,
            ttl_seconds=6 * 3600,
        )
        router = Router(name="plugin:encoder")
        router.message.register(self.on_command, F.text.startswith("/"), OWN_COMMAND)
        router.callback_query.register(self.on_callback, F.data.startswith("enc:"))
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        for task in list(self._jobs.values()):
            if not task.done():
                task.cancel()
        self._jobs.clear()
        self._workspace = None

    # -- profile -----------------------------------------------------------
    async def profile(self) -> EncodeSettings:
        saved = await self.context.get_state("profile", None) if self.context else None
        if isinstance(saved, dict):
            profile = EncodeSettings.from_dict(saved)
        else:
            profile = EncodeSettings(
                codec=str(self.config("default_codec", "x265")),
                crf=int(self.config("default_crf", 26)),
                preset=str(self.config("default_preset", "superfast")),
                container=str(self.config("default_container", "mkv")),
            )
        profile.watermark_position = str(self.config("watermark_position", "top-left"))
        return profile

    async def save_profile(self, profile: EncodeSettings) -> None:
        if self.context is not None:
            await self.context.set_state("profile", profile.to_dict())

    # -- dispatch ----------------------------------------------------------
    async def on_command(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        routes = {
            "/encode": self.cmd_encode,
            "/fastencode": self.cmd_fast,
            "/encodehelp": self.cmd_help,
            "/encpreset": self.cmd_preset,
            "/enccrf": self.cmd_crf,
            "/enccodec": self.cmd_codec,
            "/encaudio": self.cmd_audio,
            "/encsubs": self.cmd_subs,
            "/encresolution": self.cmd_resolution,
            "/encwatermark": self.cmd_watermark,
            "/encthumb": self.cmd_thumb,
            "/encinfo": self.cmd_info,
            "/enccancel": self.cmd_cancel,
            "/encstats": self.cmd_stats,
        }
        handler = routes.get(command)
        if handler is None:
            return
        if command in {"/encode", "/fastencode", "/encpreset", "/enccrf", "/enccodec",
                       "/encaudio", "/encsubs", "/encresolution", "/encwatermark"}:
            await handler(message_obj, argument)
        else:
            await handler(message_obj)

    # -- info --------------------------------------------------------------
    async def cmd_help(self, message_obj: Message, _argument: str = "") -> None:
        profile = await self.profile()
        state = toolchain.describe()
        ready = tools_available()
        builder = message("Encoder", "video" if ready else "warn")
        builder.key_value("FFmpeg", _tool_label(state, "ffmpeg"), "check" if state["ffmpeg"] else "cross")
        builder.key_value("ffprobe", _tool_label(state, "ffprobe"), "check" if state["ffprobe"] else "cross")
        if not ready:
            builder.line("Missing: " + ", ".join(esc(t) for t in missing_tools()), raw=True)
            if state["probe_fallback"]:
                builder.line(
                    "<i>ffprobe is absent but ffmpeg is here, so metadata is read from its banner and "
                    "encoding works normally.</i>",
                    raw=True,
                )
            else:
                builder.line("<i>Profiles are still editable and commands still plan; "
                             "jobs will refuse to start until FFmpeg is present.</i>", raw=True)
        builder.blank()
        builder.key_value("Codec", profile.codec, "video")
        builder.key_value("CRF / preset", f"{profile.crf} / {profile.preset}", "gear")
        builder.key_value("Container", profile.container, "file")
        builder.key_value("Audio", f"{profile.audio_codec} {profile.audio_bitrate}", "music")
        builder.key_value("Subtitles", profile.subtitles, "type")
        builder.key_value("Resolution", profile.resolution, "photo")
        if profile.watermark:
            builder.key_value("Watermark", profile.watermark, "id")
        await message_obj.reply(builder.build(), reply_markup=self._panel())

    def _panel(self):
        return keyboard(
            [
                [Btn("Codec", callback_data="enc:codec", emoji="video"),
                 Btn("Preset", callback_data="enc:preset", emoji="gear"),
                 Btn("CRF", callback_data="enc:crf", emoji="stats")],
                [Btn("Subtitles", callback_data="enc:subs", emoji="type"),
                 Btn("Audio", callback_data="enc:audio", emoji="music"),
                 Btn("Resolution", callback_data="enc:resolution", emoji="photo")],
                [Btn("Preview command", callback_data="enc:preview", emoji="code"),
                 Btn("Reset", callback_data="enc:reset", emoji="refresh")],
            ]
        )

    # -- settings commands -------------------------------------------------
    async def _update(self, message_obj: Message, **changes: Any) -> None:
        profile = await self.profile()
        for key, value in changes.items():
            setattr(profile, key, value)
        problems = profile.validate()
        if problems:
            await message_obj.reply(
                message("Not applied", "warn").line("; ".join(esc(p) for p in problems), raw=True).build()
            )
            return
        await self.save_profile(profile)
        await message_obj.reply(
            message("Profile updated", "check")
            .key_value("Codec", profile.codec, "video")
            .key_value("CRF / preset", f"{profile.crf} / {profile.preset}", "gear")
            .key_value("Container", profile.container, "file")
            .build(),
            reply_markup=self._panel(),
        )

    async def cmd_preset(self, message_obj: Message, argument: str) -> None:
        preset = argument.strip().lower()
        if preset not in PRESETS:
            await message_obj.reply(
                message("Presets", "gear").line(" · ".join(PRESETS), raw=True).build()
            )
            return
        await self._update(message_obj, preset=preset)

    async def cmd_crf(self, message_obj: Message, argument: str) -> None:
        if not argument.strip().isdigit():
            await message_obj.reply("Usage: <code>/enccrf 24</code> (lower = better quality)")
            return
        await self._update(message_obj, crf=max(0, min(51, int(argument.strip()))))

    async def cmd_codec(self, message_obj: Message, argument: str) -> None:
        codec = argument.strip().lower()
        if codec not in CODECS:
            await message_obj.reply("Usage: <code>/enccodec x264|x265|copy</code>")
            return
        await self._update(message_obj, codec=codec)

    async def cmd_audio(self, message_obj: Message, argument: str) -> None:
        parts = argument.split()
        if not parts or parts[0].lower() not in AUDIO_CODECS:
            await message_obj.reply(
                message("Audio", "music")
                .line(" · ".join(AUDIO_CODECS), raw=True)
                .line("Usage: <code>/encaudio aac 192k stereo</code>", raw=True)
                .build()
            )
            return
        changes: dict[str, Any] = {"audio_codec": parts[0].lower()}
        if len(parts) > 1 and (parts[1].endswith("k") or parts[1].isdigit()):
            changes["audio_bitrate"] = parts[1] if parts[1].endswith("k") else f"{parts[1]}k"
        if len(parts) > 2 and parts[2].lower() in {"mono", "stereo", "original"}:
            changes["audio_channels"] = parts[2].lower()
        await self._update(message_obj, **changes)

    async def cmd_subs(self, message_obj: Message, argument: str) -> None:
        mode = argument.strip().lower()
        if mode not in SUBTITLE_MODES:
            await message_obj.reply("Usage: <code>/encsubs copy|burn|none|extract</code>")
            return
        await self._update(message_obj, subtitles=mode)

    async def cmd_resolution(self, message_obj: Message, argument: str) -> None:
        value = argument.strip().lower()
        if value not in RESOLUTIONS and value != "original":
            await message_obj.reply("Usage: <code>/encresolution 720p</code> · " + " · ".join(RESOLUTIONS))
            return
        await self._update(message_obj, resolution=value)

    async def cmd_watermark(self, message_obj: Message, argument: str) -> None:
        text = argument.strip()
        if not text or text.lower() in {"off", "none", "clear"}:
            await self._update(message_obj, watermark="")
            return
        await self._update(message_obj, watermark=text[:40])

    async def cmd_thumb(self, message_obj: Message) -> None:
        target = message_obj.reply_to_message or message_obj
        photo = getattr(target, "photo", None) or getattr(target, "document", None)
        if photo is None:
            await message_obj.reply("Reply to a photo with <code>/encthumb</code>.")
            return
        file_id = photo.file_id if hasattr(photo, "file_id") else photo[-1].file_id
        if self.context is not None:
            await self.context.set_state("thumbnail", file_id)
        await message_obj.reply(message("Thumbnail set", "photo").build())

    # -- probing -----------------------------------------------------------
    async def cmd_info(self, message_obj: Message) -> None:
        target = message_obj.reply_to_message or message_obj
        document = getattr(target, "video", None) or getattr(target, "document", None) or getattr(target, "audio", None)
        if document is None:
            await message_obj.reply("Reply to a video/document with <code>/encinfo</code>.")
            return
        if not tools_available() and not toolchain.describe()["probe_fallback"]:
            await message_obj.reply(
                message("ffprobe missing", "warn")
                .line("Install <code>ffmpeg</code> (it ships ffprobe) to inspect files.", raw=True)
                .line("<code>pip install imageio-ffmpeg</code> provides ffmpeg only; metadata is then read from "
                       "its banner.", raw=True)
                .build()
            )
            return
        await message_obj.reply(message("Probing…", "search").build())
        path = await self._download(message_obj, document)
        banner = not toolchain.has("ffprobe")
        try:
            on_disk = Path(path).stat().st_size
        except OSError:  # pragma: no cover - download already gone
            on_disk = 0
        try:
            data = await probe(path)
        finally:
            Path(path).unlink(missing_ok=True)
        streams = data.get("streams") or []
        builder = message("Media info", "video")
        for stream in streams[:8]:
            kind = stream.get("codec_type", "?")
            builder.line(
                f"#{stream.get('index')} <b>{esc(kind)}</b> · {esc(stream.get('codec_name', '?'))} "
                f"· {stream.get('width', '')}x{stream.get('height', '')}"
                f"{' · ' + str(stream.get('channels')) + 'ch' if stream.get('channels') else ''}",
                raw=True,
            )
        audio = parse_audio_streams(data)
        if audio:
            builder.blank().line(f"Audio tracks: {len(audio)}", raw=True)
        builder.key_value("Duration", f"{duration_of(data) // 60}:{duration_of(data) % 60:02d}", "clock")
        reported = int((data.get("format") or {}).get("size", 0) or 0)
        builder.key_value("Size", human_size(reported or on_disk), "file")
        if banner:
            builder.line("<i>Read from the ffmpeg banner (ffprobe is not installed).</i>", raw=True)
        await message_obj.reply(builder.build())

    # -- encoding ----------------------------------------------------------
    async def cmd_fast(self, message_obj: Message, _argument: str = "") -> None:
        profile = await self.profile()
        quick = EncodeSettings.from_dict(profile.to_dict())
        quick.codec, quick.crf, quick.preset, quick.container = "x264", 28, "veryfast", "mp4"
        await self._start_job(message_obj, quick)

    async def cmd_encode(self, message_obj: Message, argument: str = "") -> None:
        profile = await self.profile()
        if argument:
            # inline overrides, so one-off encodes do not need the panel
            parts = argument.lower().split()
            if "x265" in parts or "x264" in parts:
                profile.codec = "x265" if "x265" in parts else "x264"
            for part in parts:
                if part.isdigit() and 0 <= int(part) <= 51:
                    profile.crf = int(part)
                elif part in PRESETS:
                    profile.preset = part
                elif part in RESOLUTIONS:
                    profile.resolution = part
                elif part in CONTAINERS:
                    profile.container = part
        await self._start_job(message_obj, profile)

    async def _start_job(self, message_obj: Message, profile: EncodeSettings) -> None:
        ctx = self.context
        assert ctx is not None
        chat_id = message_obj.chat.id
        target = message_obj.reply_to_message or message_obj
        document = (
            getattr(target, "video", None)
            or getattr(target, "document", None)
            or getattr(target, "audio", None)
        )
        if document is None:
            await message_obj.reply(
                message("Reply to a file", "warn")
                .line("Usage: reply to a video with <code>/encode</code>.", raw=True)
                .build()
            )
            return
        if chat_id in self._jobs and not self._jobs[chat_id].done():
            await message_obj.reply(message("A job is already running here", "warn").line("/enccancel to stop it.", raw=True).build())
            return
        problems = profile.validate()
        if problems:
            await message_obj.reply(
                message("Profile is not runnable", "warn").line("; ".join(esc(p) for p in problems), raw=True).build()
            )
            return
        if not toolchain.has("ffmpeg"):
            planned = " ".join(build_command("<input>", "<output>", profile))
            await message_obj.reply(
                message("FFmpeg missing", "warn")
                .line("Missing: " + ", ".join(esc(t) for t in missing_tools()), raw=True)
                .line("<i>The planned command is below so you can run it on a worker with FFmpeg.</i>", raw=True)
                .blank()
                .code(planned)
                .build()
            )
            return
        await self._run_job(message_obj, document, profile)

    async def _download(self, message_obj: Message, document: Any) -> str:
        ctx = self.context
        assert ctx is not None and self._workspace is not None
        name = getattr(document, "file_name", "") or f"input_{getattr(document, 'file_unique_id', 'x')}.bin"
        target = self._workspace.path_for(name)
        await ctx.bot.download(document, destination=target)
        return str(target)

    async def _run_job(self, message_obj: Message, document: Any, profile: EncodeSettings) -> None:
        ctx = self.context
        assert ctx is not None and self._workspace is not None
        chat_id = message_obj.chat.id
        self._cancelled.discard(chat_id)
        ledger_id = ""
        try:
            entry = self.job_ledger().create(
                scope=self._scope_key(),
                kind="encode",
                owner_id=int(getattr(getattr(message_obj.from_user, "id", 0), "real", 0) or 0),
                meta={
                    "chat_id": chat_id,
                    "file": getattr(document, "file_name", "") or "input",
                    "codec": profile.codec,
                    "crf": profile.crf,
                },
            )
            ledger_id = entry.id
            self._ledger_ids[chat_id] = ledger_id
        except Exception:  # pragma: no cover - the ledger is a record, never a gate
            ledger_id = ""
        progress = await message_obj.reply(
            message("Starting encode", "video")
            .key_value("Codec", profile.codec, "video")
            .key_value("CRF / preset", f"{profile.crf} / {profile.preset}", "gear")
            .build()
        )
        try:
            source = await self._download(message_obj, document)
        except Exception as exc:
            await self.edit_text(progress, message("Download failed", "warn").line(esc(str(exc)), raw=True).build())
            return

        meta = await probe(source)
        duration = duration_of(meta)
        from .plans import has_subtitle_stream

        output = str(Path(source).with_name(output_name(Path(source).name, profile)))
        interval = float(self.config("progress_interval", 4.0))
        last: dict[str, Any] = {"percent": 0.0}

        async def on_progress(stats: dict[str, Any]) -> None:
            last.update(stats)
            if ledger_id:
                self.job_ledger().update(
                    self._scope_key(),
                    ledger_id,
                    progress=float(stats.get("percent", 0) or 0),
                    state="running",
                    detail=f"speed {stats.get('speed') or '—'} · fps {stats.get('fps') or '—'}",
                )
            await self.edit_text(
                progress,
                message("Encoding…", "loading")
                .line(f"{_bar(stats.get('percent', 0))} <b>{stats.get('percent', 0)}%</b>", raw=True)
                .line(f"speed {esc(stats.get('speed') or '—')} · fps {esc(stats.get('fps') or '—')}", raw=True)
                .build(),
            )

        task = asyncio.ensure_future(
            encode(
                source,
                output,
                profile,
                duration=duration,
                has_subtitles=has_subtitle_stream(meta),
                has_audio=bool(parse_audio_streams(meta)),
                on_progress=on_progress,
                progress_interval=interval,
            )
        )
        self._jobs[chat_id] = task
        try:
            result = await task
        except asyncio.CancelledError:
            await self.edit_text(progress, message("Cancelled", "cross").build())
            Path(source).unlink(missing_ok=True)
            if ledger_id:
                self.job_ledger().finish(self._scope_key(), ledger_id, state="cancelled")
            return
        except FFmpegMissing as exc:
            await self.edit_text(progress, message("FFmpeg missing", "warn").line(esc(str(exc)), raw=True).build())
            return
        except Exception as exc:  # pragma: no cover - unexpected process errors
            await self.edit_text(progress, message("Encode failed", "warn").line(esc(str(exc)), raw=True).build())
            return
        finally:
            self._jobs.pop(chat_id, None)
            self._ledger_ids.pop(chat_id, None)

        if not result.ok or not Path(output).exists():
            if ledger_id:
                self.job_ledger().finish(
                    self._scope_key(),
                    ledger_id,
                    state="failed",
                    error=f"ffmpeg exited {result.returncode}",
                    detail=(result.log_tail or "")[-400:],
                )
            await self.edit_text(
                progress,
                message("Encode failed", "warn")
                .line(f"exit code {result.returncode}", raw=True)
                .code(result.log_tail[-1200:] or "no output")
                .build(),
            )
            return

        size = Path(output).stat().st_size
        await self.edit_text(
            progress,
            message("Encode finished", "check")
            .key_value("Output", Path(output).name, "file")
            .key_value("Size", human_size(size), "database")
            .key_value("Codec", profile.codec, "video")
            .build(),
        )
        if ledger_id:
            # the studio shows the *validated* output, not the reported 100%
            self.job_ledger().finish(
                self._scope_key(),
                ledger_id,
                state="done",
                detail=f"{Path(output).name} · {human_size(size)}",
            )
        thumb = await ctx.get_state("thumbnail", None) if ctx else None
        try:
            await message_obj.reply_document(
                FSInputFile(output),
                caption=f"<code>{esc(Path(output).name)}</code> · {human_size(size)}",
                thumb=thumb or None,
            )
            if bool(self.config("keep_original", True)):
                with contextlib.suppress(Exception):
                    await message_obj.reply_document(FSInputFile(source), caption="original")
        finally:
            Path(source).unlink(missing_ok=True)
            Path(output).unlink(missing_ok=True)

    async def cmd_cancel(self, message_obj: Message) -> None:
        task = self._jobs.get(message_obj.chat.id)
        if task is None or task.done():
            await message_obj.reply(message("Nothing is running", "sleep").build())
            return
        task.cancel()
        await message_obj.reply(message("Cancelling…", "cross").build())

    async def cmd_stats(self, message_obj: Message) -> None:
        profile = await self.profile()
        running = sum(1 for task in self._jobs.values() if not task.done())
        await message_obj.reply(
            message("Encoder stats", "stats")
            .key_value("FFmpeg", _tool_label(toolchain.describe(), "ffmpeg"), "check" if toolchain.has("ffmpeg") else "cross")
            .key_value("Running jobs", str(running), "loading")
            .key_value("Profile", f"{profile.codec} crf{profile.crf} {profile.preset}", "gear")
            .build()
        )

    # -- callbacks ---------------------------------------------------------
    async def on_callback(self, call: CallbackQuery) -> None:
        ctx = self.context
        if ctx is None or call.message is None:
            return
        action = (call.data or "").partition(":")[2]
        profile = await self.profile()
        mapping = {
            "codec": lambda: setattr(profile, "codec", "x264" if profile.codec == "x265" else "x265"),
            "preset": lambda: setattr(profile, "preset", list(PRESETS)[(list(PRESETS).index(profile.preset) + 1) % len(PRESETS)]),
            "crf": lambda: setattr(profile, "crf", 22 if profile.crf >= 30 else profile.crf + 2),
            "subs": lambda: setattr(profile, "subtitles", ["copy", "burn", "none", "extract"][(["copy", "burn", "none", "extract"].index(profile.subtitles) + 1) % 4]),
            "audio": lambda: setattr(profile, "audio_codec", ["aac", "opus", "mp3", "copy"][(["aac", "opus", "mp3", "copy"].index(profile.audio_codec) + 1) % 4]),
            "resolution": lambda: setattr(profile, "resolution", ["original", "720p", "1080p", "480p"][(["original", "720p", "1080p", "480p"].index(profile.resolution) + 1) % 4]),
            "reset": lambda: None,
        }
        if action == "preview":
            await call.answer()
            await call.message.reply(
                message("Planned command", "code")
                .code(" ".join(build_command("<input>", "<output>", profile)))
                .build()
            )
            return
        handler = mapping.get(action)
        if handler is None:
            await call.answer("Unknown control.")
            return
        handler()
        await self.save_profile(profile)
        await call.answer("Updated")
        with contextlib.suppress(Exception):
            await call.message.edit_text(
                message("Encoder", "video")
                .key_value("Codec", profile.codec, "video")
                .key_value("CRF / preset", f"{profile.crf} / {profile.preset}", "gear")
                .key_value("Subtitles", profile.subtitles, "type")
                .key_value("Audio", profile.audio_codec, "music")
                .key_value("Resolution", profile.resolution, "photo")
                .build(),
                reply_markup=self._panel(),
            )


__all__ = ["EncoderPlugin", "OWN_COMMAND"]
