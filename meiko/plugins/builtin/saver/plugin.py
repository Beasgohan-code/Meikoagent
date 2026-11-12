"""``saver`` handlers – post saving, caption templating, word rules, thumbnails."""

from __future__ import annotations

import asyncio
from typing import Any

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

from .captions import CaptionContext, render_caption
from .links import LinkPlan, LinkTarget, chunks_of, plan_links
from .manifest import MANIFEST
from .state import (
    INDEX_KEY,
    SEND_MODES,
    STATE_KEY,
    SavedIndex,
    SavedPost,
    SaverConfig,
    apply_word_rules,
    validate_caption,
)

MEDIA_ATTRIBUTES = ("document", "video", "audio", "photo", "animation", "voice")

#: Commands owned by this plugin – everything else must fall through.



def attachment_of(source: Message) -> Any:
    """Return the first media attribute present on a message (or None)."""
    for attribute in MEDIA_ATTRIBUTES:
        value = getattr(source, attribute, None)
        if value is not None:
            return attribute, value
    return None


def describe_attachment(kind: str, media: Any) -> dict[str, Any]:
    if kind == "photo":
        largest = media[-1]
        return {
            "unique_id": largest.file_unique_id,
            "filename": f"photo_{largest.file_unique_id}.jpg",
            "size": largest.file_size or 0,
            "duration": 0,
        }
    return {
        "unique_id": getattr(media, "file_unique_id", ""),
        "filename": getattr(media, "file_name", "") or f"{kind}_{getattr(media, 'file_unique_id', 'file')}",
        "size": getattr(media, "file_size", 0) or 0,
        "duration": getattr(media, "duration", 0) or 0,
    }


#: Only dispatch commands this package declares – never the whole "/" space.
OWN_COMMAND = OwnCommand(MANIFEST.commands)

@plugin
class SaverPlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._config = SaverConfig()
        self._index = SavedIndex()
        self._workspace: Workspace | None = None
        self._state: PluginState | None = None
        self._lock: asyncio.Lock | None = None

    # -- lifecycle -------------------------------------------------------
    async def on_start(self, ctx: PluginContext) -> None:
        self._state = PluginState(ctx)
        self._config = SaverConfig.from_dict(await self._state.get(STATE_KEY))
        self._index = SavedIndex(await self._state.get(INDEX_KEY))
        self._workspace = Workspace(
            MANIFEST.slug,
            scope=ctx.scope.key,
            quota_bytes=1024 * 1024 * 1024,
            max_file_bytes=int(self.config("max_file_mb", 45)) * 1024 * 1024,
            ttl_seconds=3600,
        )
        self._lock = asyncio.Lock()
        router = Router(name="plugin:saver")
        # Own namespace only: a catch-all ``F.text`` would swallow commands of
        # plugins registered later in the dispatcher.
        router.message.register(self.on_own_command, F.text.startswith("/"), OWN_COMMAND)
        router.message.register(self.on_link_text, F.text.contains("t.me/"))
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        self._workspace = None
        self._lock = None

    # -- persistence -----------------------------------------------------
    async def save_config(self) -> None:
        ctx = self.context
        if ctx is None:
            return
        self._config.touch()
        state = self._state or PluginState(ctx)
        await state.set(STATE_KEY, self._config.to_dict())

    async def save_index(self) -> None:
        ctx = self.context
        if ctx is None:
            return
        state = self._state or PluginState(ctx)
        await state.set(INDEX_KEY, self._index.to_list())

    # -- router ----------------------------------------------------------
    async def on_own_command(self, message_obj: Message, **_: object) -> None:
        await self.on_command(message_obj)

    async def on_link_text(self, message_obj: Message, **_: object) -> None:
        if not bool(self.config("auto_save", False)):
            return
        plan = plan_links(message_obj.text or "")
        if plan.ok:
            await self.execute_plan(message_obj, plan)

    async def on_command(self, message_obj: Message) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        simple = {
            "/saver": self.cmd_panel,
            "/savestats": self.cmd_stats,
            "/resetcaption": self.cmd_reset_caption,
            "/caption": self.cmd_caption_toggle,
            "/clearwords": self.cmd_clear_words,
            "/setthumb": self.cmd_set_thumb,
            "/removethumb": self.cmd_remove_thumb,
        }
        with_argument = {
            "/save": self.cmd_save,
            "/saved": self.cmd_saved,
            "/setcaption": self.cmd_set_caption,
            "/delword": self.cmd_delete_word,
            "/replaceword": self.cmd_replace_word,
            "/setdump": self.cmd_set_dump,
            "/savemode": self.cmd_send_mode,
            "/savebatch": self.cmd_batch,
        }
        handler = simple.get(command)
        if handler is not None:
            await handler(message_obj)
            return
        handler = with_argument.get(command)
        if handler is not None:
            await handler(message_obj, argument)

    # -- simple commands -------------------------------------------------
    async def cmd_panel(self, message_obj: Message) -> None:
        rows = self._config.summary_rows()
        builder = message("Post saver", "download").key_value("Index", f"{len(self._index)} post(s)", "database")
        for label, value in rows:
            builder.key_value(label, value)
        builder.line(
            "<code>/save t.me/c/1234/100-120</code> · <code>/setcaption {filename}</code>",
            raw=True,
        )
        await message_obj.reply(
            builder.build(),
            reply_markup=keyboard(
                [
                    [Btn("Stats", callback_data="saver:stats", emoji="chart"),
                     Btn("Recent", callback_data="saver:recent", emoji="clock")],
                    [Btn("Toggle caption", callback_data="saver:caption", emoji="pencil")],
                ]
            ),
        )

    async def cmd_stats(self, message_obj: Message) -> None:
        stats = self._index.stats()
        await message_obj.reply(
            message("Saver stats", "chart")
            .key_value("Saved", str(stats["count"]), "database")
            .key_value("Volume", human_size(int(stats["bytes"])), "download")
            .key_value("Batch size", str(self._config.max_batch), "gear")
            .key_value("Duplicates", "skipped" if self._config.skip_duplicates else "allowed", "check")
            .build()
        )

    async def cmd_saved(self, message_obj: Message, argument: str) -> None:
        requested = argument.strip()
        limit = int(requested) if requested.isdigit() else 5
        limit = min(20, max(1, limit))
        recent = self._index.recent(limit)
        if not recent:
            await message_obj.reply("Nothing saved yet. Send <code>/save t.me/…</code>.")
            return
        builder = message("Recent saves", "clock")
        for post in recent:
            builder.bullet(f"<code>{esc(post.filename or post.kind)}</code> · {human_size(post.size)}")
        await message_obj.reply(builder.build())

    async def cmd_set_caption(self, message_obj: Message, argument: str) -> None:
        template = argument
        reply = message_obj.reply_to_message
        if not template and reply is not None:
            template = (reply.text or reply.caption or "").strip()
        if not template:
            await message_obj.reply(
                "Usage: <code>/setcaption {filename} | {size}</code> "
                "(or reply to a message with <code>/setcaption</code>)"
            )
            return
        ok, reason = validate_caption(template)
        if not ok:
            await message_obj.reply(f"<b>Caption rejected</b>: {esc(reason)}")
            return
        self._config.caption_template = template
        self._config.use_caption = True
        await self.save_config()
        preview = render_caption(template, CaptionContext(filename="Example.Show.S01E02.mkv", size=734003200))
        await message_obj.reply(
            message("Caption saved", "check").key_value("Preview", preview, "eye").build()
        )

    async def cmd_reset_caption(self, message_obj: Message) -> None:
        self._config.caption_template = MANIFEST.config_schema["caption_template"]["default"]
        self._config.use_caption = False
        await self.save_config()
        await message_obj.reply("Caption reset to the default and disabled.")

    async def cmd_caption_toggle(self, message_obj: Message) -> None:
        self._config.use_caption = not self._config.use_caption
        await self.save_config()
        state = "on" if self._config.use_caption else "off"
        await message_obj.reply(f"Custom captions are <b>{state}</b>.")

    async def cmd_delete_word(self, message_obj: Message, argument: str) -> None:
        words = [word for word in argument.replace(",", " ").split() if word]
        if not words:
            await message_obj.reply("Usage: <code>/delword x265 yify</code>")
            return
        self._config.delete_words = list(dict.fromkeys([*self._config.delete_words, *words]))[:50]
        await self.save_config()
        await message_obj.reply(
            message("Words will be removed", "check")
            .key_value("Rules", self._config.rules.describe(), "gear")
            .build()
        )

    async def cmd_replace_word(self, message_obj: Message, argument: str) -> None:
        if "=>" not in argument:
            await message_obj.reply("Usage: <code>/replaceword Dual Audio=&gt;Multi Audio</code>")
            return
        self._config.replace_words = [*self._config.replace_words, argument.strip()][-50:]
        await self.save_config()
        example = apply_word_rules("Show Dual Audio x265 1080p", self._config)
        await message_obj.reply(
            message("Replacement saved", "check").key_value("Example", example, "eye").build()
        )

    async def cmd_clear_words(self, message_obj: Message) -> None:
        self._config.delete_words.clear()
        self._config.replace_words.clear()
        await self.save_config()
        await message_obj.reply("All word rules cleared.")

    async def cmd_set_thumb(self, message_obj: Message) -> None:
        target = message_obj.reply_to_message or message_obj
        found = attachment_of(target)
        if found is None or found[0] != "photo":
            await message_obj.reply("Reply to a <b>photo</b> with <code>/setthumb</code>.")
            return
        _, photo = found
        self._config.thumbnail_file_id = photo[-1].file_id
        await self.save_config()
        await message_obj.reply("Custom thumbnail saved – new saves will embed it.")

    async def cmd_remove_thumb(self, message_obj: Message) -> None:
        self._config.thumbnail_file_id = None
        await self.save_config()
        await message_obj.reply("Custom thumbnail removed.")

    async def cmd_set_dump(self, message_obj: Message, argument: str) -> None:
        value = argument.strip()
        if value in ("here", "this", "current"):
            self._config.dump_chat = message_obj.chat.id
        elif value in ("", "off", "reset"):
            self._config.dump_chat = None
            await self.save_config()
            await message_obj.reply("Dump chat reset to the current chat.")
            return
        elif value.lstrip("-").isdigit():
            self._config.dump_chat = int(value)
        elif value.startswith("@") or value.replace("_", "").isalnum():
            self._config.dump_chat = value.lstrip("@")
        else:
            await message_obj.reply("Usage: <code>/setdump here</code> or <code>/setdump @channel</code>")
            return
        await self.save_config()
        await message_obj.reply(f"Dump chat set to <code>{esc(str(self._config.dump_chat))}</code>.")

    async def cmd_send_mode(self, message_obj: Message, argument: str) -> None:
        mode = argument.strip().lower()
        if mode not in SEND_MODES:
            await message_obj.reply(
                "Usage: <code>/savemode copy|document|video</code>\n"
                f"Current: <b>{esc(self._config.send_mode)}</b>"
            )
            return
        self._config.send_mode = mode
        await self.save_config()
        await message_obj.reply(f"Send mode set to <b>{esc(mode)}</b>.")

    async def cmd_batch(self, message_obj: Message, argument: str) -> None:
        if not argument.strip().isdigit():
            await message_obj.reply(f"Usage: <code>/savebatch 1-50</code> (current {self._config.max_batch})")
            return
        self._config.max_batch = min(50, max(1, int(argument.strip())))
        await self.save_config()
        await message_obj.reply(f"Batch size set to <b>{self._config.max_batch}</b>.")

    # -- the real work ---------------------------------------------------
    async def cmd_save(self, message_obj: Message, argument: str) -> None:
        if not argument:
            # replying to a post? save that single message instead of links
            if message_obj.reply_to_message is not None:
                await self.save_single(message_obj, message_obj.reply_to_message)
                return
            await message_obj.reply(
                message("Post saver", "download")
                .line("Send <code>/save t.me/channel/123</code>", raw=True)
                .line("Ranges: <code>t.me/c/1234/100-120</code> · <code>t.me/x/10 11-13</code>", raw=True)
                .line("Lists: <code>t.me/x/10,12,14</code>", raw=True)
                .build()
            )
            return
        plan = plan_links(argument, allow_bare_username=bool(self.config("allow_usernames", True)))
        if not plan.ok:
            await message_obj.reply(
                message("Nothing to save", "shield")
                .line(esc(plan.describe()), raw=True)
                .build()
            )
            return
        await self.execute_plan(message_obj, plan)

    async def save_single(self, message_obj: Message, source: Message) -> None:
        ctx = self.context
        assert ctx is not None
        target = LinkTarget(chat=source.chat.id, raw=f"{source.chat.id}/{source.message_id}")
        from .links import MessageRange

        target.ranges = [MessageRange(source.message_id, source.message_id)]
        await self.execute_plan(message_obj, LinkPlan(targets=[target]))

    async def execute_plan(self, message_obj: Message, plan: LinkPlan) -> None:
        ctx = self.context
        assert ctx is not None
        total = plan.total
        if total > int(self.config("max_messages", 500)):
            await message_obj.reply(f"<b>Too many messages</b>: {total} (limit {self.config('max_messages', 500)}).")
            return
        status = await message_obj.reply(f"⏳ Saving {total} message(s)…")
        copied = failed = skipped = 0
        processed = 0
        for target in plan.targets:
            for batch in chunks_of(target.message_ids, self._config.max_batch):
                for message_id in batch:
                    outcome = await self.save_one(target, message_id)
                    processed += 1
                    if outcome == "copied":
                        copied += 1
                    elif outcome == "duplicate":
                        skipped += 1
                    else:
                        failed += 1
                    if self._config.delay_seconds:
                        await asyncio.sleep(self._config.delay_seconds)
                if processed and processed % 20 == 0:
                    await self.safe_edit(status, f"⏳ {processed}/{total} processed…")
        await self.safe_edit(
            status,
            message("Save finished", "check")
            .key_value("Saved", str(copied), "download")
            .key_value("Skipped", str(skipped), "shield")
            .key_value("Failed", str(failed), "x")
            .build(),
        )
        await self.save_index()

    async def safe_edit(self, status: Message, text: str) -> None:
        """Update the progress message; never fail the run because of it.

        Uses the explicit Bot API call: aiogram does not mount a bot on the
        Message returned by ``send_message``/``reply``, so ``status.edit_text``
        would raise ``RuntimeError``.  Failures are logged, not swallowed – a
        missing progress line is how a broken flush path hides for weeks.
        """
        await self.edit_text(status, text)

    async def save_one(self, target: LinkTarget, message_id: int) -> str:
        ctx = self.context
        assert ctx is not None
        dump = self._config.dump_chat or target.chat
        try:
            source = await ctx.bot.copy_message(
                chat_id=dump,
                from_chat_id=target.chat,
                message_id=message_id,
                message_thread_id=target.topic_id,
            )
        except Exception as exc:  # pragma: no cover - network/permission errors
            ctx.log("warning", f"save failed for {target.chat}/{message_id}: {exc}")
            return "failed"

        if isinstance(source, bool):
            return "copied"
        found = attachment_of(source)
        kind, media = found if found else ("file", None)
        details = describe_attachment(kind, media) if media is not None else {
            "unique_id": getattr(source, "media_group_id", "") or f"{target.chat}:{message_id}",
            "filename": "",
            "size": 0,
            "duration": 0,
        }
        if self._config.needs_download and media is not None:
            redelivered = await self.redeliver(dump, target, source, kind, media, details)
            if redelivered:
                source = redelivered
        uid = str(details["unique_id"] or f"{target.chat}:{message_id}")
        if self._config.skip_duplicates and self._index.is_duplicate(uid):
            return "duplicate"
        self._index.add(
            SavedPost(
                chat=str(target.chat),
                message_id=int(getattr(source, "message_id", message_id) or message_id),
                unique_id=uid,
                filename=str(details["filename"]),
                size=int(details["size"] or 0),
                kind=kind,
            )
        )
        return "copied"


    async def redeliver(
        self,
        dump: int | str,
        target: LinkTarget,
        source: Message,
        kind: str,
        media: Any,
        details: dict[str, Any],
    ) -> Message | None:
        """Re-upload with a templated caption / custom thumbnail (or None on failure)."""
        ctx = self.context
        workspace = self._workspace
        if ctx is None or workspace is None or media is None:
            return None
        size = int(details.get("size") or 0)
        limit = int(self.config("max_file_mb", 45)) * 1024 * 1024
        if size and size > limit:
            ctx.log("info", f"saver: {details['filename']} exceeds {limit} bytes, copying instead")
            return None
        caption = render_caption(
            self._config.caption_template,
            CaptionContext(
                filename=str(details.get("filename") or ""),
                size=size,
                title=str(details.get("filename") or ""),
                chat_title=str(getattr(source.chat, "title", "") or ""),
                message_id=int(getattr(source, "message_id", 0) or 0),
                duration_seconds=int(details.get("duration") or 0),
            ),
        )
        caption = apply_word_rules(caption, self._config)
        if not self._config.use_caption:
            caption = source.caption or ""
        try:
            buffer = await ctx.bot.download(media, destination=None)
            data = buffer.read() if hasattr(buffer, "read") else b""
            name = safe_filename(str(details.get("filename") or f"{kind}.bin"))
            stored = workspace.write_bytes(name, data)
        except Exception as exc:  # pragma: no cover - download/disk failures
            ctx.log("warning", f"saver: re-upload skipped ({exc})")
            return None
        file_input = FSInputFile(str(stored.path), filename=stored.name)
        thumbnail: Any = None
        if self._config.thumbnail_file_id:
            try:
                thumb_buffer = await ctx.bot.download(self._config.thumbnail_file_id, destination=None)
                thumb_bytes = thumb_buffer.read() if hasattr(thumb_buffer, "read") else b""
                thumbnail = BufferedInputFile(thumb_bytes, filename="thumb.jpg")
            except Exception:  # pragma: no cover - invalid file id
                thumbnail = None
        try:
            if kind == "video":
                sent = await ctx.bot.send_video(
                    dump, file_input, caption=caption, thumbnail=thumbnail, supports_streaming=True
                )
            elif kind == "audio":
                sent = await ctx.bot.send_audio(dump, file_input, caption=caption, thumbnail=thumbnail)
            else:
                sent = await ctx.bot.send_document(dump, file_input, caption=caption, thumbnail=thumbnail)
            return sent
        except Exception as exc:  # pragma: no cover - telegram rejections
            ctx.log("warning", f"saver: re-upload failed ({exc})")
            return None
        finally:
            workspace.remove(stored.name)


__all__ = ["SaverPlugin", "attachment_of", "describe_attachment", "MANIFEST"]
