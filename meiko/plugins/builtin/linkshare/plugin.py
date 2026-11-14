"""``linkshare`` handlers — protected links, join gates and approvals.

Commands::

    /protect <url> [hours] [claims]  protect a link (admins)
    /unprotect <code|url>            delete a protected link
    /require <chat_id|@username>     add a chat viewers must join
    /unrequire <chat_id>             remove that requirement
    /links [page]                    list protected links
    /getlink <code>                  release a link (runs the gate)
    /verifylink                      re-check membership for the button flow
    /approve <user_id> /decline <user_id>   join requests
    /pendinglinks                    who is waiting for approval
    /linkmode join|request|open      gate policy
    /linkstats                       counts and outcomes
"""

from __future__ import annotations

import time

import re

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import OwnCommand, PluginState
from meiko.security.crypto import signing_secret
from meiko.ui import Btn, esc, keyboard, message

from .gate import (
    BLOCKED_STATUSES,
    LinkRegistry,
    MEMBER_STATUSES,
    ProtectedLink,
    RequiredChat,
    Verdict,
    code_for,
    evaluate,
    token_for,
    verdict_copy,
)
from .manifest import MANIFEST

OWN_COMMAND = OwnCommand(MANIFEST.commands)
URL_RE = re.compile(r"https?://(?:t\.me|telegram\.me)/[\w/+?=&%-]+", re.I)
MODES = {"join", "request", "open"}
PER_PAGE = 6


@plugin
class LinkSharePlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._registry: LinkRegistry | None = None

    # -- lifecycle ---------------------------------------------------------
    async def on_start(self, ctx: PluginContext) -> None:
        # Without a configured secret this used to be ``"<scope>:linkshare"`` -- a
        # string anybody can recompute -- and the verify button is authenticated
        # only by an HMAC over ``code:user_id``.  Fall back to the installation key.
        secret = str(self.config("link_secret", "") or signing_secret("linkshare-plugin"))
        self._registry = await LinkRegistry(PluginState(ctx), secret).load()
        self._mode = str(self.config("linkmode", "join"))
        router = Router(name="plugin:linkshare")
        router.message.register(self.on_message, F.text.startswith("/"), OWN_COMMAND)
        router.callback_query.register(self.on_callback, F.data.startswith("pl:"))
        router.chat_member.register(self.on_chat_member)
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        if self._registry is not None:
            await self._registry.save()
        self._registry = None

    def registry(self) -> LinkRegistry:
        if self._registry is None:
            raise RuntimeError("linkshare is not running")
        return self._registry

    async def persist(self) -> None:
        if self.context is not None and self._registry is not None:
            await self._registry.save()

    # -- dispatch ----------------------------------------------------------
    async def on_message(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        routes = {
            "/protect": self.cmd_protect,
            "/unprotect": self.cmd_unprotect,
            "/require": self.cmd_require,
            "/unrequire": self.cmd_unrequire,
            "/links": self.cmd_links,
            "/getlink": self.cmd_getlink,
            "/verifylink": self.cmd_getlink,
            "/approve": self.cmd_approve,
            "/decline": self.cmd_decline,
            "/pendinglinks": self.cmd_pending,
            "/linkmode": self.cmd_mode,
            "/linkstats": self.cmd_stats,
        }
        handler = routes.get(command)
        if handler is None:
            return
        if command in {"/protect", "/unprotect", "/require", "/unrequire", "/getlink", "/verifylink",
                       "/approve", "/decline", "/linkmode", "/links"}:
            await handler(message_obj, argument)
        else:
            await handler(message_obj)

    def _is_admin(self, message_obj: Message) -> bool:
        """In private chats every owner-scoped command is allowed; groups defer to
        the platform's permission layer, which already filtered these commands."""
        return True if message_obj.chat.type == "private" else bool(getattr(message_obj.from_user, "id", 0))

    # -- protecting --------------------------------------------------------
    async def cmd_protect(self, message_obj: Message, argument: str) -> None:
        match = URL_RE.search(argument) or URL_RE.search(message_obj.text or "")
        if match is None:
            await message_obj.reply(
                message("Usage", "warn")
                .line("<code>/protect https://t.me/private/12 [hours] [claims]</code>", raw=True)
                .build()
            )
            return
        url = match.group(0)
        numbers = [int(tok) for tok in argument.split() if tok.isdigit()]
        hours = numbers[0] if numbers else int(self.config("default_ttl_hours", 24))
        claims = numbers[1] if len(numbers) > 1 else int(self.config("max_claims", 0))
        registry = self.registry()
        link = registry.add(
            ProtectedLink(
                code=code_for(url),
                url=url,
                title=argument.split()[0] if argument and not URL_RE.match(argument.split()[0]) else "",
                created_at=int(time.time()),
                expires_at=int(time.time() + hours * 3600) if hours else 0,
                max_claims=claims,
                enabled=True,
            )
        )
        if bool(self.config("require_join", True)) and not link.required:
            # default requirement: this chat, so the gate is never a no-op
            link.required.append(
                RequiredChat(
                    chat_id=message_obj.chat.id,
                    title=getattr(message_obj.chat, "title", "") or "this chat",
                    request_mode=str(self.config("request_mode", False)).lower() == "true",
                )
            )
        await self.persist()
        await message_obj.reply(
            message("Link protected", "lock")
            .key_value("Code", link.code, "id")
            .key_value("Claims", str(claims) if claims else "unlimited", "stats")
            .key_value("Expires", f"{hours}h" if hours else "never", "clock")
            .key_value("Required chats", str(len(link.required)), "users")
            .line("Share with <code>/getlink " + link.code + "</code>.", raw=True)
            .build(),
            reply_markup=keyboard(
                [
                    [Btn("Release now", callback_data=f"pl:get:{link.code}", emoji="link"),
                     Btn("Stats", callback_data="pl:stats", emoji="stats")],
                    [Btn("Approvals", callback_data="pl:pending", emoji="users")],
                ]
            ),
        )

    async def cmd_unprotect(self, message_obj: Message, argument: str) -> None:
        key = argument.strip()
        if not key:
            await message_obj.reply("Usage: <code>/unprotect &lt;code&gt;</code>")
            return
        link = self.registry().get(key)
        removed = self.registry().remove(link.code if link else key)
        await self.persist()
        await message_obj.reply(
            message("Removed" if removed else "Not found", "trash" if removed else "search").build()
        )

    async def cmd_require(self, message_obj: Message, argument: str) -> None:
        parts = argument.split()
        if len(parts) < 2:
            await message_obj.reply(
                "Usage: <code>/require &lt;code&gt; &lt;chat_id or @username&gt; [title]</code>"
            )
            return
        link = self.registry().get(parts[0])
        if link is None:
            await message_obj.reply(message("No such link", "search").build())
            return
        target = parts[1]
        chat_id = int(target) if target.lstrip("-").isdigit() else 0
        title = " ".join(parts[2:]) or target
        invite = ""
        if chat_id:
            try:
                invite = (await self.context.bot.create_chat_invite_link(chat_id)).invite_link or ""
            except Exception:  # pragma: no cover - bot may not be an admin
                invite = ""
        link.required.append(
            RequiredChat(
                chat_id=chat_id,
                title=title,
                invite_link=invite,
                request_mode=str(self.config("request_mode", False)).lower() == "true",
            )
        )
        await self.persist()
        await message_obj.reply(
            message("Requirement added", "users")
            .key_value("Link", link.code, "id")
            .key_value("Chat", title, "message")
            .key_value("Invite", invite or "unavailable (bot needs admin rights)", "link")
            .build()
        )

    async def cmd_unrequire(self, message_obj: Message, argument: str) -> None:
        parts = argument.split()
        if len(parts) < 2:
            await message_obj.reply("Usage: <code>/unrequire &lt;code&gt; &lt;chat_id&gt;</code>")
            return
        link = self.registry().get(parts[0])
        if link is None:
            await message_obj.reply(message("No such link", "search").build())
            return
        chat_id = int(parts[1]) if parts[1].lstrip("-").isdigit() else 0
        before = len(link.required)
        link.required = [chat for chat in link.required if chat.chat_id != chat_id]
        await self.persist()
        await message_obj.reply(
            message("Requirement removed", "check").line(f"{before - len(link.required)} removed.", raw=True).build()
        )

    # -- releasing ---------------------------------------------------------
    async def cmd_getlink(self, message_obj: Message, argument: str) -> None:
        key = argument.strip()
        link = self.registry().get(key) if key else None
        if link is None:
            # no key given: use the newest link mentioned in the replied message
            replied = message_obj.reply_to_message
            if replied is not None:
                match = URL_RE.search(replied.text or replied.caption or "")
                if match is not None:
                    link = self.registry().get(match.group(0))
        if link is None:
            await message_obj.reply("Usage: <code>/getlink &lt;code&gt;</code>")
            return
        user_id = message_obj.from_user.id if message_obj.from_user else 0
        facts = await self._facts(link, user_id, message_obj)
        verdict, detail = evaluate(link, facts)
        if verdict is Verdict.ALLOW:
            self.registry().claim(link)
            await self.persist()
            await message_obj.reply(
                message("Here you go", "link").line(f"<code>{esc(link.url)}</code>", raw=True).build(),
                reply_markup=keyboard([[Btn("Open", url=link.url, emoji="link")]]),
            )
            return
        await message_obj.reply(
            self._verdict_markup(verdict, detail, link, user_id)
        )

    async def _facts(self, link: ProtectedLink, user_id: int, message_obj: Message):
        registry = self.registry()
        statuses: dict[int, str] = {}
        for chat in link.required:
            if not chat.chat_id:
                continue
            try:
                member = await self.context.bot.get_chat_member(chat.chat_id, user_id)
                statuses[chat.chat_id] = str(getattr(member, "status", "left")).lower()
            except Exception:  # pragma: no cover - bot not in that chat
                statuses[chat.chat_id] = "left"
        return registry.facts_for(link, user_id, statuses, is_admin=message_obj.chat.type == "private")

    def _verdict_markup(self, verdict: Verdict, detail: str, link: ProtectedLink, user_id: int):
        from meiko.ui import message as build

        builder = build("Locked", "lock")
        builder.line(esc(verdict_copy(verdict, detail, link)), raw=True)
        builder.key_value("Verdict", verdict.value, "warn" if verdict is not Verdict.ALLOW else "check")
        rows = []
        for chat in link.required:
            if chat.invite_link:
                rows.append([Btn(f"Join {chat.title or chat.chat_id}", url=chat.invite_link, emoji="join")])
        if verdict in (Verdict.JOIN, Verdict.REQUEST, Verdict.PENDING):
            token = token_for(link, user_id, self.registry().secret)
            rows.append([Btn("Verify", callback_data=f"pl:verify:{link.code}:{token}", emoji="check")])
        return builder.build(), keyboard(rows) if rows else None

    # -- approvals ---------------------------------------------------------
    async def cmd_approve(self, message_obj: Message, argument: str) -> None:
        target = argument.strip()
        if not target.isdigit() and message_obj.reply_to_message is not None:
            target = str(getattr(message_obj.reply_to_message.from_user, "id", ""))
        if not target.isdigit():
            await message_obj.reply("Usage: <code>/approve &lt;user_id&gt;</code> or reply to them.")
            return
        self.registry().approve(int(target))
        await self.persist()
        await message_obj.reply(
            message("Approved", "check").key_value("User", target, "user").build()
        )

    async def cmd_decline(self, message_obj: Message, argument: str) -> None:
        target = argument.strip()
        if not target.isdigit() and message_obj.reply_to_message is not None:
            target = str(getattr(message_obj.reply_to_message.from_user, "id", ""))
        if not target.isdigit():
            await message_obj.reply("Usage: <code>/decline &lt;user_id&gt;</code>")
            return
        if not self.registry().decline(int(target)):
            await message_obj.reply(message("No pending request from that user", "search").build())
            return
        await self.persist()
        await message_obj.reply(message("Declined", "cross").key_value("User", target, "user").build())

    async def cmd_pending(self, message_obj: Message) -> None:
        registry = self.registry()
        builder = message("Pending approvals", "users")
        if not registry.pending:
            builder.line("Nobody is waiting.", raw=True)
        for user_id in sorted(registry.pending)[:20]:
            builder.line(f"<code>{user_id}</code>", raw=True)
            builder.line(f"<code>/approve {user_id}</code> · <code>/decline {user_id}</code>", raw=True)
        await message_obj.reply(builder.build())

    # -- listing / policy / stats -------------------------------------------
    async def cmd_links(self, message_obj: Message, argument: str = "") -> None:
        page = int(argument) if argument.strip().isdigit() else 1
        registry = self.registry()
        links = sorted(registry.links.values(), key=lambda link: link.created_at, reverse=True)
        total = len(links)
        window = links[(page - 1) * PER_PAGE : page * PER_PAGE]
        builder = message("Protected links", "lock")
        builder.key_value("Total", str(total), "stats")
        builder.key_value("Pending viewers", str(len(registry.pending)), "users")
        builder.blank()
        if not window:
            builder.line("Nothing protected yet — <code>/protect &lt;url&gt;</code>.", raw=True)
        for link in window:
            state = "off" if not link.enabled else "expired" if link.expired else f"{link.claims}/{link.max_claims or '∞'}"
            builder.line(
                f"<code>{esc(link.code)}</code> · {esc(link.url[:48])}\n"
                f"    claims {state} · {len(link.required)} gate(s)",
                raw=True,
            )
        pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        if pages > 1:
            builder.line(f"<i>page {page}/{pages}</i>", raw=True)
        nav = []
        if page > 1:
            nav.append(Btn("Prev", callback_data=f"pl:page:{page - 1}", emoji="arrow"))
        if page < pages:
            nav.append(Btn("Next", callback_data=f"pl:page:{page + 1}", emoji="arrow"))
        await message_obj.reply(
            builder.build(),
            reply_markup=keyboard([nav, [Btn("Pending", callback_data="pl:pending", emoji="users")]] if nav else [[Btn("Pending", callback_data="pl:pending", emoji="users")]]),
        )

    async def cmd_mode(self, message_obj: Message, argument: str) -> None:
        mode = argument.strip().lower()
        if mode not in MODES:
            await message_obj.reply(
                message("Gate policy", "gear")
                .key_value("Current", getattr(self, "_mode", "join"), "check")
                .line(" · ".join(sorted(MODES)), raw=True)
                .line("join = must be a member · request = admin approval · open = no gate", raw=True)
                .build()
            )
            return
        self._mode = mode
        await self.persist()
        await message_obj.reply(message(f"Gate policy · {mode}", "gear").build())

    async def cmd_stats(self, message_obj: Message) -> None:
        registry = self.registry()
        links = list(registry.links.values())
        await message_obj.reply(
            message("Link stats", "stats")
            .key_value("Links", str(len(links)), "link")
            .key_value("Claims", str(sum(link.claims for link in links)), "download")
            .key_value("Expired", str(sum(1 for link in links if link.expired)), "clock")
            .key_value("Disabled", str(sum(1 for link in links if not link.enabled)), "ban")
            .key_value("Approved", str(len(registry.approved)), "check")
            .key_value("Pending", str(len(registry.pending)), "loading")
            .build()
        )

    # -- events ------------------------------------------------------------
    async def on_chat_member(self, event: ChatMemberUpdated, **_: object) -> None:
        if self._registry is None:
            return
        status = str(getattr(event.new_chat_member, "status", "")).lower()
        user_id = event.new_chat_member.user.id
        if status in MEMBER_STATUSES:
            # an approval only matters while the user is in the chat; a join
            # clears a stale pending request so the gate re-evaluates cleanly
            self._registry.pending.discard(user_id)
            return
        if status in BLOCKED_STATUSES:
            self._registry.approved.discard(user_id)

    async def on_callback(self, call: CallbackQuery) -> None:
        ctx = self.context
        if ctx is None or call.message is None:
            return
        parts = (call.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        registry = self.registry()
        if action == "pending":
            await call.answer()
            await call.message.answer(
                message("Pending", "users").line(
                    ", ".join(str(u) for u in sorted(registry.pending)[:20]) or "Nobody is waiting.", raw=True
                ).build()
            )
            return
        if action == "stats":
            await call.answer()
            await self.cmd_stats(call.message)
            return
        if action == "page" and len(parts) > 2:
            await call.answer()
            await self.cmd_links(call.message, parts[2])
            return
        if action == "get" and len(parts) > 2:
            await call.answer()
            await self.cmd_getlink(call.message, parts[2])
            return
        if action == "verify" and len(parts) > 3:
            code, token = parts[2], parts[3]
            link = registry.get(code)
            user_id = call.from_user.id if call.from_user else 0
            if link is None:
                await call.answer("That link is gone.", show_alert=True)
                return
            if token != token_for(link, user_id, registry.secret):
                await call.answer("This button is not yours.", show_alert=True)
                return
            facts = await self._facts(link, user_id, call.message)
            verdict, detail = evaluate(link, facts)
            if verdict is Verdict.ALLOW:
                registry.claim(link)
                await self.persist()
                await call.answer("Unlocked")
                await call.message.answer(f"<code>{esc(link.url)}</code>")
                return
            if verdict in (Verdict.REQUEST, Verdict.PENDING):
                registry.request(user_id)
            await self.persist()
            await call.answer(verdict_copy(verdict, detail, link)[:190], show_alert=True)
            return
        await call.answer("Unknown control.")


__all__ = ["LinkSharePlugin", "OWN_COMMAND"]
