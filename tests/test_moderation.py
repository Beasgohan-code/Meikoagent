"""Anti-spam detectors and the EVENT -> CONDITION -> ACTION -> AUDIT engine."""

from __future__ import annotations

import pytest

from meiko.moderation.antispam import (
    AntiSpamEngine,
    AntiSpamPolicy,
    ReputationTracker,
    is_ignored,
)
from meiko.moderation.events import ModerationEvent
from meiko.moderation.rules import Rule, RuleEngine, describe_rule, evaluate_rule


def event(text="hello", **kwargs) -> ModerationEvent:
    base = dict(kind="message", bot_id=1, chat_id=-100, user_id=42, group_id=3, text=text)
    base.update(kwargs)
    return ModerationEvent(**base)


class Recorder:
    def __init__(self):
        self.calls = []
        self.records = []

    async def __call__(self, action, evt, rule):
        self.calls.append(action)
        return {"action": action.get("type"), "ok": True}

    async def record(self, bot_id, action, **kwargs):
        self.records.append((bot_id, action, kwargs))

    async def notify(self, chat_id, text, thread=None):
        self.records.append(("notify", chat_id, text))


class TestDetectors:
    async def test_flood_detection(self, cache):
        engine = AntiSpamEngine(cache)
        policy = AntiSpamPolicy(flood_messages=3, flood_window_seconds=60)
        verdicts = []
        for _ in range(4):
            verdicts = await engine.evaluate(event("msg"), policy)
        assert any(v.rule == "flood" for v in verdicts)

    async def test_duplicate_detection(self, cache):
        engine = AntiSpamEngine(cache)
        policy = AntiSpamPolicy(duplicate_threshold=2, flood_enabled=False)
        await engine.evaluate(event("same"), policy)
        verdicts = await engine.evaluate(event("same"), policy)
        assert any(v.rule == "duplicate" for v in verdicts)

    async def test_invite_link_high_severity(self, cache):
        engine = AntiSpamEngine(cache)
        verdicts = await engine.evaluate(event("join https://t.me/+abcDEF"), AntiSpamPolicy())
        assert any(v.rule == "invite_link" and v.severity == "high" for v in verdicts)

    async def test_link_detection_and_allow_links(self, cache):
        engine = AntiSpamEngine(cache)
        assert await engine.evaluate(event("see https://evil.test"), AntiSpamPolicy())
        allowed = await engine.evaluate(
            event("see https://ok.test"), AntiSpamPolicy(allow_links=True)
        )
        assert not any(v.rule == "link" for v in allowed)

    async def test_mention_spam(self, cache):
        engine = AntiSpamEngine(cache)
        verdicts = await engine.evaluate(event("hi", mentions_count=12), AntiSpamPolicy())
        assert any(v.rule == "mention_spam" for v in verdicts)

    async def test_keyword_filter(self, cache):
        engine = AntiSpamEngine(cache)
        policy = AntiSpamPolicy(keywords_enabled=True, blocked_keywords=["casino"])
        verdicts = await engine.evaluate(event("BEST CASINO here"), policy)
        assert any(v.rule == "blocked_keyword" for v in verdicts)

    async def test_caps_and_emoji(self, cache):
        engine = AntiSpamEngine(cache)
        policy = AntiSpamPolicy(caps_enabled=True, emoji_enabled=True, max_emoji=2)
        verdicts = await engine.evaluate(event("THIS IS ALL SHOUTING 🎉🎉🎉🎉"), policy)
        rules = {v.rule for v in verdicts}
        assert "caps" in rules and "emoji_spam" in rules

    async def test_clean_message_has_no_verdict(self, cache):
        engine = AntiSpamEngine(cache)
        assert await engine.evaluate(event("good morning"), AntiSpamPolicy()) == []

    async def test_suspicious_new_account(self, cache):
        engine = AntiSpamEngine(cache)
        verdict = await engine.suspicious_join(
            event("", user_id=9, joined_within_seconds=30), AntiSpamPolicy()
        )
        assert verdict and verdict.rule == "suspicious_join"

    async def test_higher_privilege_members_ignored(self):
        policy = AntiSpamPolicy()
        assert is_ignored(event(is_admin=True), policy)
        assert is_ignored(event(is_trusted=True), policy)
        assert not is_ignored(event(), policy)

    async def test_reputation_tracker(self, cache):
        tracker = ReputationTracker(cache)
        assert await tracker.add(1, -100, 42) == 1
        assert await tracker.add(1, -100, 42) == 2
        assert await tracker.get(1, -100, 42) == 2
        await tracker.reset(1, -100, 42)
        assert await tracker.get(1, -100, 42) == 0


class TestRuleEngine:
    def test_condition_operators(self):
        rule = Rule(
            id=1,
            name="long messages",
            event="message",
            conditions=[{"fact": "message.length", "op": "gt", "value": 10}],
        )
        assert evaluate_rule(event("x" * 20), rule)
        assert not evaluate_rule(event("short"), rule)

    def test_event_mismatch(self):
        rule = Rule(id=1, name="joins", event="join", conditions=[], actions=[])
        assert not evaluate_rule(event("hi"), rule)

    def test_regex_condition(self):
        rule = Rule(
            id=1,
            name="crypto",
            event="message",
            conditions=[{"fact": "message.text", "op": "regex", "value": r"\bbtc\b"}],
        )
        assert evaluate_rule(event("send BTC now"), rule)

    def test_partial_rule_validation(self):
        rule = Rule(id=1, name="bad", event="message", conditions=[{"op": "eq"}], actions=[{"type": "nope"}])
        errors = rule.validate()
        assert len(errors) >= 2

    def test_describe_rule(self):
        rule = Rule(
            id=1,
            name="x",
            event="message",
            conditions=[{"fact": "message.has_link", "op": "is_true"}],
            actions=[{"type": "delete"}],
        )
        assert "message.has_link" in describe_rule(rule)

    async def test_flood_triggers_actions_and_audit(self, cache):
        executor = Recorder()
        engine = RuleEngine(executor, AntiSpamEngine(cache))
        policy = AntiSpamPolicy(flood_messages=2, flood_window_seconds=60)
        for _ in range(3):
            result = await engine.process(event("spam"), policy=policy)
        assert result.matched
        assert result.verdicts
        types = [c["type"] for c in executor.calls]
        assert "delete" in types

    async def test_custom_rule_executes_actions(self, cache):
        executor = Recorder()
        engine = RuleEngine(executor, AntiSpamEngine(cache))
        rule = Rule(
            id=5,
            name="welcome link",
            event="message",
            conditions=[{"fact": "message.has_link", "op": "is_true"}],
            actions=[{"type": "delete", "reason": "links not allowed"}],
        )
        result = await engine.process(event("look at this", has_link=True), rules=[rule])
        assert result.matched and result.rule is rule
        assert executor.calls[0]["type"] == "delete"

    async def test_priority_ordering(self, cache):
        executor = Recorder()
        engine = RuleEngine(executor, AntiSpamEngine(cache))
        low = Rule(id=1, name="late", event="message", priority=50, actions=[{"type": "log"}])
        high = Rule(id=2, name="first", event="message", priority=1, actions=[{"type": "warn"}])
        result = await engine.process(event("hi"), rules=[low, high])
        assert result.rule.name == "first"

    async def test_no_match_returns_unmatched(self, cache):
        engine = RuleEngine(Recorder(), AntiSpamEngine(cache))
        result = await engine.process(event("harmless"), rules=[])
        assert not result.matched
        assert result.unmatched_reason

    async def test_admin_message_is_not_punished(self, cache):
        executor = Recorder()
        engine = RuleEngine(executor, AntiSpamEngine(cache))
        policy = AntiSpamPolicy(flood_messages=1, flood_window_seconds=60)
        for _ in range(4):
            result = await engine.process(
                event("repeated", is_admin=True), policy=policy
            )
        assert not result.matched


class TestExecutorGuardrails:
    async def test_unknown_action_is_rejected(self, cache):
        from meiko.moderation.executor import ModerationExecutor

        recorder = Recorder()
        executor = ModerationExecutor(bot=None, bot_id=1, audit=recorder)  # type: ignore[arg-type]
        result = await executor.execute({"type": "self_destruct"}, event())
        assert result["ok"] is False

    async def test_delete_without_message_id_is_noop(self, cache):
        from meiko.moderation.executor import ModerationExecutor

        recorder = Recorder()
        executor = ModerationExecutor(bot=None, bot_id=1, audit=recorder)  # type: ignore[arg-type]
        result = await executor.execute({"type": "delete"}, event())
        assert result == {"action": "delete", "ok": True, "deleted": False, "reason": "no message"}

    async def test_log_action_writes_audit(self, cache):
        from meiko.moderation.executor import ModerationExecutor

        recorder = Recorder()
        executor = ModerationExecutor(bot=None, bot_id=1, audit=recorder)  # type: ignore[arg-type]
        result = await executor.execute({"type": "log", "text": "noted"}, event())
        assert result["ok"]
        assert recorder.records[0][1] == "log"
