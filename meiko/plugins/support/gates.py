"""Owner-controlled capability gates for dangerous features.

Some plugin features must never be one "enable" click away: executing code,
storing arbitrary uploads and writing to external services are gated by an
explicit, audited platform-owner decision stored in ``system_settings``.

The gate is enforced *inside the plugin runtime*, not only in the API, so a
crafted update cannot bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from meiko.db.base import session_scope
from meiko.db.models import SystemSetting


class GateDenied(PermissionError):
    """Capability is closed for this actor."""

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(reason)
        self.capability = capability
        self.reason = reason


@dataclass
class CapabilityState:
    capability: str
    enabled: bool = False
    allowed_telegram_ids: list[int] = field(default_factory=list)
    allowed_bot_ids: list[int] = field(default_factory=list)
    max_runs_per_day: int = 0
    updated_by: int | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "enabled": self.enabled,
            "allowed_telegram_ids": list(self.allowed_telegram_ids),
            "allowed_bot_ids": list(self.allowed_bot_ids),
            "max_runs_per_day": self.max_runs_per_day,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, capability: str, data: dict[str, Any] | None) -> "CapabilityState":
        data = data or {}
        return cls(
            capability=capability,
            enabled=bool(data.get("enabled", False)),
            allowed_telegram_ids=[
                int(value) for value in data.get("allowed_telegram_ids", []) if str(value).isdigit()
            ],
            allowed_bot_ids=[
                int(value) for value in data.get("allowed_bot_ids", []) if str(value).isdigit()
            ],
            max_runs_per_day=int(data.get("max_runs_per_day", 0) or 0),
            updated_by=data.get("updated_by"),
            updated_at=str(data.get("updated_at", "")),
        )

    def permits(self, telegram_id: int | None, bot_id: int | None = None) -> bool:
        if not self.enabled:
            return False
        if self.allowed_bot_ids and bot_id is not None and bot_id not in self.allowed_bot_ids:
            return False
        if self.allowed_telegram_ids and telegram_id not in self.allowed_telegram_ids:
            return False
        return True


class CapabilityGate:
    """Reads/writes capability state; cached per process for a few seconds."""

    SETTING_PREFIX = "capability:"
    _CACHE_TTL = 5.0

    def __init__(self, capability: str) -> None:
        self.capability = capability
        self._cached: CapabilityState | None = None
        self._cached_at = 0.0

    @property
    def setting_key(self) -> str:
        return f"{self.SETTING_PREFIX}{self.capability}"

    async def state(self, *, refresh: bool = False) -> CapabilityState:
        import time

        if not refresh and self._cached is not None and time.time() - self._cached_at < self._CACHE_TTL:
            return self._cached
        async with session_scope() as session:
            row = await session.get(SystemSetting, self.setting_key)
            state = CapabilityState.from_dict(self.capability, row.value if row else None)
        self._cached = state
        self._cached_at = time.time()
        return state

    async def set_state(
        self,
        *,
        enabled: bool | None = None,
        allowed_telegram_ids: Iterable[int] | None = None,
        allowed_bot_ids: Iterable[int] | None = None,
        max_runs_per_day: int | None = None,
        actor_user_id: int | None = None,
        actor_telegram_id: int | None = None,
    ) -> CapabilityState:
        async with session_scope() as session:
            row = await session.get(SystemSetting, self.setting_key)
            data = dict(row.value or {}) if row else {}
            if enabled is not None:
                data["enabled"] = bool(enabled)
            if allowed_telegram_ids is not None:
                data["allowed_telegram_ids"] = [int(v) for v in allowed_telegram_ids]
            if allowed_bot_ids is not None:
                data["allowed_bot_ids"] = [int(v) for v in allowed_bot_ids]
            if max_runs_per_day is not None:
                data["max_runs_per_day"] = int(max_runs_per_day)
            data["updated_by"] = actor_user_id
            data["updated_at"] = datetime.utcnow().isoformat()
            if row is None:
                session.add(SystemSetting(key=self.setting_key, value=data))
            else:
                row.value = data
        self._cached = None
        return await self.state(refresh=True)

    async def require(
        self,
        *,
        telegram_id: int | None,
        bot_id: int | None = None,
        platform_owner: bool = False,
        owner_telegram_id: int | None = None,
    ) -> CapabilityState:
        """Raise :class:`GateDenied` unless the actor may use the capability."""
        state = await self.state()
        if not state.enabled:
            raise GateDenied(
                self.capability,
                f"{self.capability.replace('_', ' ')} is disabled by the platform owner",
            )
        allowed = state.permits(telegram_id, bot_id) or (
            platform_owner and state.permits(owner_telegram_id or telegram_id, bot_id)
        )
        if not allowed:
            raise GateDenied(
                self.capability,
                f"your account is not on the {self.capability.replace('_', ' ')} allow-list",
            )
        return state


async def capability_snapshot(capability: str) -> dict[str, Any]:
    return (await CapabilityGate(capability).state(refresh=True)).to_dict()
