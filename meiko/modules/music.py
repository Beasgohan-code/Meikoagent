"""Shared music session store: the bridge between the bot player and the webapp.

The bot owns playback (Telegram voice chats); the webapp must control the very
same session, not a fake copy.  This module is the single source of truth both
sides read and write:

* ``MusicSessions`` stores one room snapshot per chat through the shared module
  store (durable, so a restart keeps the queue and the "what was playing"),
* transports are *actions* the bot performs on a command — the API only records
  the intent and the running bot applies it, publishing ``music.*`` events.  If
  no bot is running, the API answers ``applied: false`` instead of pretending,
* playlists, preferences, listener presence and listening stats live here too,
  so the dashboard numbers are derived from real activity.

Nothing in this module talks to Telegram; that keeps it testable.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any

from meiko.modules.events import get_event_hub

ACTIONS = {"play", "pause", "resume", "skip", "previous", "stop", "seek", "volume", "shuffle", "loop"}
MODULE = "music"
MAX_RESULTS = 50


def _now() -> int:
    return int(time.time())


@dataclass
class ListeningStat:
    plays: int = 0
    seconds: int = 0
    last_played: int = 0
    top: dict[str, int] = field(default_factory=dict)

    def record(self, title: str, duration: int = 0) -> None:
        self.plays += 1
        self.seconds += max(0, duration)
        self.last_played = _now()
        if title:
            self.top[title] = self.top.get(title, 0) + 1
            if len(self.top) > MAX_RESULTS:
                keep = sorted(self.top.items(), key=lambda row: row[1], reverse=True)[:MAX_RESULTS]
                self.top = dict(keep)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plays": self.plays,
            "seconds": self.seconds,
            "last_played": self.last_played,
            "top": dict(sorted(self.top.items(), key=lambda row: row[1], reverse=True)[:10]),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ListeningStat":
        if not isinstance(data, dict):
            return cls()
        return cls(
            plays=int(data.get("plays") or 0),
            seconds=int(data.get("seconds") or 0),
            last_played=int(data.get("last_played") or 0),
            top={str(k): int(v) for k, v in (data.get("top") or {}).items()},
        )


class MusicSessions:
    """Durable per-chat sessions, playlists, preferences and stats."""

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- scopes -----------------------------------------------------------
    def _scope(self, bot_id: int) -> str:
        return f"bot:{bot_id}"

    def _read(self, bot_id: int) -> dict[str, Any]:
        document = self.store.read(MODULE, self._scope(bot_id))
        document.setdefault("rooms", {})
        document.setdefault("playlists", {})
        document.setdefault("preferences", {})
        document.setdefault("listeners", {})
        document.setdefault("stats", {})
        return document

    def _write(self, bot_id: int, document: dict[str, Any]) -> None:
        self.store.write(MODULE, self._scope(bot_id), document)

    # -- room snapshots ---------------------------------------------------
    def rooms(self, bot_id: int) -> dict[str, Any]:
        return dict(self._read(bot_id)["rooms"])

    def room(self, bot_id: int, chat_id: int) -> dict[str, Any]:
        return dict(self._read(bot_id)["rooms"].get(str(chat_id)) or {})

    def publish_room(self, bot_id: int, chat_id: int, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Called by the bot after every queue mutation."""
        document = self._read(bot_id)
        snapshot = dict(snapshot)
        snapshot["chat_id"] = chat_id
        snapshot["updated_at"] = _now()
        previous = document["rooms"].get(str(chat_id)) or {}
        document["rooms"][str(chat_id)] = snapshot
        current = snapshot.get("current") or {}
        before = (previous.get("current") or {}).get("title") or ""
        if current.get("title") and current.get("title") != before:
            # count a play only when the track actually changed, not on every
            # pause/seek/volume edit of the same card
            stats = ListeningStat.from_dict(document["stats"].get(str(chat_id)))
            stats.record(str(current["title"]), int(current.get("duration") or 0))
            document["stats"][str(chat_id)] = stats.to_dict()
        self._write(bot_id, document)
        get_event_hub().publish(
            "music.room", {"chat_id": chat_id, **snapshot}, scope=f"bot:{bot_id}"
        )
        return snapshot

    def active_rooms(self, bot_id: int) -> list[dict[str, Any]]:
        rows = []
        for chat_id, room in self._read(bot_id)["rooms"].items():
            if room.get("current") or room.get("items"):
                rows.append({"chat_id": int(chat_id), **room})
        rows.sort(key=lambda row: row.get("updated_at", 0), reverse=True)
        return rows

    # -- presence ---------------------------------------------------------
    def touch_listener(self, bot_id: int, chat_id: int, user_id: int, name: str = "") -> dict[str, Any]:
        document = self._read(bot_id)
        room = document["listeners"].setdefault(str(chat_id), {})
        room[str(user_id)] = {"name": name, "seen": _now()}
        cutoff = _now() - 900
        document["listeners"][str(chat_id)] = {
            key: value for key, value in room.items() if int(value.get("seen") or 0) >= cutoff
        }
        self._write(bot_id, document)
        get_event_hub().publish(
            "music.presence",
            {"chat_id": chat_id, "listeners": len(document["listeners"][str(chat_id)])},
            scope=f"bot:{bot_id}",
        )
        return document["listeners"][str(chat_id)]

    def listeners(self, bot_id: int, chat_id: int) -> list[dict[str, Any]]:
        rows = self._read(bot_id)["listeners"].get(str(chat_id)) or {}
        return [{"user_id": int(uid), **value} for uid, value in rows.items()]

    # -- playlists --------------------------------------------------------
    def playlists(self, bot_id: int, *, owner_id: int | None = None) -> dict[str, Any]:
        rows = self._read(bot_id)["playlists"]
        if owner_id is None:
            return dict(rows)
        return {name: row for name, row in rows.items() if int(row.get("owner_id") or 0) == owner_id}

    def save_playlist(
        self,
        bot_id: int,
        name: str,
        tracks: list[dict[str, Any]],
        *,
        owner_id: int,
        chat_id: int = 0,
    ) -> dict[str, Any]:
        document = self._read(bot_id)
        entry = {
            "name": name[:48],
            "owner_id": owner_id,
            "chat_id": chat_id,
            "tracks": tracks[:200],
            "created_at": document["playlists"].get(name, {}).get("created_at") or _now(),
            "updated_at": _now(),
        }
        document["playlists"][name[:48]] = entry
        self._write(bot_id, document)
        get_event_hub().publish(
            "music.playlists", {"name": entry["name"], "size": len(entry["tracks"])},
            scope=f"bot:{bot_id}",
        )
        return entry

    def delete_playlist(self, bot_id: int, name: str, *, owner_id: int) -> bool:
        document = self._read(bot_id)
        entry = document["playlists"].get(name)
        if entry is None:
            return False
        if int(entry.get("owner_id") or 0) != owner_id:
            raise PermissionError("that playlist belongs to someone else")
        document["playlists"].pop(name, None)
        self._write(bot_id, document)
        return True

    # -- preferences ------------------------------------------------------
    def preferences(self, bot_id: int, user_id: int) -> dict[str, Any]:
        return dict(self._read(bot_id)["preferences"].get(str(user_id)) or {})

    def set_preference(self, bot_id: int, user_id: int, key: str, value: Any) -> dict[str, Any]:
        document = self._read(bot_id)
        prefs = dict(document["preferences"].get(str(user_id)) or {})
        prefs[str(key)] = value
        document["preferences"][str(user_id)] = prefs
        self._write(bot_id, document)
        return prefs

    # -- analytics --------------------------------------------------------
    def analytics(self, bot_id: int) -> dict[str, Any]:
        document = self._read(bot_id)
        stats = {chat: ListeningStat.from_dict(row) for chat, row in document["stats"].items()}
        titles: dict[str, int] = {}
        for stat in stats.values():
            for title, count in stat.top.items():
                titles[title] = titles.get(title, 0) + count
        return {
            "rooms": len(document["rooms"]),
            "active_rooms": len(self.active_rooms(bot_id)),
            "plays": sum(stat.plays for stat in stats.values()),
            "seconds": sum(stat.seconds for stat in stats.values()),
            "playlists": len(document["playlists"]),
            "listening_rooms": len(stats),
            "top_tracks": [
                {"title": title, "plays": count}
                for title, count in sorted(titles.items(), key=lambda row: row[1], reverse=True)[:10]
            ],
        }


def _plugin_slug(plugin: Any) -> str:
    """The manifest slug of a started plugin (instances do not carry one)."""
    manifest = getattr(plugin, "manifest", None)
    return str(getattr(manifest, "slug", "") or getattr(plugin, "slug", "") or "")


class MusicTransport:
    """Applies webapp actions to the *real* running bot, or says it cannot."""

    def __init__(self, sessions: MusicSessions) -> None:
        self.sessions = sessions

    def _application(self, bot_id: int) -> Any:
        try:
            from meiko.runtime.manager import get_worker_manager

            return get_worker_manager().state.applications.get(bot_id)
        except Exception:  # pragma: no cover - manager not started
            return None

    def _runtime(self, bot_id: int) -> Any:
        application = self._application(bot_id)
        if application is None:
            return None
        for plugin in getattr(application, "plugins", None) or []:
            if _plugin_slug(plugin) == "music":
                return plugin
        return None

    async def dispatch(
        self,
        bot_id: int,
        chat_id: int,
        action: str,
        *,
        value: Any = None,
        actor_id: int = 0,
    ) -> dict[str, Any]:
        if action not in ACTIONS:
            return {"applied": False, "reason": f"unknown action {action!r}"}
        runtime = self._runtime(bot_id)
        if runtime is None:
            return {
                "applied": False,
                "reason": "the bot is not running; start it to control playback",
                "action": action,
            }
        handler = getattr(runtime, "apply_transport", None)
        if handler is None:
            return {"applied": False, "reason": "this music build cannot be driven remotely"}
        result = handler(bot_id=bot_id, chat_id=chat_id, action=action, value=value, actor_id=actor_id)
        if inspect.isawaitable(result):
            result = await result
        return {"applied": True, **(result or {})}


__all__ = ["ACTIONS", "ListeningStat", "MusicSessions", "MusicTransport", "MODULE"]
