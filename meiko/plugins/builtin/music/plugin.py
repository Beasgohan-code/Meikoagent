"""``music`` handlers: catalogue search, ID3 tagging and cover art."""

from __future__ import annotations

import re
from pathlib import Path

from aiogram import F, Router
from aiogram.types import BufferedInputFile, FSInputFile, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import (
    OwnCommand,
    PluginState,
    Workspace,
    human_size,
    safe_filename,
)
from meiko.ui import Btn, esc, keyboard, message

from .id3 import (
    TrackTags,
    is_audio,
    read_tags,
    read_tag_file,
    supported_format,
    write_tag,
    write_tag_file,
)
from .library import (
    LibraryEntry,
    MusicLibrary,
    guess_tags_from_filename,
    placeholder_cover,
    render_cover_text,
)
from .manifest import MANIFEST
from .player import PLAYER_COMMAND_NAMES, PlayerMixin
from .sources import PlaylistManager

INDEX_KEY = "index:tracks"


def _looks_like_id3(head: bytes) -> bool:
    """``ID3`` magic — an MP3 that merely happens to live in an .m4a name."""
    return head[:3] == b"ID3"

MB = 1024 * 1024
USAGE = (
    "<b>Music · VC player</b>\n"
    "<code>/play &lt;song|link&gt; [-f]</code> – queue music\n"
    "<code>/vplay &lt;name&gt; [-hd|-fhd|-4k]</code> – queue video (alias /stream)\n"
    "<code>/pause /resume /skip /prev /end</code> – transport\n"
    "<code>/seek 1:30 /fw /bw</code> – scrub\n"
    "<code>/loop 3 /loopq /autoplay</code> – repeat\n"
    "<code>/queue /now /next /chart</code> – what is playing\n"
    "<code>/savequeue chill /loadqueue chill /playlist</code> – playlists\n"
    "<code>/style bassboost /mode /speed 1.25 /volume 80 /3d 8d</code> – sound\n"
    "<code>/join /leave /room /vc /auth</code> – voice chat\n"
    "\n<b>Catalogue</b>\n"
    "<code>/music &lt;query&gt;</code> · <code>/addmusic</code> · <code>/tag</code> · "
    "<code>/magic</code> · <code>/cover</code> · <code>/musicstats</code>"
)


#: Toolkit commands handled here; everything else is the player's.
TOOLKIT_COMMANDS = {
    "music", "musicsearch", "addmusic", "tag", "magic", "cover",
    "musicstats", "musictags", "delmusic",
}
PLAYER_ONLY = set(PLAYER_COMMAND_NAMES)

#: Only dispatch commands this package declares – never the whole "/" space.
OWN_COMMAND = OwnCommand(MANIFEST.commands)

@plugin
class MusicPlugin(PlayerMixin, MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._library = MusicLibrary()
        self._workspace: Workspace | None = None
        self._state: PluginState | None = None

    async def on_start(self, ctx: PluginContext) -> None:
        self._state = PluginState(ctx)
        self._library = MusicLibrary(
            await self._state.get(INDEX_KEY), limit=int(self.config("index_limit", 3000))
        )
        self._workspace = Workspace(
            MANIFEST.slug,
            scope=ctx.scope.key,
            quota_bytes=512 * MB,
            max_file_bytes=int(self.config("max_file_mb", 20)) * MB,
            ttl_seconds=1800,
        )
        # playback half of the package (queue, resolvers, cards, callbacks)
        await self.start_player(ctx)
        self._chart = await PlaylistManager(self.state).load()
        router = Router(name="plugin:music")
        router.message.register(self.on_audio, F.audio)
        router.message.register(self.on_command_message, F.text.startswith("/"), OWN_COMMAND)
        self.register_player_routes(router)
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        await self.stop_player()
        self._chart = None
        self._workspace = None

    async def persist(self) -> None:
        ctx = self.context
        if ctx is not None:
            state = self._state or PluginState(ctx)
            await state.set(INDEX_KEY, self._library.to_list())

    # -- routing ---------------------------------------------------------
    async def on_command_message(self, message_obj: Message, **_: object) -> None:
        """Single entry point: toolkit commands first, then the player."""
        command = (message_obj.text or "").split(" ")[0].split("@")[0].lower()
        if command.startswith("/") and command[1:] in PLAYER_ONLY:
            await self.on_player_command(message_obj)
            return
        await self.on_command(message_obj)

    async def on_audio(self, message_obj: Message, **_: object) -> None:
        if message_obj.audio and bool(self.config("auto_tag", True)):
            await self.index_audio(message_obj, message_obj.audio)

    async def on_command(self, message_obj: Message) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        routes = {
            "/music": self.cmd_search,
            "/musicsearch": self.cmd_search,
            "/addmusic": self.cmd_add,
            "/tag": self.cmd_tag,
            "/magic": self.cmd_magic,
            "/cover": self.cmd_cover,
            "/musicstats": self.cmd_stats,
            "/musictags": self.cmd_tags,
            "/delmusic": self.cmd_delete,
        }
        handler = routes.get(command)
        if handler is None:
            return
        if command in {"/music", "/musicsearch", "/tag", "/delmusic"}:
            await handler(message_obj, argument)
        else:
            await handler(message_obj)

    # -- catalogue -------------------------------------------------------
    async def index_audio(self, message_obj: Message, audio: object) -> LibraryEntry | None:
        filename = getattr(audio, "file_name", "") or f"audio_{getattr(audio, 'file_unique_id', 'x')}.mp3"
        if not is_audio(filename):
            return None
        tags = guess_tags_from_filename(filename)
        entry = LibraryEntry.from_track(
            filename,
            tags,
            size=getattr(audio, "file_size", 0) or 0,
            message_id=message_obj.message_id,
            chat=str(message_obj.chat.id),
            file_id=getattr(audio, "file_id", "") or "",
        )
        if self._library.add(entry):
            await self.persist()
            return entry
        return None

    async def cmd_add(self, message_obj: Message) -> None:
        target = message_obj.reply_to_message
        audio = getattr(target, "audio", None) or getattr(target, "document", None)
        if audio is None:
            await message_obj.reply("Reply to an audio file with <code>/addmusic</code>.")
            return
        entry = await self.index_audio(target, audio)
        if entry is None:
            await message_obj.reply("That file is already indexed or is not audio.")
            return
        await message_obj.reply(
            message("Indexed", "music")
            .key_value("Title", entry.title, "music")
            .key_value("Artist", entry.artist or "unknown", "user")
            .key_value("Catalogue", f"{len(self._library)} track(s)", "database")
            .build()
        )

    async def cmd_search(self, message_obj: Message, argument: str) -> None:
        if not argument:
            await message_obj.reply(USAGE)
            return
        limit = int(self.config("results", 8))
        results = self._library.search(argument, limit=limit)
        if not results:
            await message_obj.reply(
                message("No matches", "search")
                .line(f"Nothing matched <code>{esc(argument)}</code>.", raw=True)
                .line("Index tracks with <code>/addmusic</code>.", raw=True)
                .build()
            )
            return
        builder = message(f"Results for “{argument}”", "music")
        for entry, score in results:
            builder.bullet(
                f"<code>{esc(entry.filename)}</code> · {human_size(entry.size)} · {int(score * 100)}%"
            )
        await message_obj.reply(builder.build())

    async def cmd_stats(self, message_obj: Message) -> None:
        stats = self._library.stats()
        await message_obj.reply(
            message("Music catalogue", "music")
            .key_value("Tracks", str(stats["tracks"]), "music")
            .key_value("Artists", str(stats["artists"]), "user")
            .key_value("Albums", str(stats["albums"]), "folder")
            .key_value("Volume", human_size(int(stats["bytes"])), "database")
            .blank()
            .line("Want to hear it in voice chat? Enable <b>VC Player</b> and use "
                  "<code>/play &lt;song&gt;</code>.", raw=True)
            .build()
        )

    async def cmd_delete(self, message_obj: Message, argument: str) -> None:
        if not argument:
            await message_obj.reply("Usage: <code>/delmusic &lt;filename&gt;</code>")
            return
        if self._library.remove(argument.strip()):
            await self.persist()
            await message_obj.reply(f"Removed <code>{esc(argument.strip())}</code> from the catalogue.")
        else:
            await message_obj.reply("No such file in the catalogue.")

    async def cmd_tags(self, message_obj: Message, argument: str = "") -> None:
        target = message_obj.reply_to_message
        document = getattr(target, "audio", None) or getattr(target, "document", None)
        if document is None:
            await message_obj.reply("Reply to an audio file with <code>/musictags</code>.")
            return
        ctx = self.context
        assert ctx is not None
        try:
            buffer = await ctx.bot.download(document, destination=None)
            head = buffer.read(256 * 1024) if hasattr(buffer, "read") else b""
        except Exception as exc:  # pragma: no cover - telegram failures
            await message_obj.reply(f"Download failed: {esc(str(exc))}")
            return
        filename = getattr(document, "file_name", "") or "track.mp3"
        if supported_format(filename) == "mutagen" and not _looks_like_id3(head):
            # MP4/FLAC/OGG carry their own tag structures; the in-tree ID3 reader
            # cannot parse those, so a whole-file read through mutagen is used.
            tags = await self._read_tags_via_mutagen(document, filename) or read_tags(head)
        else:
            tags = read_tags(head)
        if tags.empty:
            tags = guess_tags_from_filename(getattr(document, "file_name", "") or "")
        builder = message("Tags", "type")
        for label, value in tags.summary_rows():
            builder.key_value(label, value, "music")
        if not tags.summary_rows():
            builder.line("No embedded tags – guessed nothing from the filename.", raw=True)
        await message_obj.reply(builder.build())

    async def _read_tags_via_mutagen(self, document: object, filename: str) -> TrackTags:
        """Read a non-MP3 container's tags; returns empty tags on any failure.

        The download is staged in the workspace and removed again, so a failed
        read never leaves a stray file behind.
        """
        ctx = self.context
        workspace = self._workspace
        if ctx is None or workspace is None:
            return TrackTags()
        staged = None
        try:
            buffer = await ctx.bot.download(document, destination=None)
            data = buffer.read() if hasattr(buffer, "read") else b""
            staged = workspace.write_bytes(safe_filename(filename), data)
            return read_tag_file(str(staged.path))
        except Exception:  # unreadable container, missing mutagen, disk error
            return TrackTags()
        finally:
            if staged is not None:
                workspace.remove(staged.name)

    # -- tagging ---------------------------------------------------------
    async def cmd_tag(self, message_obj: Message, argument: str) -> None:
        target = message_obj.reply_to_message
        document = getattr(target, "audio", None) or getattr(target, "document", None)
        if document is None:
            await message_obj.reply("Reply to an audio file with <code>/tag Artist - Title</code>.")
            return
        if not argument:
            await message_obj.reply("Usage: <code>/tag Artist - Title</code> or <code>/tag title=… artist=…</code>")
            return
        tags = parse_tag_argument(argument, getattr(document, "file_name", "") or "track.mp3")
        await self.apply_tags(message_obj, document, tags)

    async def cmd_magic(self, message_obj: Message) -> None:
        target = message_obj.reply_to_message
        document = getattr(target, "audio", None) or getattr(target, "document", None)
        if document is None:
            await message_obj.reply("Reply to an audio file with <code>/magic</code>.")
            return
        filename = getattr(document, "file_name", "") or "track.mp3"
        tags = guess_tags_from_filename(filename)
        if not tags.title:
            await message_obj.reply("Could not find anything to use – try <code>/tag Artist - Title</code>.")
            return
        await self.apply_tags(message_obj, document, tags)

    async def apply_tags(self, message_obj: Message, document: object, tags: TrackTags) -> None:
        ctx = self.context
        workspace = self._workspace
        assert ctx is not None
        if workspace is None:
            await message_obj.reply("Music plugin is not ready yet.")
            return
        if not bool(self.config("write_tags", True)):
            await message_obj.reply("Tag writing is disabled by the owner.")
            return
        filename = getattr(document, "file_name", "") or "track.mp3"
        size = int(getattr(document, "file_size", 0) or 0)
        limit = int(self.config("max_file_mb", 20)) * MB
        if size and size > limit:
            await message_obj.reply(f"File is larger than the {self.config('max_file_mb', 20)} MB tagging limit.")
            return
        cover = placeholder_cover(render_cover_text(tags)) if bool(self.config("embed_cover", True)) else None
        try:
            buffer = await ctx.bot.download(document, destination=None)
            data = buffer.read() if hasattr(buffer, "read") else b""
            fmt = supported_format(filename)
            if fmt == "unsupported":
                await message_obj.reply(
                    f"<b>Unsupported format</b>: <code>{esc(filename.rsplit('.', 1)[-1])}</code>. "
                    "MP3 is written in-tree; M4A/MP4/FLAC/OGG/Opus need <code>mutagen</code> "
                    "(<code>pip install mutagen</code>)."
                )
                return
            if fmt == "mutagen":
                # mutagen edits the file in place, so write the download out first
                staged = workspace.write_bytes(safe_filename(filename), data)
                write_tag_file(str(staged.path), tags, cover)
                tagged = Path(staged.path).read_bytes()
                stored = staged
                result = type("_Result", (), {"tags": tags, "artwork_applied": bool(cover)})()
            else:
                tagged, result = write_tag(data, tags, cover)
                stored = workspace.write_bytes(safe_filename(filename), tagged)
        except Exception as exc:  # pragma: no cover - download/disk failures
            await message_obj.reply(f"<b>Tagging failed</b>: {esc(str(exc))}")
            return
        caption = render_cover_text(result.tags)
        file_input = FSInputFile(str(stored.path), filename=stored.name)
        try:
            if bool(self.config("send_as_document", True)):
                await ctx.bot.send_document(message_obj.chat.id, file_input, caption=caption)
            else:
                await ctx.bot.send_audio(
                    message_obj.chat.id,
                    file_input,
                    caption=caption,
                    title=result.tags.title or None,
                    performer=result.tags.artist or None,
                    thumbnail=BufferedInputFile(cover, filename="cover.png") if cover else None,
                )
        finally:
            workspace.remove(stored.name)
        if result.tags.title:
            self._library.add(
                LibraryEntry.from_track(
                    stored.name, result.tags, size=len(tagged), chat=str(message_obj.chat.id)
                )
            )
            await self.persist()

    async def cmd_cover(self, message_obj: Message) -> None:
        target = message_obj.reply_to_message
        document = getattr(target, "audio", None) or getattr(target, "document", None)
        filename = getattr(document, "file_name", "") or "track.mp3"
        tags = guess_tags_from_filename(filename)
        cover = placeholder_cover(render_cover_text(tags))
        await message_obj.reply_photo(
            BufferedInputFile(cover, filename="cover.png"),
            caption=render_cover_text(tags),
            reply_markup=keyboard([[Btn("Tag this file", callback_data="music:tag", emoji="type")]]),
        )


def parse_tag_argument(argument: str, filename: str) -> TrackTags:
    """``Artist - Title`` or ``title=… artist=… album=…`` (or a mix)."""
    base = guess_tags_from_filename(filename)
    text = (argument or "").strip()
    if "=" in text:
        tags = TrackTags()
        pattern = re.compile(r"(\w+)\s*=\s*(.*?)(?=\s+\w+\s*=|$)", re.S)
        for key, value in pattern.findall(text):
            key = key.strip().lower()
            if key in tags.as_dict() and value.strip():
                setattr(tags, key, value.strip(" ,"))
        for name, value in base.as_dict().items():
            if not getattr(tags, name):
                setattr(tags, name, value)
        return tags
    if " - " in text:
        artist, _, title = text.partition(" - ")
        return TrackTags(
            title=title.strip() or base.title,
            artist=artist.strip(),
            album=base.album,
            year=base.year,
            track=base.track,
        )
    return TrackTags(
        title=text, artist=base.artist, album=base.album, year=base.year, track=base.track
    )


__all__ = ["MusicPlugin", "MANIFEST", "parse_tag_argument", "USAGE"]
