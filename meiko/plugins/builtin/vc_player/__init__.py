"""Voice-chat player package.

Layout::

    queue.py         durable queue state machine (pure, no aiogram)
    sources.py       link/flag parsing, layered search, VC backend detection
    player_card.py   play card, progress bar, control keyboards
    plugin.py        commands + the ``pb:``/``q:`` callback router
    manifest.py      plugin contract
"""

from .manifest import MANIFEST
from .player_card import clock, play_card, player_keyboard, progress_bar, queue_card
from .plugin import VCPlayerPlugin, parse_seek
from .queue import PlayerQueue, QueueState, Track
from .sources import MediaResolver, PlayRequest, parse_request, vc_available

__all__ = [
    "MANIFEST",
    "MediaResolver",
    "PlayRequest",
    "PlayerQueue",
    "QueueState",
    "Track",
    "VCPlayerPlugin",
    "clock",
    "parse_request",
    "parse_seek",
    "play_card",
    "player_keyboard",
    "progress_bar",
    "queue_card",
    "vc_available",
]
