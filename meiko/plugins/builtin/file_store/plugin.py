"""``file_store`` handlers: save, fetch, browse, delete and quota reporting."""

from __future__ import annotations

import re
import time

from aiogram import F, Router
from aiogram.types import Message

from meiko.db.models import BotAccount
from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import FileTooLarge, QuotaExceeded, Workspace, human_size, safe_filename
from meiko.ui import Btn, esc, keyboard, message

from .manifest import MANIFEST
from .store import StoreError, StoredFile, VaultIndex

MEGABYTE = 1024 * 1024
TAG_RE = re.compile(r"#([\w-]{2,24})")


@plugin
class FileStorePlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._index: VaultIndex | None = None
        self._workspace: Workspace | None = None

    # -- lifecycle -------------------------------------------------------
    async def on_start(self, ctx: PluginContext) -> None:
        scope = ctx.scope.key
        self._workspace = Workspace(
            MANIFEST.slug,
            scope=scope,
            quota_bytes=int(self.config("quota_mb", 512)) * MEGABYTE,
            max_file_bytes=int(self.config("max_file_mb", 20)) * MEGABYTE,
            ttl_seconds=int(self.config("ttl_days", 30)) * 86400,
        )
        self._workspace.ensure()
        self._index = VaultIndex(ctx, self._workspace)
        router = Router(name="plugin:file_store")
        router.message.register(self.on_message, F.text.startswith("/"))
        ctx.add_router(router)
        if ctx.can("scheduler"):
            ctx.schedule("sweep", 3600 * 6, self.sweep)
        ctx.log("info", "file store ready", scope=scope, path=str(self._workspace.root))

    async def on_stop(self, ctx: PluginContext) -> None:
        self._index = None
        self._workspace = None

    def workspace(self) -> Workspace:
        if self._workspace is None:
            raise StoreError("file store is not running")
        return self._workspace

    def index(self) -> VaultIndex:
        if self._index is None:
            raise StoreError("file store is not running")
        return self._index

    async def sweep(self) -> None:  # pragma: no cover - scheduler hook
        removed = await self.index().sweep(int(self.config("ttl_days", 30)))
        if removed:
            self.workspace().sweep(int(self.config("ttl_days", 30)) * 86400)

    # -- helpers ---------------------------------------------------------
    async def _bot_owner_id(self) -> int | None:
        ctx = self.context
        assert ctx is not None
        if ctx.bot_id is None:
            return None
        async with ctx.session() as session:
            bot = await session.get(BotAccount, ctx.bot_id)
            return bot.owner_id if bot else None

    async def _reply(self, message_obj: Message, text: str, markup=None) -> None:
        ctx = self.context
        assert ctx is not None
        await ctx.bot.send_message(
            message_obj.chat.id, text, reply_markup=markup, reply_to_message_id=message_obj.message_id
        )

    # -- command routing -------------------------------------------------
    async def on_message(self, message_obj: Message) -> None:
        text = (message_obj.text or "").strip()
        command = text.split()[0].split("@")[0].lower() if text else ""
        handlers = {
            # The vault owns the /file* namespace: /save belongs to the post
            # saver (install both and the distinction matters).
            "/fsave": self.cmd_save,
            "/get": self.cmd_get,
            "/files": self.cmd_files,
            "/storestats": self.cmd_stats,
            "/storequota": self.cmd_stats,
            "/del": self.cmd_delete,
        }
        handler = handlers.get(command)
        if handler is not None:
            await handler(message_obj, text)

    async def cmd_save(self, message_obj: Message, text: str) -> None:
        if message_obj.chat.type != "private" and not bool(self.config("allow_groups", True)):
            await self._reply(message_obj, "Group vaults are disabled; save files in a private chat.")
            return
        target = message_obj.reply_to_message
        attachment = None
        for attribute in ("document", "video", "audio", "voice", "photo", "animation"):
            attachment = getattr(target, attribute, None) if target else None
            if attachment:
                break
        if target is None or attachment is None:
            await self._reply(
                message_obj,
                message("File Store", "database")
                .line("Reply to a file with <code>/fsave</code> to store it.", raw=True)
                .line("Add #tags or a caption to make it searchable.", raw=True)
                .build(),
            )
            return
        file_name = getattr(attachment, "file_name", None) or f"{attachment.file_unique_id}.bin"
        name = safe_filename(file_name)
        args = text.partition(" ")[2].strip()
        if args:
            candidate = safe_filename(args, fallback=name)
            if "." in candidate:
                name = candidate
        size = int(getattr(attachment, "file_size", 0) or 0)
        workspace = self.workspace()
        try:
            workspace.check_capacity(size)
        except (FileTooLarge, QuotaExceeded) as exc:
            await self._reply(message_obj, f"<b>Refused</b>: {esc(str(exc))}")
            return
        per_user = int(self.config("per_user_quota_mb", 64)) * MEGABYTE
        used_by_user = await self.index().usage_for(message_obj.from_user.id)
        if per_user and used_by_user + size > per_user:
            await self._reply(
                message_obj,
                f"<b>Refused</b>: your quota is {human_size(per_user)}, "
                f"used {human_size(used_by_user)}.",
            )
            return

        ctx = self.context
        assert ctx is not None
        try:
            downloaded = await ctx.bot.download(attachment, destination=None)
        except Exception as exc:  # pragma: no cover - network/telegram failure
            await self._reply(message_obj, f"<b>Download failed</b>: {esc(str(exc))}")
            return
        data = downloaded.read() if hasattr(downloaded, "read") else None
        if data is None:  # pragma: no cover - defensive
            await self._reply(message_obj, "Could not read the downloaded file.")
            return
        try:
            stored = workspace.write_bytes(name, data)
        except QuotaExceeded as exc:
            await self._reply(message_obj, f"<b>Refused</b>: {esc(str(exc))}")
            return
        caption = (target.caption or "")[:500]
        tags = TAG_RE.findall(caption)
        media_type = next(
            (kind for kind in ("document", "video", "audio", "voice", "photo", "animation")
             if getattr(target, kind, None) is not None),
            "document",
        )
        item = StoredFile(
            name=stored.name,
            size=stored.size,
            sha256=stored.sha256,
            owner_id=message_obj.from_user.id,
            uploaded_at=time.time(),
            source="upload",
            media_type=media_type,
            caption=caption,
            tags=tags,
            file_id=getattr(attachment, "file_id", ""),
        )
        try:
            await self.index().add(item)
        except StoreError as exc:
            workspace.remove(stored.name)
            await self._reply(message_obj, f"<b>Refused</b>: {esc(str(exc))}")
            return

        count, total = await self.index().total()
        await self._reply(
            message_obj,
            message("Saved", "check")
            .key_value("File", stored.name, "database")
            .key_value("Size", human_size(stored.size), "download")
            .key_value("Vault", f"{count} files · {human_size(total)}", "stats")
            .line(f"<code>sha256:{stored.sha256[:16]}…</code>", raw=True)
            .build(),
            reply_markup=keyboard([[Btn("Browse", callback_data="store:list", emoji="book")]]),
        )

    async def cmd_get(self, message_obj: Message, text: str) -> None:
        query = text.partition(" ")[2].strip()
        if not query:
            await self._reply(message_obj, "Usage: <code>/get &lt;name or #tag&gt;</code>", None)
            return
        matches = await self.index().search(query)
        if not matches:
            await self._reply(message_obj, f"No stored file matches <b>{esc(query)}</b>.")
            return
        item = matches[0]
        try:
            path = self.workspace().open_for_read(item.name)
        except FileNotFoundError:
            await self.index().remove(item.name)
            await self._reply(message_obj, "That entry was missing on disk and has been cleaned up.")
            return
        ctx = self.context
        assert ctx is not None
        from aiogram.types import FSInputFile

        await ctx.bot.send_document(
            message_obj.chat.id,
            FSInputFile(str(path), filename=item.name),
            caption=f"<b>{esc(item.name)}</b> · {human_size(item.size)}",
        )

    async def cmd_files(self, message_obj: Message, text: str) -> None:
        query = text.partition(" ")[2].strip()
        files = await self.index().search(query)
        if not files:
            await self._reply(
                message_obj,
                message("Vault", "database").line("Nothing stored yet. Reply to a file with /fsave.").build(),
            )
            return
        page_size = max(3, int(self.config("index_page_size", 10)))
        builder = message("Vault", "database").key_value(
            "Entries", f"{len(files)} shown of {len(await self.index().load())}", "database"
        )
        for item in files[:page_size]:
            builder.bullet(
                f"<b>{esc(item.name)}</b> · {human_size(item.size)} · {item.media_type}",
                "database",
            )
        if len(files) > page_size:
            builder.line(f"<i>…and {len(files) - page_size} more. Use /get &lt;name&gt;.</i>", raw=True)
        await self._reply(message_obj, builder.build())

    async def cmd_stats(self, message_obj: Message, text: str) -> None:
        workspace = self.workspace()
        count, total = await self.index().total()
        mine = await self.index().usage_for(message_obj.from_user.id)
        await self._reply(
            message_obj,
            message("Vault usage", "stats")
            .key_value("Files", count, "database")
            .key_value("Stored", human_size(total), "download")
            .key_value("Quota", human_size(workspace.quota_bytes), "server")
            .key_value("Your share", human_size(mine), "user")
            .key_value("Largest file", human_size(workspace.max_file_bytes), "upload")
            .build(),
        )

    async def cmd_delete(self, message_obj: Message, text: str) -> None:
        name = text.partition(" ")[2].strip()
        if not name:
            await self._reply(message_obj, "Usage: <code>/del &lt;name&gt;</code>")
            return
        matches = await self.index().search(name)
        if not matches:
            await self._reply(message_obj, f"No stored file matches <b>{esc(name)}</b>.")
            return
        item = matches[0]
        bot_owner = await self._bot_owner_id()
        if item.owner_id != message_obj.from_user.id:
            # group admins and the bot owner may delete on behalf of others
            principal_ok = item.owner_id == bot_owner
            if not principal_ok:
                await self._reply(message_obj, "You can only delete files you stored.")
                return
        removed = await self.index().remove(item.name)
        if removed is None:
            await self._reply(message_obj, "That entry disappeared before it could be deleted.")
            return
        await self._reply(message_obj, f"Deleted <b>{esc(removed.name)}</b> ({human_size(removed.size)}).")


__all__ = ["FileStorePlugin", "MANIFEST"]
