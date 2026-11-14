"""Executes moderation actions against Telegram and records the audit trail.

Telegram permissions, rate limits and API restrictions are respected: every
call is wrapped in :func:`meiko.telegram.client.with_retry`, insufficient-rights
errors are downgraded to a logged warning and never re-raised into the
dispatcher loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions

from meiko.logging_setup import get_logger
from meiko.moderation.events import ModerationEvent
from meiko.moderation.rules import ACTION_KINDS, Rule
from meiko.services.core import ModerationService
from meiko.telegram.client import with_retry

log = get_logger("moderation.executor")


class AuditSink(Protocol):
    async def record(self, **kwargs: Any) -> Any: ...


MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

UNMUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


def expiry(duration: int) -> datetime | None:
    return datetime.now(timezone.utc) + timedelta(seconds=duration) if duration else None


class ModerationExecutor:
    """Bridges rule actions to the Telegram API and the moderation journal."""

    def __init__(
        self,
        bot: Bot,
        bot_id: int,
        audit: ModerationService | AuditSink,
        notifier: Any | None = None,
    ) -> None:
        self.bot = bot
        self.bot_id = bot_id
        self.audit = audit
        self.notifier = notifier

    async def execute(
        self, action: dict[str, Any], event: ModerationEvent, rule: Rule | None = None
    ) -> dict[str, Any]:
        kind = action.get("type")
        if kind not in ACTION_KINDS:
            return {"action": kind, "ok": False, "error": "unknown action"}
        handler = getattr(self, f"_do_{kind}", None)
        if handler is None:
            return {"action": kind, "ok": False, "error": "action not implemented"}
        try:
            result = await handler(action, event, rule)
            result.setdefault("action", kind)
            result.setdefault("ok", True)
            return result
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            log.warning("moderation action %s failed: %s", kind, exc)
            return {"action": kind, "ok": False, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected moderation failure for %s", kind)
            return {"action": kind, "ok": False, "error": str(exc)}

    # -- primitives ------------------------------------------------------
    async def _record(self, action: str, event: ModerationEvent, **extra: Any) -> None:
        try:
            await self.audit.record(
                self.bot_id,
                action,
                group_id=event.group_id,
                chat_id=event.chat_id,
                target_id=event.user_id,
                moderator_id=extra.pop("moderator_id", None),
                source=extra.pop("source", "automation"),
                **extra,
            )
        except Exception:  # pragma: no cover - audit must never break moderation
            log.exception("failed to record moderation action %s", action)

    # -- actions ---------------------------------------------------------
    async def _do_warn(self, action, event, rule=None):
        await self._record("warn", event, reason=action.get("reason", ""))
        if self.notifier and event.chat_id:
            await self.notifier(
                event.chat_id,
                f"⚠️ Warning issued to user {event.user_id}: {action.get('reason', 'rule violation')}",
                event.message_thread_id,
            )
        return {"warned": event.user_id}

    async def _do_clear_warnings(self, action, event, rule=None):
        await self._record("unwarn", event, reason="rule cleared warnings")
        return {"cleared": event.user_id}

    async def _do_mute(self, action, event, rule=None):
        duration = int(action.get("duration") or action.get("mute_seconds") or 300)
        await with_retry(
            lambda: self.bot.restrict_chat_member(
                chat_id=event.chat_id,
                user_id=event.user_id,
                permissions=MUTE_PERMISSIONS,
                until_date=expiry(duration),
            )
        )
        await self._record(
            "mute", event, reason=action.get("reason", ""), duration_seconds=duration
        )
        return {"muted": event.user_id, "duration": duration}

    async def _do_unmute(self, action, event, rule=None):
        await with_retry(
            lambda: self.bot.restrict_chat_member(
                chat_id=event.chat_id,
                user_id=event.user_id,
                permissions=UNMUTE_PERMISSIONS,
            )
        )
        await self._record("unmute", event, reason=action.get("reason", ""))
        return {"unmuted": event.user_id}

    async def _do_restrict(self, action, event, rule=None):
        duration = int(action.get("duration") or 0)
        await with_retry(
            lambda: self.bot.restrict_chat_member(
                chat_id=event.chat_id,
                user_id=event.user_id,
                permissions=MUTE_PERMISSIONS,
                until_date=expiry(duration) if duration else None,
            )
        )
        await self._record(
            "restrict", event, reason=action.get("reason", ""), duration_seconds=duration
        )
        return {"restricted": event.user_id}

    async def _do_kick(self, action, event, rule=None):
        await with_retry(
            lambda: self.bot.ban_chat_member(
                chat_id=event.chat_id, user_id=event.user_id, revoke_messages=False
            )
        )
        await with_retry(
            lambda: self.bot.unban_chat_member(
                chat_id=event.chat_id, user_id=event.user_id, only_if_banned=True
            )
        )
        await self._record("kick", event, reason=action.get("reason", ""))
        return {"kicked": event.user_id}

    async def _do_ban(self, action, event, rule=None):
        duration = int(action.get("duration") or 0)
        await with_retry(
            lambda: self.bot.ban_chat_member(
                chat_id=event.chat_id,
                user_id=event.user_id,
                until_date=expiry(duration) if duration else None,
            )
        )
        await self._record(
            "ban", event, reason=action.get("reason", ""), duration_seconds=duration
        )
        return {"banned": event.user_id}

    async def _do_unban(self, action, event, rule=None):
        await with_retry(
            lambda: self.bot.unban_chat_member(
                chat_id=event.chat_id, user_id=event.user_id, only_if_banned=True
            )
        )
        await self._record("unban", event, reason=action.get("reason", ""))
        return {"unbanned": event.user_id}

    async def _do_delete(self, action, event, rule=None):
        if not event.message_id:
            return {"deleted": False, "reason": "no message"}
        await with_retry(
            lambda: self.bot.delete_message(chat_id=event.chat_id, message_id=event.message_id)
        )
        await self._record("delete", event, reason=action.get("reason", ""))
        return {"deleted": event.message_id}

    async def _do_purge(self, action, event, rule=None):
        count = int(action.get("count") or 10)
        top = event.message_id or 0
        deleted = 0
        for message_id in range(max(1, top - count + 1), top + 1):
            try:
                await self.bot.delete_message(chat_id=event.chat_id, message_id=message_id)
                deleted += 1
            except TelegramBadRequest:
                continue
        await self._record("purge", event, reason=action.get("reason", ""), meta={"deleted": deleted})
        return {"purged": deleted}

    async def _do_notify_admin(self, action, event, rule=None):
        if self.notifier and action.get("chat_id"):
            await self.notifier(
                int(action["chat_id"]),
                action.get("text", f"Moderation event: {action.get('reason', '')}"),
            )
        return {"notified": True}

    async def _do_notify_user(self, action, event, rule=None):
        if event.user_id:
            try:
                await with_retry(
                    lambda: self.bot.send_message(
                        chat_id=event.user_id, text=action.get("text", "Notice from Meiko Agent")
                    )
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                return {"notified": False, "reason": "user unreachable"}
        return {"notified": True}

    async def _do_reply(self, action, event, rule=None):
        if event.chat_id:
            await with_retry(
                lambda: self.bot.send_message(
                    chat_id=event.chat_id,
                    text=action.get("text", ""),
                    message_thread_id=event.message_thread_id,
                )
            )
        return {"replied": True}

    async def _do_log(self, action, event, rule=None):
        await self._record(
            "log",
            event,
            reason=action.get("text", action.get("reason", "")),
            source=action.get("source", "automation"),
        )
        return {"logged": True}

    async def _do_escalate(self, action, event, rule=None):
        await self._record(
            "escalate", event, reason=action.get("reason", "escalation"), source="automation"
        )
        return {"escalated": True}
