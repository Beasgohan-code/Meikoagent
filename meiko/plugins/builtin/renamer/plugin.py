"""``renamer`` handlers: template management, previews and re-upload."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import FSInputFile, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import (
    OwnCommand,
    Workspace,
    human_size,
    safe_filename,
)
from meiko.ui import Btn, esc, keyboard, message

from .engine import DEFAULT_TEMPLATE, clean_text, guess_media_type, plan_rename
from .manifest import MANIFEST

MEGABYTE = 1024 * 1024
TUTORIAL = (
    "<b>Placeholders</b>\n"
    "{title} {season} {episode} {quality} {audio} {source} {group} {year} {extension} {original}\n"
    "Format specs work: <code>{season:02d}</code> → <code>05</code>\n\n"
    "<b>Examples</b>\n"
    "<code>/autorename {title} S{season:02d}E{episode:02d} [{quality}]</code>\n"
    "<code>/testrename Movie.2023.1080p.mkv</code>"
)


#: Only dispatch commands this package declares – never the whole "/" space.
OWN_COMMAND = OwnCommand(MANIFEST.commands)

@plugin
class RenamerPlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._workspace: Workspace | None = None
        self._template: str = DEFAULT_TEMPLATE

    async def on_start(self, ctx: PluginContext) -> None:
        self._template = str(self.config("template", DEFAULT_TEMPLATE))
        self._workspace = Workspace(
            MANIFEST.slug,
            scope=ctx.scope.key,
            quota_bytes=512 * MEGABYTE,
            max_file_bytes=45 * MEGABYTE,
            ttl_seconds=86400,
        )
        router = Router(name="plugin:renamer")
        router.message.register(self.on_command, F.text.startswith("/"), OWN_COMMAND)
        ctx.add_router(router)
        saved = await ctx.get_state("template")
        if isinstance(saved, str) and saved.strip():
            self._template = saved.strip()

    async def on_stop(self, ctx: PluginContext) -> None:
        self._workspace = None

    # -- helpers ---------------------------------------------------------
    def template(self) -> str:
        return self._template or str(self.config("template", DEFAULT_TEMPLATE))

    def plan(self, filename: str):
        return plan_rename(
            filename,
            self.template(),
            episode_offset=int(self.config("episode_offset", 0)),
            max_length=int(self.config("max_name_length", 120)),
            keep_extension=bool(self.config("keep_extension", True)),
        )

    async def on_command(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        if command == "/autorename":
            await self.cmd_autorename(message_obj, rest.strip())
        elif command == "/testrename":
            await self.cmd_test(message_obj, rest.strip())
        elif command == "/renamemode":
            await self.cmd_mode(message_obj, rest.strip())
        elif command == "/clean":
            await self.cmd_clean(message_obj, rest.strip())
        elif command == "/rename":
            await self.cmd_rename_attachment(message_obj, rest.strip())

    async def cmd_autorename(self, message_obj: Message, argument: str) -> None:
        ctx = self.context
        assert ctx is not None
        if not argument:
            await message_obj.reply(
                message("Renamer", "hammer")
                .key_value("Template", self.template(), "code")
                .key_value("Mode", str(self.config("mode", "manual")), "gear")
                .line("Send <code>/autorename &lt;template&gt;</code> to change it.", raw=True)
                .blank()
                .line(TUTORIAL, raw=True)
                .build(),
                reply_markup=keyboard(
                    [[Btn("Tutorial", callback_data="rename:tutorial", emoji="book"),
                      Btn("Preview", callback_data="rename:preview", emoji="eye")]]
                ),
            )
            return
        candidate = argument.strip()
        preview = plan_rename("Example.Show.S01E02.1080p.mkv", candidate)
        if preview.warnings and "invalid template" in " ".join(preview.warnings):
            await message_obj.reply(
                f"<b>Template rejected</b>: {esc(', '.join(preview.warnings))}"
            )
            return
        self._template = candidate
        await ctx.set_state("template", candidate)
        await message_obj.reply(
            message("Template updated", "check")
            .key_value("Template", candidate, "code")
            .key_value("Example", preview.renamed, "eye")
            .build()
        )

    async def cmd_test(self, message_obj: Message, argument: str) -> None:
        if not argument:
            await message_obj.reply("Usage: <code>/testrename &lt;filename&gt;</code>")
            return
        plan = self.plan(argument)
        builder = (
            message("Rename preview", "eye")
            .key_value("Original", plan.original, "upload")
            .key_value("Renamed", plan.renamed, "download")
            .key_value("Type", guess_media_type(plan.fields.get("extension", "")), "database")
        )
        if should_show_episode_fields_from(plan.fields):
            builder.key_value(
                "Episode",
                f"S{int(plan.fields.get('season', 0)):02d}E{int(plan.fields.get('episode', 0)):02d}",
                "clock",
            )
        for warning in plan.warnings:
            builder.line(f"⚠️ {esc(warning)}", raw=True)
        if bool(self.config("dry_run", False)):
            builder.line("<i>dry run is on: files are never uploaded</i>", raw=True)
        await message_obj.reply(builder.build())

    async def cmd_mode(self, message_obj: Message, argument: str) -> None:
        ctx = self.context
        assert ctx is not None
        if argument not in ("off", "auto", "manual"):
            await message_obj.reply(
                "Usage: <code>/renamemode off|auto|manual</code>\n"
                f"Current: <b>{esc(str(self.config('mode', 'manual')))}</b>"
            )
            return
        await ctx.set_state("mode", argument)
        await message_obj.reply(f"Rename mode set to <b>{esc(argument)}</b> for this scope.")

    async def cmd_clean(self, message_obj: Message, argument: str) -> None:
        if not argument:
            await message_obj.reply("Usage: <code>/clean &lt;release name&gt;</code>")
            return
        cleaned = clean_text(argument)
        await message_obj.reply(
            message("Cleaned", "spark").key_value("Result", cleaned, "code").build()
        )

    async def cmd_rename_attachment(self, message_obj: Message, argument: str) -> None:
        target = message_obj.reply_to_message or message_obj
        attachment = None
        for attribute in ("document", "video", "audio"):
            attachment = getattr(target, attribute, None)
            if attachment:
                break
        if attachment is None:
            await message_obj.reply(
                message("Renamer", "hammer")
                .line("Reply to a file with <code>/rename</code> (optionally a template).", raw=True)
                .line("Set a default with <code>/autorename &lt;template&gt;</code>.", raw=True)
                .build()
            )
            return
        if bool(self.config("dry_run", False)):
            plan = self.plan(attachment.file_name or "file.bin")
            await message_obj.reply(
                message("Dry run", "eye").key_value("Would become", plan.renamed, "download").build()
            )
            return
        ctx = self.context
        assert ctx is not None
        workspace = self._workspace
        if workspace is None:
            await message_obj.reply("Renamer is not ready yet.")
            return
        original_name = attachment.file_name or f"{attachment.file_unique_id}.bin"
        plan = plan_rename(
            original_name,
            argument.strip() or self.template(),
            episode_offset=int(self.config("episode_offset", 0)),
            max_length=int(self.config("max_name_length", 120)),
            keep_extension=bool(self.config("keep_extension", True)),
        )
        try:
            downloaded = await ctx.bot.download(attachment, destination=None)
            data = downloaded.read() if hasattr(downloaded, "read") else b""
            stored = workspace.write_bytes(safe_filename(plan.renamed), data)
        except Exception as exc:  # pragma: no cover - telegram/disk failures
            await message_obj.reply(f"<b>Rename failed</b>: {esc(str(exc))}")
            return

        send_as = str(self.config("send_as", "document"))
        if send_as == "auto":
            send_as = guess_media_type(plan.fields.get("extension", ""))
        file_input = FSInputFile(str(stored.path), filename=stored.name)
        caption = message("Renamed", "check").key_value("Name", stored.name, "download").key_value(
            "Size", human_size(stored.size), "database"
        ).build()
        try:
            if send_as == "video":
                await ctx.bot.send_video(message_obj.chat.id, file_input, caption=caption)
            elif send_as == "audio":
                await ctx.bot.send_audio(message_obj.chat.id, file_input, caption=caption)
            else:
                await ctx.bot.send_document(message_obj.chat.id, file_input, caption=caption)
        finally:
            workspace.remove(stored.name)
        if plan.warnings:
            await message_obj.reply("\n".join(f"⚠️ {esc(w)}" for w in plan.warnings))


def should_show_episode_fields_from(fields: dict) -> bool:
    """True when the parsed fields carry an episode/season number to display."""
    return bool(fields.get("season") or fields.get("episode"))


__all__ = ["RenamerPlugin", "MANIFEST", "TUTORIAL", "should_show_episode_fields_from"]
