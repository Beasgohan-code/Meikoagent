"""The play card: progress bar, state line, controls and the queue view.

Everything is rendered from a :class:`~.queue.QueueState`, so a card can be
rebuilt for a fresh message, an edited one or a webapp preview from the same
state.  Callback protocol (Playeon-compatible)::

    pb:toggle   pb:skip  pb:end    pb:-10  pb:+10
    pb:loop     pb:loopq pb:auto   pb:mode pb:shuffle
    pb:prev     pb:queue q:close   q:rm:<n>

Premium emoji come from the UI engine, which falls back to plain glyphs when a
bot cannot use custom emoji - so a card never arrives with a broken entity.
"""

from __future__ import annotations

from typing import Sequence

from meiko.ui import Btn, esc, keyboard, message
from meiko.ui.emoji import get as get_emoji

from .queue import PlayerQueue, Track

BAR_WIDTH = 12
FILLED = "▰"
EMPTY = "▱"


def clock(seconds: int | float | None) -> str:
    """``m:ss`` or ``h:mm:ss`` – what a player shows, not a humanised duration."""
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def progress_bar(position: int, duration: int, width: int = BAR_WIDTH) -> str:
    """``▰▰▰▱▱▱▱▱▱▱▱▱`` for the current position (empty bar when unknown)."""
    if duration <= 0 or width <= 0:
        return EMPTY * max(0, width)
    ratio = min(1.0, max(0.0, position / duration))
    filled = int(round(ratio * width))
    return FILLED * filled + EMPTY * (width - filled)


def state_line(queue: PlayerQueue) -> str:
    """One line: ▶️/⏸️ · position/duration · flags."""
    state = queue.state
    current = state.current
    if current is None:
        return f"{get_emoji('sleep').fallback} idle"
    icon = get_emoji("pause" if state.paused else "play").html
    elapsed = clock(state.position)
    total = clock(current.duration) if current.duration else "live"
    flags = []
    if state.loop_count:
        flags.append(f"🔁 ×{state.loop_count}")
    if state.loop_queue:
        flags.append("🔁 queue")
    if state.autoplay:
        flags.append("✨ autoplay")
    if state.volume != 100:
        flags.append(f"🔊 {state.volume}%")
    suffix = "  " + " · ".join(flags) if flags else ""
    return f"{icon} <b>{elapsed}</b> / {total}{suffix}"


def track_line(track: Track, index: int | None = None) -> str:
    prefix = f"<b>{index}.</b> " if index is not None else ""
    title = f"<code>{esc(track.label)}</code>"
    meta = []
    if track.duration:
        meta.append(clock(track.duration))
    if track.uploader:
        meta.append(esc(track.uploader))
    if track.requested_by:
        meta.append(f"by {esc(track.requested_by)}")
    tail = f" · {' · '.join(meta)}" if meta else ""
    return f"{prefix}{title}{tail}"


def play_card(queue: PlayerQueue, *, requested_by: str = "", room: str = "") -> str:
    """The card shown when something starts playing."""
    state = queue.state
    current = state.current
    builder = message("Now playing", "play" if not state.paused else "pause")
    if current is None:
        builder.line("Nothing is playing. Use <code>/play &lt;song&gt;</code>.", raw=True)
        return builder.build()

    title = f"<a href=\"{esc(current.url)}\">{esc(current.label)}</a>" if current.url.startswith("http") else f"<b>{esc(current.label)}</b>"
    builder.line(title, raw=True)
    if current.uploader:
        builder.line(f"{get_emoji('user').html} {esc(current.uploader)}", raw=True)
    builder.blank()
    builder.line(f"{progress_bar(state.position, current.duration)}", raw=True)
    builder.line(state_line(queue), raw=True)
    if requested_by:
        builder.line(f"{get_emoji('users').html} requested by {esc(requested_by)}", raw=True)
    if room:
        builder.line(f"{get_emoji('link').html} <code>{esc(room)}</code>", raw=True)
    upcoming = queue.upcoming(3)
    if upcoming:
        builder.blank()
        builder.line(f"{get_emoji('queue').html} <b>Up next</b>", raw=True)
        for position, track in enumerate(upcoming, start=1):
            builder.line(track_line(track, position), raw=True)
    return builder.build()


def player_keyboard(queue: PlayerQueue) -> object:
    """Two control rows plus a queue row, Playeon's ``pb:`` protocol."""
    paused = queue.state.paused
    empty = queue.state.current is None
    toggle = "Resume" if paused else "Pause"
    return keyboard(
        [
            [
                Btn("Back", callback_data="pb:-10", emoji="arrow"),
                Btn(toggle, callback_data="pb:toggle", emoji="unmute" if paused else "mute"),
                Btn("Skip", callback_data="pb:skip", emoji="arrow"),
            ],
            [
                Btn("+10s", callback_data="pb:+10", emoji="clock"),
                Btn("Loop", callback_data="pb:loop", emoji="refresh"),
                Btn("Stop", callback_data="pb:end", emoji="cross"),
            ],
            [
                Btn("Queue", callback_data="pb:queue", emoji="queue"),
                Btn("Mode", callback_data="pb:mode", emoji="video" if queue.state.mode == "video" else "music"),
                Btn("Autoplay", callback_data="pb:auto", emoji="spark"),
            ],
        ]
    ) if not empty else keyboard([[Btn("Queue", callback_data="pb:queue", emoji="queue")]])


def queue_card(queue: PlayerQueue, *, page: int = 1, per_page: int = 8) -> str:
    state = queue.state
    builder = message("Queue", "queue")
    if state.current is not None:
        builder.key_value("Playing", state.current.label, "play")
    total = state.remaining
    builder.key_value("Up next", f"{total} track(s)", "queue")
    if state.total_duration():
        builder.key_value("Total", clock(state.total_duration()), "clock")
    builder.blank()
    start = max(0, (page - 1) * per_page)
    window = state.items[start : start + per_page]
    if not window:
        builder.line("The queue is empty.", raw=True)
    for offset, track in enumerate(window, start=start + 1):
        builder.line(track_line(track, offset), raw=True)
    pages = max(1, (total + per_page - 1) // per_page)
    if pages > 1:
        builder.line(f"<i>page {page}/{pages}</i>", raw=True)
    return builder.build()


def queue_keyboard(queue: PlayerQueue, *, page: int = 1, per_page: int = 8) -> object:
    total = queue.state.remaining
    pages = max(1, (total + per_page - 1) // per_page)
    rows: list[list[Btn]] = []
    nav: list[Btn] = []
    if page > 1:
        nav.append(Btn("Prev", callback_data=f"pb:qpage:{page - 1}", emoji="arrow"))
    if page < pages:
        nav.append(Btn("Next", callback_data=f"pb:qpage:{page + 1}", emoji="arrow"))
    if nav:
        rows.append(nav)
    remove_row = [
        Btn(f"✕{start}", callback_data=f"q:rm:{start}", emoji="trash")
        for start in range((page - 1) * per_page + 1, min(total, page * per_page) + 1)
    ]
    for index in range(0, len(remove_row), 5):
        rows.append(remove_row[index : index + 5])
    rows.append([Btn("Close", callback_data="q:close", emoji="cross")])
    return keyboard(rows)


def results_card(query: str, tracks: Sequence[Track], *, layer: str = "", page: int = 1, per_page: int = 8) -> str:
    builder = message(f"Results · {query[:40]}", "search")
    if layer:
        builder.key_value("Source", layer, "database")
    if not tracks:
        builder.line("Nothing found.", raw=True)
        return builder.build()
    for index, track in enumerate(tracks, start=1):
        builder.line(track_line(track, index), raw=True)
    builder.blank()
    builder.line("<i>Tap a number to queue it.</i>", raw=True)
    return builder.build()


def results_keyboard(tracks: Sequence[Track], *, prefix: str = "pb:pick") -> object:
    rows: list[list[Btn]] = []
    row: list[Btn] = []
    for index in range(1, min(len(tracks), 10) + 1):
        row.append(Btn(str(index), callback_data=f"{prefix}:{index - 1}", emoji="music"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Btn("Cancel", callback_data="q:close", emoji="cross")])
    return keyboard(rows)


__all__ = [
    "BAR_WIDTH",
    "clock",
    "play_card",
    "player_keyboard",
    "progress_bar",
    "queue_card",
    "queue_keyboard",
    "results_card",
    "results_keyboard",
    "state_line",
    "track_line",
]
