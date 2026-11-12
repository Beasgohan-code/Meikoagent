"""``music`` plugin package.

Layout:
  id3.py       ID3v2 read/write (no mutagen dependency)
  library.py   filename metadata guessing, fuzzy search, cover art
  plugin.py    commands
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
from .plugin import MusicPlugin, parse_tag_argument

__all__ = [
    "MANIFEST",
    "MusicPlugin",
    "LibraryEntry",
    "MusicLibrary",
    "TrackTags",
    "guess_tags_from_filename",
    "is_audio",
    "parse_tag_argument",
    "placeholder_cover",
    "read_tags",
    "render_cover_text",
    "write_tag",
]
