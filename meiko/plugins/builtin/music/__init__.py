"""``music`` plugin package — voice-chat player **and** audio toolkit.

Layout::

    queue.py         durable Track/QueueState/PlayerQueue state machine (pure)
    sources.py       link+flag parsing, layered search, playlists, charts, VC detection
    player_card.py   play card, premium seek bar, control keyboards
    player.py        PlayerMixin: playback commands + ``pb:``/``q:`` callbacks
    plugin.py        MusicPlugin: toolkit commands (catalogue, ID3, covers) + wiring
    library.py       filename metadata guessing, fuzzy search
    id3.py           ID3v2 read/write (no mutagen dependency)
"""

from __future__ import annotations

from .id3 import TrackTags, is_audio, read_tags, write_tag
from .library import (
    LibraryEntry,
    MusicLibrary,
    guess_tags_from_filename,
    placeholder_cover,
    render_cover_text,
)
from .manifest import MANIFEST
from .player import PlayerMixin, parse_seek
from .player_card import clock, play_card, player_keyboard, progress_bar, queue_card
from .plugin import MusicPlugin, parse_tag_argument
from .queue import PlayerQueue, QueueState, Track
from .sources import MediaResolver, PlayRequest, Playlist, PlaylistManager, parse_request, vc_available

__all__ = [
    "MANIFEST",
    "LibraryEntry",
    "MediaResolver",
    "MusicLibrary",
    "MusicPlugin",
    "PlayRequest",
    "PlayerMixin",
    "PlayerQueue",
    "Playlist",
    "PlaylistManager",
    "QueueState",
    "Track",
    "TrackTags",
    "clock",
    "guess_tags_from_filename",
    "is_audio",
    "parse_request",
    "parse_seek",
    "parse_tag_argument",
    "placeholder_cover",
    "play_card",
    "player_keyboard",
    "progress_bar",
    "queue_card",
    "read_tags",
    "render_cover_text",
    "vc_available",
    "write_tag",
]
