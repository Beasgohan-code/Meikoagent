"""Playback queue: ordering, loop, autoplay, repeat-count and history.

Pure state machine – no aiogram, no network, no clock beyond what is passed in.
That is what makes the interesting behaviour (what plays next, what a skip does
to history, how ``/loop 3`` and ``/autoplay`` interact) testable without a bot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable

MAX_QUEUE = 200
MAX_HISTORY = 50
#: ``audio`` keeps voice-chat audio, ``video`` streams video, ``both`` allows mixed queues.
MODES = ("audio", "video", "both")


@dataclass
class Track:
    """One playable item."""

    title: str = ""
    source: str = ""  # direct URL / file reference
    kind: str = "audio"  # audio | video
    duration: int = 0
    uploader: str = ""
    thumbnail: str = ""
    video_id: str = ""
    requested_by: str = ""
    requested_by_id: int | str = 0
    url: str = ""  # canonical page, shown in the card title
    height: int = 0  # video quality, when known
    stream_url: str = ""  # resolved, short-lived media URL
    file_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "kind": self.kind,
            "duration": self.duration,
            "uploader": self.uploader,
            "thumbnail": self.thumbnail,
            "video_id": self.video_id,
            "requested_by": self.requested_by,
            "requested_by_id": self.requested_by_id,
            "url": self.url or self.source,
            "height": self.height,
            "stream_url": self.stream_url,
            "file_id": self.file_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Track":
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}  # type: ignore[attr-defined]
        payload = {key: value for key, value in data.items() if key in known}
        return cls(**payload, extra=dict(data.get("extra") or {}))

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def label(self) -> str:
        return self.title or self.url or self.source or self.file_id or "untitled"


def _tracks(value: Any) -> list[Track]:
    """Tolerant list decoding – a corrupted state file must not crash the bot."""
    if not isinstance(value, list):
        return []
    return [Track.from_dict(item) for item in value if isinstance(item, dict)]


@dataclass
class QueueState:
    """Everything one room/chat is doing right now."""

    current: Track | None = None
    items: list[Track] = field(default_factory=list)
    history: list[Track] = field(default_factory=list)
    paused: bool = False
    position: int = 0  # seconds into the current track
    loop_count: int = 0  # remaining repeats of the current track (0 = off)
    loop_queue: bool = False  # repeat the whole queue
    autoplay: bool = False  # append recommendations when the queue empties
    volume: int = 100
    mode: str = "audio"  # audio | video
    updates: int = 0  # bumped on every mutation, used for card refresh

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current.to_dict() if self.current else None,
            "items": [track.to_dict() for track in self.items],
            "history": [track.to_dict() for track in self.history],
            "paused": self.paused,
            "position": self.position,
            "loop_count": self.loop_count,
            "loop_queue": self.loop_queue,
            "autoplay": self.autoplay,
            "volume": self.volume,
            "mode": self.mode,
            "updates": self.updates,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "QueueState":
        if not isinstance(data, dict):
            return cls()
        current = data.get("current")
        state = cls(
            current=Track.from_dict(current) if isinstance(current, dict) else None,
            items=_tracks(data.get("items")),
            history=_tracks(data.get("history")),
            paused=bool(data.get("paused", False)),
            position=max(0, int(data.get("position", 0) or 0)),
            loop_count=max(0, int(data.get("loop_count", 0) or 0)),
            loop_queue=bool(data.get("loop_queue", False)),
            autoplay=bool(data.get("autoplay", False)),
            volume=min(200, max(0, int(data.get("volume", 100) or 100))),
            mode=str(data.get("mode")) if data.get("mode") in MODES else "audio",
            updates=int(data.get("updates", 0) or 0),
        )
        return state

    # -- helpers ----------------------------------------------------------
    @property
    def empty(self) -> bool:
        return self.current is None and not self.items

    @property
    def remaining(self) -> int:
        return len(self.items)

    def total_duration(self) -> int:
        """Seconds still queued (excludes the current track on purpose)."""
        return sum(max(0, track.duration) for track in self.items)

    def _touch(self) -> None:
        self.updates += 1


class PlayerQueue:
    """The operations a player exposes, expressed on :class:`QueueState`."""

    def __init__(self, state: QueueState | None = None) -> None:
        self.state = state or QueueState()

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return self.state.to_dict()

    @classmethod
    def from_dict(cls, data: Any) -> "PlayerQueue":
        return cls(QueueState.from_dict(data))

    # -- queries ----------------------------------------------------------
    @property
    def current(self) -> Track | None:
        return self.state.current

    @property
    def paused(self) -> bool:
        return self.state.paused

    def is_empty(self) -> bool:
        return self.state.empty

    def upcoming(self, limit: int = 10) -> list[Track]:
        return self.state.items[:limit]

    def position(self) -> int:
        return self.state.position

    def progress(self) -> float:
        """0.0 – 1.0 through the current track (0 when the duration is unknown)."""
        current = self.state.current
        if current is None or current.duration <= 0:
            return 0.0
        return min(1.0, max(0.0, self.state.position / current.duration))

    # -- mutations --------------------------------------------------------
    def enqueue(self, track: Track, *, play_next: bool = False) -> int:
        """Add to the queue; returns the new queue length (0 when full)."""
        if len(self.state.items) >= MAX_QUEUE:
            return 0
        if play_next:
            self.state.items.insert(0, track)
        else:
            self.state.items.append(track)
        self.state._touch()
        return len(self.state.items)

    def start(self, track: Track) -> Track | None:
        """Begin ``track`` immediately, remembering what was playing."""
        if self.state.current is not None:
            self._push_history(self.state.current)
        self.state.current = track
        self.state.position = 0
        self.state.paused = False
        self.state.mode = "video" if track.is_video else "audio"
        self.state._touch()
        return track

    def play_now(self, track: Track) -> None:
        """``/play -f``: play immediately, push the current track back to the queue.

        The displaced track goes to the *queue*, not to history, so it is not
        both replayed from the queue and remembered as "previously played".
        """
        previous = self.state.current
        if previous is not None and previous is not track:
            self.state.items.insert(0, previous)
            del self.state.items[MAX_QUEUE:]
        self.state.current = track
        self.state.position = 0
        self.state.paused = False
        self.state.mode = "video" if track.is_video else "audio"
        self.state._touch()

    def next(self, *, autoplay_pool: Iterable[Track] | None = None) -> Track | None:
        """Advance the queue honouring loop-count, loop-queue and autoplay."""
        current = self.state.current
        if current is not None:
            if self.state.loop_count > 0:
                self.state.loop_count -= 1
                self.state.position = 0
                self.state.paused = False
                self.state._touch()
                return current
            self._push_history(current)

        if self.state.items:
            upcoming = self.state.items.pop(0)
            self.state.current = upcoming
            self.state.position = 0
            self.state.paused = False
            self.state.mode = "video" if upcoming.is_video else "audio"
            if self.state.loop_queue:
                self.state.items.append(upcoming)
            self.state._touch()
            return upcoming

        if self.state.loop_queue and self.state.history:
            replay = self.state.history[-1]
            self.state.current = replay
            self.state.position = 0
            self.state.paused = False
            self.state._touch()
            return replay

        self.state.current = None
        self.state.position = 0
        self.state.paused = False
        self.state._touch()
        if self.state.autoplay and autoplay_pool is not None:
            pool = list(autoplay_pool)
            if pool:
                pick = random.choice(pool)
                self.enqueue(pick)
                return self.next()
        return None

    def previous(self) -> Track | None:
        if not self.state.history:
            return None
        previous = self.state.history.pop()
        if self.state.current is not None:
            self.state.items.insert(0, self.state.current)
        self.state.current = previous
        self.state.position = 0
        self.state.paused = False
        self.state._touch()
        return previous

    def pause(self) -> bool:
        if self.state.current is None:
            return False
        self.state.paused = True
        self.state._touch()
        return True

    def resume(self) -> bool:
        if self.state.current is None:
            return False
        self.state.paused = False
        self.state._touch()
        return True

    def toggle(self) -> bool:
        """Returns the new paused flag (False when there is nothing to toggle)."""
        if self.state.current is None:
            return False
        self.state.paused = not self.state.paused
        self.state._touch()
        return self.state.paused

    def seek(self, seconds: int) -> int:
        current = self.state.current
        if current is None:
            return 0
        limit = current.duration if current.duration > 0 else None
        target = max(0, seconds)
        if limit is not None:
            target = min(limit, target)
        self.state.position = target
        self.state._touch()
        return target

    def jump(self, delta: int) -> int:
        return self.seek(self.state.position + delta)

    def set_loop(self, count: int) -> int:
        self.state.loop_count = max(0, min(50, int(count)))
        self.state._touch()
        return self.state.loop_count

    def set_loop_queue(self, enabled: bool) -> bool:
        self.state.loop_queue = bool(enabled)
        self.state._touch()
        return self.state.loop_queue

    def set_autoplay(self, enabled: bool) -> bool:
        self.state.autoplay = bool(enabled)
        self.state._touch()
        return self.state.autoplay

    def set_volume(self, volume: int) -> int:
        self.state.volume = max(0, min(200, int(volume)))
        self.state._touch()
        return self.state.volume

    def set_mode(self, mode: str) -> str:
        self.state.mode = mode if mode in MODES else "audio"
        self.state._touch()
        return self.state.mode

    def remove(self, index: int) -> Track | None:
        """Remove a queued track by 1-based position."""
        position = int(index) - 1
        if 0 <= position < len(self.state.items):
            track = self.state.items.pop(position)
            self.state._touch()
            return track
        return None

    def move(self, index: int, new_index: int) -> bool:
        position = int(index) - 1
        target = int(new_index) - 1
        if not (0 <= position < len(self.state.items)):
            return False
        track = self.state.items.pop(position)
        self.state.items.insert(max(0, min(len(self.state.items), target)), track)
        self.state._touch()
        return True

    def clear(self, *, keep_current: bool = False) -> int:
        removed = len(self.state.items)
        self.state.items.clear()
        if not keep_current:
            self.state.current = None
            self.state.position = 0
            self.state.paused = False
        self.state._touch()
        return removed

    def shuffle(self) -> int:
        random.shuffle(self.state.items)
        self.state._touch()
        return len(self.state.items)

    # -- internals --------------------------------------------------------
    def _push_history(self, track: Track) -> None:
        self.state.history.append(track)
        del self.state.history[:-MAX_HISTORY]


__all__ = ["MAX_HISTORY", "MAX_QUEUE", "MODES", "PlayerQueue", "QueueState", "Track"]
