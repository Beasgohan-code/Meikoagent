"""``post_maker`` handlers: compose, preview, publish and schedule posts."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import (
    OwnCommand,
    PluginState,
)
from meiko.ui import esc, message

from .manifest import MANIFEST
from .scheduler import PostQueue, ScheduledPost, format_when, now_utc, parse_when
from .templates import (
    PostTemplate,
    parse_buttons,
    placeholders_used,
    preview_text,
    render_post,
    suggest_fill,
    validate_html,
)

TEMPLATE_KEY = "template:active"
QUEUE_KEY = "queue:posts"
USAGE = (
    "<b>Post maker</b>\n"
    "<code>/post Title | description</code> – compose now\n"
    "<code>/postpreview</code> – preview the current template\n"
    "<code>/posttemplate &lt;html&gt;</code> – set the body template\n"
    "<code>/postbuttons Label | https://url</code> – replace buttons\n"
    "<code>/schedule +10m Title | description</code> – queue a post\n"
    "<code>/scheduled</code> · <code>/unschedule &lt;id&gt;</code>"
)


#: Only dispatch commands this package declares – never the whole "/" space.
OWN_COMMAND = OwnCommand(MANIFEST.commands)

@plugin
class PostMakerPlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._template = PostTemplate()
        self._queue = PostQueue()
        self._state: PluginState | None = None

    async def on_start(self, ctx: PluginContext) -> None:
        self._state = PluginState(ctx)
        stored = await self._state.get(TEMPLATE_KEY)
        if isinstance(stored, dict):
            self._template = PostTemplate.from_dict(stored)
        else:
            self._template = PostTemplate(
                body=str(self.config("default_template", PostTemplate().body)),
                footer=str(self.config("footer", "")),
            )
        self._queue = PostQueue(
            await self._state.get(QUEUE_KEY), limit=int(self.config("queue_limit", 50))
        )
        router = Router(name="plugin:post_maker")
        router.message.register(self.on_message, F.text, OWN_COMMAND)
        ctx.add_router(router)
        ctx.schedule("post_queue", 30, self.flush_queue, run_immediately=True)

    async def on_stop(self, ctx: PluginContext) -> None:
        self._queue = PostQueue()

    # -- routing ---------------------------------------------------------
    async def on_message(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        if not text.startswith("/"):
            return
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        routes = {
            "/post": self.cmd_post,
            "/postpreview": self.cmd_preview,
            "/posttemplate": self.cmd_template,
            "/postbuttons": self.cmd_buttons,
            "/schedule": self.cmd_schedule,
            "/scheduled": self.cmd_scheduled,
            "/unschedule": self.cmd_unschedule,
        }
        handler = routes.get(command)
        if handler is None:
            return
        if command in {"/post", "/posttemplate", "/postbuttons", "/schedule", "/unschedule"}:
            await handler(message_obj, argument)
        else:
            await handler(message_obj)

    # -- helpers ---------------------------------------------------------
    def markup(self) -> InlineKeyboardMarkup | None:
        rows = [
            [
                InlineKeyboardButton(text=button.text, url=button.url)
                for button in row
            ]
            for row in self._template.markup_rows()
        ]
        rows = [row for row in rows if row]
        if not rows:
            return None
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def build_text(self, argument: str) -> str:
        values = suggest_fill(self._template)
        if argument:
            title, _, description = argument.partition("|")
            values["title"] = title.strip() or values["title"]
            values["description"] = description.strip() or values["description"]
        if self.config("signature"):
            values["channel"] = str(self.config("signature"))
        return render_post(self._template, values)

    async def persist(self) -> None:
        ctx = self.context
        if ctx is None:
            return
        state = self._state or PluginState(ctx)
        await state.set(TEMPLATE_KEY, self._template.to_dict())
        await state.set(QUEUE_KEY, self._queue.to_list())

    # -- commands --------------------------------------------------------
    async def cmd_post(self, message_obj: Message, argument: str) -> None:
        ctx = self.context
        assert ctx is not None
        text = self.build_text(argument)
        report = validate_html(text)
        if not report.ok:
            await message_obj.reply(
                f"<b>Post rejected</b>: {esc('; '.join(report.errors))}\n\n"
                f"<code>{esc(text[:500])}</code>"
            )
            return
        try:
            await ctx.bot.send_message(message_obj.chat.id, text, reply_markup=self.markup())
        except Exception as exc:  # pragma: no cover - telegram rejections
            await message_obj.reply(f"<b>Telegram refused the post</b>: {esc(str(exc))}")
            return
        await message_obj.reply(message("Post published", "check").build())

    async def cmd_preview(self, message_obj: Message) -> None:
        text = self.build_text("")
        report = validate_html(text)
        preview = preview_text(self._template, suggest_fill(self._template))
        builder = (
            message("Post preview", "eye")
            .key_value("Placeholders", ", ".join(sorted(placeholders_used(self._template))) or "none", "code")
            .key_value("Validation", report.summary(), "check" if report.ok else "warn")
            .blank()
            .code(preview[:900])
        )
        await message_obj.reply(builder.build())

    async def cmd_template(self, message_obj: Message, argument: str) -> None:
        if not argument:
            await message_obj.reply(USAGE)
            return
        report = validate_html(argument)
        if not report.ok:
            await message_obj.reply(f"<b>Template rejected</b>: {esc('; '.join(report.errors))}")
            return
        self._template.body = argument
        await self.persist()
        await message_obj.reply(
            message("Template saved", "check")
            .key_value("Placeholders", ", ".join(sorted(placeholders_used(self._template))), "code")
            .key_value("Warnings", report.summary() if report.warnings else "none", "warn")
            .build()
        )

    async def cmd_buttons(self, message_obj: Message, argument: str) -> None:
        if not argument or argument.lower() in ("clear", "none", "off"):
            self._template.buttons = []
            await self.persist()
            await message_obj.reply("Buttons cleared.")
            return
        limit = int(self.config("max_buttons", 4))
        buttons = parse_buttons(argument)[:limit]
        if not buttons:
            await message_obj.reply("No valid buttons found. Format: <code>Label | https://url</code>")
            return
        self._template.buttons = buttons
        await self.persist()
        await message_obj.reply(
            message("Buttons saved", "link")
            .line("\n".join(f"• {esc(button.text)} → {esc(button.url)}" for button in buttons), raw=True)
            .build()
        )

    async def cmd_schedule(self, message_obj: Message, argument: str) -> None:
        if not bool(self.config("allow_scheduling", True)):
            await message_obj.reply("Scheduling is disabled by the owner.")
            return
        when_text, _, payload = argument.partition(" ")
        moment = parse_when(when_text)
        if moment is None:
            await message_obj.reply(
                "Usage: <code>/schedule +10m Title | description</code>\n"
                "Also accepts <code>2026-01-31T18:00</code> (UTC)."
            )
            return
        text = self.build_text(payload.strip())
        report = validate_html(text)
        if not report.ok:
            await message_obj.reply(f"<b>Post rejected</b>: {esc('; '.join(report.errors))}")
            return
        post = ScheduledPost(
            id=uuid.uuid4().hex[:8],
            chat_id=message_obj.chat.id,
            text=text,
            send_at=moment.isoformat(timespec="seconds"),
            buttons=[button.to_dict() for button in self._template.buttons],
            author_id=message_obj.from_user.id if message_obj.from_user else 0,
        )
        if not self._queue.add(post):
            await message_obj.reply("The schedule queue is full – cancel something first.")
            return
        await self.persist()
        await message_obj.reply(
            message("Post scheduled", "clock")
            .key_value("When", format_when(moment), "clock")
            .key_value("Queue", f"{len(self._queue.pending)} pending", "database")
            .key_value("ID", post.id, "code")
            .build()
        )

    async def cmd_scheduled(self, message_obj: Message) -> None:
        upcoming = self._queue.upcoming(10)
        builder = message("Scheduled posts", "clock")
        if not upcoming:
            builder.line("Nothing scheduled.", raw=True)
        for post in upcoming:
            builder.bullet(f"<code>{esc(post.id)}</code> · {format_when(post.when)} · {esc(post.text[:40])}")
        await message_obj.reply(builder.build())

    async def cmd_unschedule(self, message_obj: Message, argument: str) -> None:
        if not argument:
            await message_obj.reply("Usage: <code>/unschedule &lt;id&gt;</code>")
            return
        if self._queue.cancel(argument.strip()):
            await self.persist()
            await message_obj.reply(f"Cancelled <code>{esc(argument.strip())}</code>.")
        else:
            await message_obj.reply("No pending post with that id.")

    # -- scheduled job ---------------------------------------------------
    async def flush_queue(self) -> None:
        ctx = self.context
        if ctx is None:
            return
        due = self._queue.due()
        if not due:
            return
        for post in due:
            markup = None
            if post.buttons:
                rows = [
                    [InlineKeyboardButton(text=item["text"], url=item["url"]) for item in post.buttons[index : index + 2]]
                    for index in range(0, len(post.buttons), 2)
                ]
                markup = InlineKeyboardMarkup(inline_keyboard=rows)
            try:
                await ctx.bot.send_message(post.chat_id, post.text, reply_markup=markup)
                post.sent = True
                post.sent_at = now_utc().isoformat(timespec="seconds")
            except Exception as exc:  # pragma: no cover - telegram failures
                post.error = str(exc)[:200]
                ctx.log("warning", f"post {post.id} failed: {exc}")
        await self.persist()


__all__ = ["PostMakerPlugin", "MANIFEST", "USAGE"]
