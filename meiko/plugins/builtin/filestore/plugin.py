"""``filestore`` handlers — multi-user vault commands and share links.

Commands::

    /put              reply to any file → store it in your vault
    /myfiles [page]   your files (paged)
    /getf <id|name>   fetch a file back
    /share [days] [uses]  mint a signed /start link for the replied/stored file
    /revoke <id>      revoke every link for a file
    /vaultsearch <q>  search your files (name + tags)
    /vaultusage       per-member usage, quota and retention
    /tagf <tags>      tag the replied stored file
    /expire <days>    change retention for the replied file
    /collections      list your collections
    /newcollection <name> [public]
    /collection <name>  browse one collection
    /vaultstats       scope-wide vault statistics
"""

from __future__ import annotations

import time
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import OwnCommand, PluginState, human_size
from meiko.ui import Btn, esc, keyboard, message

from .collections import MEGABYTE, StoredFile, Vault, VaultError, encoding_choices
from .links import LinkBook, issue, parse_start_payload, start_deep_link
from .manifest import MANIFEST

OWN_COMMAND = OwnCommand(MANIFEST.commands)
VAULT_KEY = "vault"


def file_extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


@plugin
class FileStorePlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._vault: Vault | None = None
        self._links: LinkBook | None = None

    # -- lifecycle ---------------------------------------------------------
    async def on_start(self, ctx: PluginContext) -> None:
        raw = await ctx.get_state(VAULT_KEY, None) if ctx.can("storage.read") else None
        self._vault = Vault.from_dict(
            raw,
            global_quota=int(self.config("global_quota_mb", 2048)) * MEGABYTE,
            per_user_quota=int(self.config("per_user_quota_mb", 256)) * MEGABYTE,
            max_file_bytes=int(self.config("max_file_mb", 20)) * MEGABYTE,
            max_files=int(self.config("max_files", 5000)),
        )
        secret = str(self.config("share_secret", "") or f"{ctx.scope.key}:{MANIFEST.slug}")
        self._links = await LinkBook(PluginState(ctx), secret).load()
        router = Router(name="plugin:filestore")
        router.message.register(self.on_message, F.text.startswith("/"), OWN_COMMAND)
        router.callback_query.register(self.on_callback, F.data.startswith("fs:"))
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        await self.persist()
        self._vault = None
        self._links = None

    # -- helpers -----------------------------------------------------------
    def vault(self) -> Vault:
        if self._vault is None:
            raise VaultError("vault is not running")
        return self._vault

    def links(self) -> LinkBook:
        if self._links is None:
            raise VaultError("vault is not running")
        return self._links

    async def persist(self) -> None:
        if self.context is not None and self._vault is not None:
            await self.context.set_state(VAULT_KEY, self._vault.to_dict())
            if self._links is not None:
                await self._links.save()

    async def on_message(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        routes = {
            "/put": self.cmd_put,
            "/myfiles": self.cmd_myfiles,
            "/getf": self.cmd_get,
            "/share": self.cmd_share,
            "/revoke": self.cmd_revoke,
            "/vaultsearch": self.cmd_search,
            "/vaultusage": self.cmd_usage,
            "/tagf": self.cmd_tag,
            "/expire": self.cmd_expire,
            "/collections": self.cmd_collections,
            "/newcollection": self.cmd_new_collection,
            "/collection": self.cmd_collection,
            "/vaultstats": self.cmd_stats,
        }
        handler = routes.get(command)
        if handler is None:
            return
        if command in {"/put", "/myfiles", "/getf", "/share", "/revoke", "/vaultsearch",
                       "/tagf", "/expire", "/newcollection", "/collection"}:
            await handler(message_obj, argument)
        else:
            await handler(message_obj)

    def _owner(self, message_obj: Message) -> int:
        return message_obj.from_user.id if message_obj.from_user else 0

    # -- storing -----------------------------------------------------------
    async def cmd_put(self, message_obj: Message, argument: str = "") -> None:
        vault = self.vault()
        target = message_obj.reply_to_message or message_obj
        document = (
            getattr(target, "document", None)
            or getattr(target, "video", None)
            or getattr(target, "audio", None)
            or getattr(target, "voice", None)
        )
        if document is None:
            await message_obj.reply(
                message("Nothing to store", "warn")
                .line("Reply to a file with <code>/put</code>.", raw=True)
                .line("Optional: <code>/put collection-name</code>", raw=True)
                .build()
            )
            return
        owner = self._owner(message_obj)
        size = int(getattr(document, "file_size", 0) or 0)
        name = getattr(document, "file_name", "") or f"{getattr(document, 'file_unique_id', uuid.uuid4().hex)[:12]}.bin"
        collection = argument.strip().lower() or "default"
        ttl_days = int(self.config("default_ttl_days", 30))
        entry = StoredFile(
            id=uuid.uuid4().hex[:10],
            name=name,
            size=size,
            owner_id=owner,
            file_id=getattr(document, "file_id", ""),
            mime=getattr(document, "mime_type", "") or "application/octet-stream",
            collection=collection,
            visibility=str(self.config("default_visibility", "private")),
            created_at=int(time.time()),
            expires_at=int(time.time()) + ttl_days * 86400 if ttl_days else 0,
            message_id=target.message_id,
            chat_id=target.chat.id,
        )
        try:
            vault.add(entry)
        except VaultError as exc:
            await message_obj.reply(message("Not stored", "warn").line(esc(str(exc)), raw=True).build())
            return
        if collection != "default" and collection not in vault.collections:
            vault.create_collection(collection, owner)
        await self.persist()
        await message_obj.reply(
            message("Stored", "check")
            .key_value("File", entry.name, "file")
            .key_value("ID", entry.id, "id")
            .key_value("Size", human_size(size), "database")
            .key_value("Collection", entry.collection, "folder")
            .key_value("Used", f"{human_size(vault.used(owner))} / {human_size(vault.per_user_quota)}", "stats")
            .build(),
            reply_markup=keyboard(
                [
                    [Btn("Share", callback_data=f"fs:share:{entry.id}", emoji="link"),
                     Btn("Fetch", callback_data=f"fs:get:{entry.id}", emoji="download")],
                    [Btn("Tags", callback_data=f"fs:tags:{entry.id}", emoji="type"),
                     Btn("Delete", callback_data=f"fs:del:{entry.id}", emoji="trash", style="danger")],
                ]
            ),
        )

    # -- browsing ----------------------------------------------------------
    async def cmd_myfiles(self, message_obj: Message, argument: str = "") -> None:
        page = int(argument) if argument.strip().isdigit() else 1
        await self._listing(message_obj, self._owner(message_obj), page=page)

    async def _listing(self, message_obj: Message, user_id: int, *, page: int = 1, collection: str | None = None, query: str = "") -> None:
        vault = self.vault()
        per_page = int(self.config("page_size", 8))
        rows, total = vault.list_for(user_id, collection=collection, query=query, page=page, per_page=per_page)
        title = f"Files · {collection}" if collection else ("Search · " + query if query else "Your files")
        builder = message(title[:60], "folder")
        builder.key_value("Stored", f"{total} file(s)", "stats")
        builder.key_value("Quota", f"{human_size(vault.used(user_id))} / {human_size(vault.per_user_quota)}", "database")
        builder.blank()
        if not rows:
            builder.line("Nothing here yet — reply to a file with <code>/put</code>.", raw=True)
        for entry in rows:
            badges = []
            if entry.visibility != "private":
                badges.append(entry.visibility)
            if entry.expired:
                badges.append("expired")
            suffix = f" · {' · '.join(badges)}" if badges else ""
            builder.line(
                f"<code>{esc(entry.id)}</code> <b>{esc(entry.name[:40])}</b>\n"
                f"    {human_size(entry.size)} · {esc(entry.collection)}{suffix}",
                raw=True,
            )
        pages = max(1, (total + per_page - 1) // per_page)
        if pages > 1:
            builder.line(f"<i>page {page}/{pages}</i>", raw=True)
        nav = []
        if page > 1:
            nav.append(Btn("Prev", callback_data=f"fs:page:{page - 1}", emoji="arrow"))
        if page < pages:
            nav.append(Btn("Next", callback_data=f"fs:page:{page + 1}", emoji="arrow"))
        rows_markup = [nav] if nav else []
        rows_markup.append([Btn("Share", callback_data="fs:needfile", emoji="link"), Btn("Usage", callback_data="fs:usage", emoji="stats")])
        await message_obj.reply(builder.build(), reply_markup=keyboard(rows_markup))

    async def cmd_search(self, message_obj: Message, argument: str) -> None:
        query = argument.strip()
        if not query:
            await message_obj.reply("Usage: <code>/vaultsearch report</code>")
            return
        await self._listing(message_obj, self._owner(message_obj), query=query)

    async def cmd_collection(self, message_obj: Message, argument: str) -> None:
        name = argument.strip().lower()
        if not name:
            await self.cmd_collections(message_obj)
            return
        await self._listing(message_obj, self._owner(message_obj), collection=name)

    async def cmd_collections(self, message_obj: Message) -> None:
        vault = self.vault()
        owner = self._owner(message_obj)
        items = vault.collections_for(owner)
        builder = message("Collections", "folder")
        if not items:
            builder.line("No collections yet — <code>/newcollection media</code>.", raw=True)
        for col in items:
            count = sum(1 for f in vault.files.values() if f.collection == col.name)
            builder.line(
                f"<code>{esc(col.name)}</code> · {count} file(s){' · public' if col.public else ''}", raw=True
            )
        await message_obj.reply(builder.build())

    async def cmd_new_collection(self, message_obj: Message, argument: str) -> None:
        parts = argument.split()
        if not parts:
            await message_obj.reply("Usage: <code>/newcollection media [public]</code>")
            return
        public = "public" in [p.lower() for p in parts[1:]]
        if public and not bool(self.config("allow_public", True)):
            await message_obj.reply(message("Public collections are disabled", "warn").build())
            return
        try:
            col = self.vault().create_collection(parts[0], self._owner(message_obj), public=public)
        except VaultError as exc:
            await message_obj.reply(message("Not created", "warn").line(esc(str(exc)), raw=True).build())
            return
        await self.persist()
        await message_obj.reply(
            message("Collection created", "check").key_value("Name", col.name, "folder").build()
        )

    # -- retrieval ---------------------------------------------------------
    async def cmd_get(self, message_obj: Message, argument: str) -> None:
        key = argument.strip()
        if not key:
            await message_obj.reply("Usage: <code>/getf &lt;id or name&gt;</code>")
            return
        await self._deliver(message_obj, key)

    async def _deliver(self, message_obj: Message, key: str) -> None:
        vault = self.vault()
        entry = vault.get(key, user_id=self._owner(message_obj))
        if entry is None:
            await message_obj.reply(message("Not found", "search").line("Check <code>/myfiles</code>.", raw=True).build())
            return
        if entry.expired:
            vault.files.pop(entry.id, None)
            await self.persist()
            await message_obj.reply(message("Expired", "warn").line("That file passed its retention window.", raw=True).build())
            return
        entry.downloads += 1
        await self.persist()
        kind = "video" if entry.mime.startswith("video/") else "audio" if entry.mime.startswith("audio/") else "document"
        caption = f"<code>{esc(entry.name)}</code> · {human_size(entry.size)}"
        try:
            if kind == "video":
                await self.context.bot.send_video(message_obj.chat.id, entry.file_id, caption=caption)
            elif kind == "audio":
                await self.context.bot.send_audio(message_obj.chat.id, entry.file_id, caption=caption)
            else:
                await self.context.bot.send_document(message_obj.chat.id, entry.file_id, caption=caption)
        except Exception as exc:  # pragma: no cover - telegram edge cases
            await message_obj.reply(
                message("Delivery failed", "warn").line(esc(str(exc)), raw=True).build()
            )

    # -- share links -------------------------------------------------------
    async def cmd_share(self, message_obj: Message, argument: str) -> None:
        parts = argument.split()
        key = ""
        days = int(self.config("share_ttl_days", 7))
        uses = 0
        for part in parts:
            if part.isdigit():
                if days == int(self.config("share_ttl_days", 7)):
                    days = int(part)
                else:
                    uses = int(part)
            else:
                key = part
        if not key and message_obj.reply_to_message is not None:
            stored = [
                entry
                for entry in self.vault().files.values()
                if entry.message_id == message_obj.reply_to_message.message_id
                and entry.chat_id == message_obj.reply_to_message.chat.id
            ]
            if stored:
                key = stored[0].id
        if not key:
            await message_obj.reply("Usage: <code>/share &lt;id&gt; [days] [uses]</code>")
            return
        entry = self.vault().get(key, user_id=self._owner(message_obj))
        if entry is None:
            await message_obj.reply(message("Not found", "search").build())
            return
        link = self.links().add(issue(entry.file_id or entry.id, entry.owner_id, self.links().secret, ttl_days=days, max_uses=uses))
        await self.persist()
        bot_username = getattr(self.context.bot, "username", "") if self.context and self.context.bot else ""
        url = start_deep_link(bot_username or "meiko_bot", link.token)
        await message_obj.reply(
            message("Share link", "link")
            .line(f"<code>{esc(url)}</code>", raw=True)
            .key_value("File", entry.name, "file")
            .key_value("Expires", f"{days} day(s)" if days else "never", "clock")
            .key_value("Uses", str(uses) if uses else "unlimited", "stats")
            .line("<i>Anyone with the link can fetch it once opened; /revoke to kill it.</i>", raw=True)
            .build()
        )

    async def cmd_revoke(self, message_obj: Message, argument: str) -> None:
        key = argument.strip()
        if not key:
            await message_obj.reply("Usage: <code>/revoke &lt;id&gt;</code>")
            return
        entry = self.vault().get(key, user_id=self._owner(message_obj))
        if entry is None:
            await message_obj.reply(message("Not found", "search").build())
            return
        removed = self.links().revoke_file(entry.file_id or entry.id)
        await self.persist()
        await message_obj.reply(
            message("Revoked", "check").line(f"{removed} link(s) removed.", raw=True).build()
        )

    # -- metadata ----------------------------------------------------------
    async def cmd_tag(self, message_obj: Message, argument: str) -> None:
        tags = [tag.strip("#").lower() for tag in argument.replace(",", " ").split() if tag]
        target = message_obj.reply_to_message
        if target is None or not tags:
            await message_obj.reply("Reply to a stored file with <code>/tagf holiday 2024</code>")
            return
        entries = [
            entry
            for entry in self.vault().files.values()
            if entry.message_id == target.message_id and entry.chat_id == target.chat.id
        ]
        if not entries:
            await message_obj.reply(message("Not a stored file", "warn").build())
            return
        for entry in entries:
            entry.tags = sorted({*entry.tags, *tags})[:12]
        await self.persist()
        await message_obj.reply(
            message("Tagged", "type").line(" ".join("#" + esc(tag) for tag in tags), raw=True).build()
        )

    async def cmd_expire(self, message_obj: Message, argument: str) -> None:
        if not argument.strip().isdigit():
            await message_obj.reply("Usage: <code>/expire 7</code> (days, 0 = forever)")
            return
        days = int(argument.strip())
        target = message_obj.reply_to_message
        key = ""
        if target is not None:
            stored = [
                entry
                for entry in self.vault().files.values()
                if entry.message_id == target.message_id and entry.chat_id == target.chat.id
            ]
            key = stored[0].id if stored else ""
        if not key:
            await message_obj.reply("Reply to a stored file with <code>/expire 7</code>")
            return
        entry = self.vault().get(key, user_id=self._owner(message_obj))
        if entry is None:
            await message_obj.reply(message("Not found", "search").build())
            return
        entry.expires_at = int(time.time()) + days * 86400 if days else 0
        await self.persist()
        await message_obj.reply(
            message("Retention updated", "clock")
            .line(f"{days} day(s)" if days else "kept forever", raw=True)
            .build()
        )

    # -- reports -----------------------------------------------------------
    async def cmd_usage(self, message_obj: Message) -> None:
        vault = self.vault()
        owner = self._owner(message_obj)
        await message_obj.reply(
            message("Your vault", "stats")
            .key_value("Files", str(vault.count(owner)), "folder")
            .key_value("Used", human_size(vault.used(owner)), "database")
            .key_value("Quota", human_size(vault.per_user_quota), "stats")
            .key_value("Shared vault", f"{human_size(vault.used())} / {human_size(vault.global_quota)}", "server")
            .key_value("Deliver as", " · ".join(encoding_choices()), "download")
            .build()
        )

    async def cmd_stats(self, message_obj: Message) -> None:
        vault = self.vault()
        await message_obj.reply(
            message("Vault stats", "stats")
            .key_value("Files", str(vault.count()), "folder")
            .key_value("Members", str(len({f.owner_id for f in vault.files.values()})), "users")
            .key_value("Stored", human_size(vault.used()), "database")
            .key_value("Collections", str(len(vault.collections)), "folder")
            .key_value("Share links", str(len(self.links().links)), "link")
            .key_value("Tags", ", ".join(vault.tags()[:8]) or "—", "type")
            .build()
        )

    # -- callbacks ---------------------------------------------------------
    async def on_callback(self, call: CallbackQuery) -> None:
        ctx = self.context
        if ctx is None or call.message is None:
            return
        parts = (call.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        key = parts[2] if len(parts) > 2 else ""
        owner = call.from_user.id if call.from_user else 0
        if action == "needfile":
            await call.answer("Use /share <id> to create a link.", show_alert=True)
            return
        if action == "usage":
            await call.answer()
            await call.message.answer(
                message("Your vault", "stats")
                .key_value("Used", human_size(self.vault().used(owner)), "database")
                .key_value("Quota", human_size(self.vault().per_user_quota), "stats")
                .build()
            )
            return
        entry = self.vault().get(key, user_id=owner)
        if entry is None:
            await call.answer("That file is gone.", show_alert=True)
            return
        if action == "share":
            link = self.links().add(issue(entry.file_id or entry.id, entry.owner_id, self.links().secret))
            await self.persist()
            bot_username = getattr(ctx.bot, "username", "") if ctx.bot else ""
            await call.answer("Link created", show_alert=True)
            await call.message.answer(f"<code>{esc(start_deep_link(bot_username or 'meiko_bot', link.token))}</code>")
            return
        if action == "get":
            await call.answer("Sending…")
            await self._deliver(call.message, entry.id)
            return
        if action == "tags":
            await call.answer()
            await call.message.answer(f"Tags: {' '.join('#' + t for t in entry.tags) or 'none'}")
            return
        if action == "del":
            self.vault().remove(entry.id, owner_id=owner)
            self.links().revoke_file(entry.file_id or entry.id)
            await self.persist()
            await call.answer("Deleted")
            await call.message.edit_text(message("Deleted", "trash").line(esc(entry.name), raw=True).build())
            return
        if action == "page":
            await call.answer()
        else:
            await call.answer("Unknown control.")

    # -- deep-link resolution (``/start s_<token>``) -----------------------
    async def redeem(self, message_obj: Message, payload: str) -> bool:
        """Fetch a shared file from a ``/start s_<token>`` payload."""
        token = parse_start_payload(payload)
        if not token:
            return False
        link, reason = self.links().resolve(token)
        if link is None:
            await message_obj.reply(message("Link unusable", "warn").line(esc(reason), raw=True).build())
            return True
        entry = next(
            (f for f in self.vault().files.values() if (f.file_id or f.id) == link.file_id),
            None,
        )
        if entry is None:
            await message_obj.reply(message("File gone", "warn").line("The owner removed it.", raw=True).build())
            return True
        self.links().consume(link)
        await self.persist()
        await self._deliver(message_obj, entry.id)
        return True


__all__ = ["FileStorePlugin", "OWN_COMMAND", "file_extension"]
