"""Group rule engine: ``EVENT -> CONDITION -> ACTION -> AUDIT``.

Rules are data (stored per group in ``group_rules``), evaluated by this engine
and executed by an :class:`ActionExecutor`.  Nothing here talks to Telegram
directly – the executor is injected – which keeps the engine unit-testable and
lets the same rules run from the bot, a plugin or a scheduled automation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from meiko.logging_setup import get_logger
from meiko.moderation.antispam import AntiSpamEngine, AntiSpamPolicy, SpamVerdict, is_ignored
from meiko.moderation.events import ModerationEvent

log = get_logger("moderation.rules")

CONDITION_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "contains": lambda a, b: b is not None and str(b).lower() in str(a or "").lower(),
    "not_contains": lambda a, b: b is None or str(b).lower() not in str(a or "").lower(),
    "startswith": lambda a, b: str(a or "").lower().startswith(str(b or "").lower()),
    "endswith": lambda a, b: str(a or "").lower().endswith(str(b or "").lower()),
    "regex": lambda a, b: bool(re.search(str(b), str(a or ""), re.IGNORECASE)),
    "in": lambda a, b: a in (b or []),
    "not_in": lambda a, b: a not in (b or []),
    "exists": lambda a, b: a is not None,
    "is_true": lambda a, b: bool(a),
    "is_false": lambda a, b: not bool(a),
}

ACTION_KINDS = {
    "warn",
    "mute",
    "unmute",
    "kick",
    "ban",
    "unban",
    "restrict",
    "delete",
    "purge",
    "notify_admin",
    "reply",
    "notify_user",
    "log",
    "clear_warnings",
    "escalate",
}


@dataclass
class Rule:
    id: int | None
    name: str
    event: str
    conditions: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    priority: int = 100
    enabled: bool = True

    @classmethod
    def from_model(cls, model: Any) -> "Rule":
        return cls(
            id=getattr(model, "id", None),
            name=getattr(model, "name", ""),
            event=getattr(model, "event", "message"),
            conditions=list(getattr(model, "conditions", []) or []),
            actions=list(getattr(model, "actions", []) or []),
            priority=getattr(model, "priority", 100),
            enabled=getattr(model, "enabled", True),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        for condition in self.conditions:
            op = condition.get("op", "eq")
            if op not in CONDITION_OPS:
                errors.append(f"unknown condition operator {op!r}")
            if not condition.get("fact"):
                errors.append("condition is missing 'fact'")
        for action in self.actions:
            if action.get("type") not in ACTION_KINDS:
                errors.append(f"unknown action type {action.get('type')!r}")
        if not self.event:
            errors.append("rule has no event")
        return errors


@dataclass
class RuleResult:
    matched: bool
    rule: Rule | None = None
    verdicts: list[SpamVerdict] = field(default_factory=list)
    executed: list[dict[str, Any]] = field(default_factory=list)
    unmatched_reason: str = ""


def evaluate_condition(event: ModerationEvent, condition: dict[str, Any]) -> bool:
    fact_name = str(condition.get("fact", ""))
    op = condition.get("op", "eq")
    expected = condition.get("value")
    actual = event.fact(fact_name)
    comparator = CONDITION_OPS.get(op, CONDITION_OPS["eq"])
    try:
        return bool(comparator(actual, expected))
    except Exception:  # pragma: no cover - defensive against odd rule data
        log.warning("condition %s %s %r failed for fact %s", fact_name, op, expected, actual)
        return False


def evaluate_rule(event: ModerationEvent, rule: Rule) -> bool:
    if not rule.enabled:
        return False
    if rule.event not in ("*", event.kind):
        return False
    logic = rule.conditions and rule.conditions[0].get("logic", "all") or "all"
    results = [evaluate_condition(event, c) for c in rule.conditions]
    if not results:
        return True
    return all(results) if logic != "any" else any(results)


#: Executor signature: ``async def execute(action, event, rule) -> dict``
Executor = Callable[[dict[str, Any], ModerationEvent, Rule | None], Awaitable[dict[str, Any]]]


class RuleEngine:
    def __init__(
        self,
        executor: Executor,
        antispam: AntiSpamEngine | None = None,
    ) -> None:
        self.executor = executor
        self.antispam = antispam or AntiSpamEngine()

    async def process(
        self,
        event: ModerationEvent,
        *,
        rules: Sequence[Rule] = (),
        policy: AntiSpamPolicy | None = None,
        fallback_actions: Sequence[dict[str, Any]] = (),
    ) -> RuleResult:
        """Run anti-spam + configured rules and execute the first match."""
        policy = policy or AntiSpamPolicy()
        if not is_ignored(event, policy):
            verdicts = await self.antispam.evaluate(event, policy)
            if verdicts:
                actions = self.actions_for_verdicts(verdicts, policy, fallback_actions)
                executed = [await self.executor(a, event, None) for a in actions]
                return RuleResult(True, None, verdicts, executed)

        for rule in sorted((r for r in rules if r.enabled), key=lambda r: r.priority):
            if evaluate_rule(event, rule):
                executed = [await self.executor(a, event, rule) for a in rule.actions]
                return RuleResult(True, rule, [], executed)

        return RuleResult(False, None, [], [], "no rule matched")

    @staticmethod
    def actions_for_verdicts(
        verdicts: list[SpamVerdict],
        policy: AntiSpamPolicy,
        fallback_actions: Sequence[dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        severity_rank = {"low": 1, "medium": 2, "high": 3}
        worst = max(verdicts, key=lambda v: severity_rank.get(v.severity, 0))
        if fallback_actions:
            actions = [dict(a) for a in fallback_actions]
        else:
            actions = [{"type": "delete"}]
            if worst.severity == "low":
                actions.append({"type": "warn", "reason": worst.reason})
            elif worst.severity == "medium":
                actions.append(
                    {"type": "mute", "duration": policy.mute_seconds, "reason": worst.reason}
                )
            else:
                actions.append(
                    {"type": policy.action, "duration": policy.mute_seconds, "reason": worst.reason}
                )
        for action in actions:
            action.setdefault("reason", worst.reason)
            action.setdefault("source", "automation")
        return actions


def describe_rule(rule: Rule) -> str:
    """Human readable summary shown in the webapp."""
    conditions = " and ".join(
        f"{c.get('fact')} {c.get('op', 'eq')} {c.get('value')!r}" for c in rule.conditions
    )
    actions = " then ".join(str(a.get("type")) for a in rule.actions)
    return f"on {rule.event}: {conditions or 'always'} -> {actions}"
