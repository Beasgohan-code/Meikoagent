"""Automation plugin: declarative workflows (trigger → conditions → actions)."""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from meiko.logging_setup import get_logger
from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.ui import message

log = get_logger("plugin.automation")


ALLOWED_ACTIONS = {"send_message", "delete_message", "pin_message", "notify_admins", "log", "tag_user"}

from .manifest import MANIFEST


@plugin
class AutomationPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:automation")
        router.message.register(self.on_message, F.chat.type.in_({"group", "supergroup"}))
        ctx.add_router(router)

    # -- workflow storage ------------------------------------------------
    async def list_workflows(self, group_key: str) -> list[dict[str, Any]]:
        assert self.context is not None
        return list(await self.context.get_state(f"workflows:{group_key}", []) or [])

    async def save_workflow(self, group_key: str, workflow: dict[str, Any]) -> dict[str, Any]:
        assert self.context is not None
        errors = validate_workflow(workflow)
        if errors:
            raise ValueError("; ".join(errors))
        workflows = await self.list_workflows(group_key)
        if len(workflows) >= int(self.config("max_workflows", 20)):
            raise ValueError("workflow limit reached")
        workflow.setdefault("id", f"wf_{len(workflows) + 1}")
        workflow.setdefault("enabled", True)
        workflows = [w for w in workflows if w.get("id") != workflow["id"]] + [workflow]
        await self.context.set_state(f"workflows:{group_key}", workflows)
        return workflow

    # -- execution -------------------------------------------------------
    async def run_matching(self, event_kind: str, chat_id: int, payload: dict[str, Any]) -> list[str]:
        ctx = self.context
        assert ctx is not None
        executed: list[str] = []
        for workflow in await self.list_workflows(str(chat_id)):
            if not workflow.get("enabled", True) or workflow.get("trigger", {}).get("event") != event_kind:
                continue
            if not conditions_match(workflow.get("conditions", []), payload):
                continue
            for action in workflow.get("actions", []):
                if bool(self.config("dry_run", False)):
                    ctx.log("info", "dry-run workflow action", workflow=workflow["id"], action=action)
                    executed.append("dry-run")
                    continue
                executed.append(await self.perform(action, chat_id, payload))
        return executed

    async def perform(self, action: dict[str, Any], chat_id: int, payload: dict[str, Any]) -> str:
        ctx = self.context
        assert ctx is not None
        kind = action.get("type")
        if kind not in ALLOWED_ACTIONS:
            return f"rejected:{kind}"
        if kind == "send_message" and action.get("text"):
            text = str(action["text"]).replace("{user}", str(payload.get("user_id", "")))
            await ctx.bot.send_message(chat_id, message().line(text).build())
            return "sent"
        if kind == "delete_message" and payload.get("message_id"):
            await ctx.bot.delete_message(chat_id, int(payload["message_id"]))
            return "deleted"
        if kind == "pin_message" and payload.get("message_id"):
            await ctx.bot.pin_chat_message(chat_id, int(payload["message_id"]))
            return "pinned"
        ctx.log("info", "workflow action recorded", action=kind, chat=chat_id)
        return kind

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or message_obj.from_user is None:
            return
        await self.run_matching(
            "message",
            message_obj.chat.id,
            {
                "user_id": message_obj.from_user.id,
                "message_id": message_obj.message_id,
                "text": message_obj.text or "",
                "has_link": "http" in (message_obj.text or ""),
            },
        )


def conditions_match(conditions: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    from meiko.moderation.rules import CONDITION_OPS

    logic = "all"
    if conditions and conditions[0].get("logic"):
        logic = conditions[0]["logic"]
    results = []
    for condition in conditions:
        fact = str(condition.get("fact", ""))
        value = payload.get(fact.split(".")[-1])
        op = CONDITION_OPS.get(condition.get("op", "eq"))
        try:
            results.append(bool(op(value, condition.get("value"))) if op else False)
        except Exception as exc:
            # A malformed comparison used to disappear: the workflow just behaved as
            # if the condition were false, with no way to tell a typo from a real
            # "no match".  Say which condition and why, once per evaluation.
            log.warning(
                "automation condition %r could not be evaluated (%s); treating as false",
                condition,
                exc,
            )
            results.append(False)
    if not results:
        return True
    return all(results) if logic != "any" else any(results)


def validate_workflow(workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    trigger = workflow.get("trigger") or {}
    if trigger.get("event") not in ("message", "join", "leave", "callback", "scheduled", "reaction"):
        errors.append("invalid trigger event")
    actions = workflow.get("actions") or []
    if not actions:
        errors.append("workflow needs at least one action")
    for action in actions:
        if action.get("type") not in ALLOWED_ACTIONS:
            errors.append(f"unsupported action {action.get('type')!r}")
    if len(str(workflow.get("name", ""))) > 120:
        errors.append("workflow name too long")
    return errors


__all__ = ["AutomationPlugin", "MANIFEST", "validate_workflow", "conditions_match"]
