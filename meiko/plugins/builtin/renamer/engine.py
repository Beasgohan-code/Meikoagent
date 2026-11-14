"""Rename engine: parse a release name, apply a template, clean the result.

Pure functions only – no aiogram, no database, no filesystem.  That makes the
behaviour (which is the part that actually matters to users) fully testable and
identical between the bot command, the webapp preview and the tests.

Template fields
---------------
``{title}``  ``{season}``  ``{episode}``  ``{quality}``  ``{audio}``
``{source}`` ``{group}``   ``{extension}`` ``{original}`` ``{year}``

Any field may use a format spec: ``{season:02d}`` -> ``05``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TEMPLATE = "{title} S{season:02d}E{episode:02d} [{quality}]"

QUALITY_RE = re.compile(r"\b(2160p|1440p|1080p|720p|576p|480p|360p|4k|2k|hd|sd)\b", re.I)
SEASON_EPISODE_RE = re.compile(
    r"\bS(?P<season>\d{1,2})[ ._-]?E(?P<episode>\d{1,4})\b", re.I
)
EPISODE_ONLY_RE = re.compile(r"\bE(?:P)?(?P<episode>\d{1,4})\b", re.I)
SEASON_ONLY_RE = re.compile(r"\b(?:S|Season[ .]?)(?P<season>\d{1,2})\b", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
AUDIO_RE = re.compile(r"\b(dual audio|multi audio|hindi|english|tamil|telugu|japanese|dubbed)\b", re.I)
SOURCE_RE = re.compile(r"\b(web[- .]?dl|webrip|bluray|blu[- ]ray|hdtv|dvdrip|brrip|amzn|nf|dsnp)\b", re.I)
GROUP_RE = re.compile(r"\[([^\]]{2,24})\]$")
NOISE_RE = re.compile(
    r"\b(x265|x264|h\.?265|h\.?264|hevc|avc|10bit|8bit|aac|ac3|ddp?5\.?1|atmos|"
    r"proper|repack|remux|hdr|sdr|subsplease|erai[- ]raws|yts|yify|rarbg|"
    r"multi|subbed|dubbed|uncensored)\b",
    re.I,
)
DOTS_UNDERSCORES = re.compile(r"[._]+")
SPACES = re.compile(r"\s{2,}")
UNSAFE = re.compile(r"[^\w\-. ()\[\]&+#@%']+", re.UNICODE)


@dataclass
class ParsedName:
    original: str
    title: str = ""
    season: int | None = None
    episode: int | None = None
    quality: str = ""
    audio: str = ""
    source: str = ""
    group: str = ""
    year: str = ""
    extension: str = ""

    def as_fields(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "season": self.season if self.season is not None else 0,
            "episode": self.episode if self.episode is not None else 0,
            "quality": self.quality,
            "audio": self.audio,
            "source": self.source,
            "group": self.group,
            "year": self.year,
            "extension": self.extension.lstrip("."),
            "original": self.original,
        }


@dataclass
class RenamePlan:
    original: str
    renamed: str
    fields: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.renamed != self.original


def split_extension(name: str) -> tuple[str, str]:
    match = re.search(r"(\.[A-Za-z0-9]{1,9})$", name or "")
    if not match:
        return name or "", ""
    return name[: match.start()], match.group(1).lower()


def parse_name(filename: str) -> ParsedName:
    """Extract the fields a renamer needs from a release/file name."""
    stem, extension = split_extension(filename)
    parsed = ParsedName(original=filename, extension=extension)
    text = stem.replace("_", " ")

    group_match = GROUP_RE.search(text)
    if group_match:
        parsed.group = group_match.group(1)
        text = text[: group_match.start()].strip()

    season_match = SEASON_EPISODE_RE.search(text)
    if season_match:
        parsed.season = int(season_match.group("season"))
        parsed.episode = int(season_match.group("episode"))
        text = text[: season_match.start()] + " " + text[season_match.end() :]
    else:
        episode_match = EPISODE_ONLY_RE.search(text)
        if episode_match:
            parsed.episode = int(episode_match.group("episode"))
            text = text[: episode_match.start()] + " " + text[episode_match.end() :]
        season_only = SEASON_ONLY_RE.search(text)
        if season_only:
            parsed.season = int(season_only.group("season"))
            text = text[: season_only.start()] + " " + text[season_only.end() :]

    quality_match = QUALITY_RE.search(text)
    if quality_match:
        parsed.quality = quality_match.group(1).lower()
        text = text[: quality_match.start()] + " " + text[quality_match.end() :]

    audio_match = AUDIO_RE.search(text)
    if audio_match:
        parsed.audio = audio_match.group(1).lower()
        text = text[: audio_match.start()] + " " + text[audio_match.end() :]

    source_match = SOURCE_RE.search(text)
    if source_match:
        parsed.source = source_match.group(1).lower().replace(" ", "")
        text = text[: source_match.start()] + " " + text[source_match.end() :]

    year_match = YEAR_RE.search(text)
    if year_match:
        parsed.year = year_match.group(0)
        text = text[: year_match.start()] + " " + text[year_match.end() :]

    # A release group that is attached with a dash ("…x264-GROUP") is only a
    # group when the bracketed form was absent and the tail looks like a name.
    if not parsed.group:
        dash_group = re.search(r"-([A-Za-z0-9]{2,20})$", text)
        if dash_group:
            parsed.group = dash_group.group(1)
            text = text[: dash_group.start()]

    text = NOISE_RE.sub(" ", text)
    text = re.sub(r"[\[\](){}]", " ", text)
    text = DOTS_UNDERSCORES.sub(" ", text)
    text = SPACES.sub(" ", text).strip(" -_.·|")
    parsed.title = text.strip()
    return parsed


def apply_template(
    parsed: ParsedName,
    template: str = DEFAULT_TEMPLATE,
    *,
    episode_offset: int = 0,
    max_length: int = 120,
    keep_extension: bool = True,
) -> RenamePlan:
    """Render ``template`` with the parsed fields; never raises on bad syntax."""
    fields = parsed.as_fields()
    if episode_offset and parsed.episode is not None:
        fields["episode"] = max(0, parsed.episode + episode_offset)
    warnings: list[str] = []
    try:
        name = template.format_map(_SafeDict(fields))
    except (ValueError, IndexError) as exc:
        warnings.append(f"invalid template ({exc}); falling back to the default")
        name = DEFAULT_TEMPLATE.format_map(_SafeDict(fields))

    name = UNSAFE.sub("_", name)
    name = SPACES.sub(" ", name).strip(" ._-")
    if keep_extension and parsed.extension and not name.lower().endswith(parsed.extension):
        name = f"{name}{parsed.extension}"
    if len(name) > max_length:
        stem, ext = split_extension(name)
        keep = max(8, max_length - len(ext))
        name = f"{stem[:keep].rstrip(' ._-')}{ext}"
        warnings.append(f"name truncated to {max_length} characters")
    if not name:
        name = parsed.original
        warnings.append("template produced an empty name; original kept")
    return RenamePlan(original=parsed.original, renamed=name, fields=fields, warnings=warnings)


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # unknown placeholders stay empty
        return ""


SERIES_ONLY_KEYWORDS = ("episode", "series", "season", "show", "ep")


def should_show_episode_fields(parsed: ParsedName) -> bool:
    """Only print SxxExx when the input actually looked like an episode.

    Prevents ``report S00E00`` for ordinary documents while keeping the template
    usable for series.
    """
    return parsed.season is not None or parsed.episode is not None


def guess_media_type(extension: str) -> str:
    ext = extension.lower().lstrip(".")
    if ext in {"mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts", "wmv"}:
        return "video"
    if ext in {"mp3", "m4a", "flac", "ogg", "opus", "wav", "aac", "alac"}:
        return "audio"
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "heic", "bmp"}:
        return "photo"
    if ext in {"zip", "rar", "7z", "tar", "gz"}:
        return "archive"
    if ext in {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub"}:
        return "document"
    return "file"


def clean_text(value: str, *, replace_spaces: bool = True) -> str:
    text = NOISE_RE.sub(" ", value or "")
    text = DOTS_UNDERSCORES.sub(" ", text)
    text = SPACES.sub(" ", text).strip()
    return text if replace_spaces else re.sub(r"\s+", " ", text)


def plan_rename(
    filename: str,
    template: str = DEFAULT_TEMPLATE,
    *,
    episode_offset: int = 0,
    max_length: int = 120,
    keep_extension: bool = True,
) -> RenamePlan:
    return apply_template(
        parse_name(filename),
        template,
        episode_offset=episode_offset,
        max_length=max_length,
        keep_extension=keep_extension,
    )


def describe_plan(plan: RenamePlan) -> str:
    lines = [f"{plan.original}  →  {plan.renamed}"]
    lines.extend(f"! {warning}" for warning in plan.warnings)
    return "\n".join(lines)


__all__ = [
    "DEFAULT_TEMPLATE",
    "ParsedName",
    "RenamePlan",
    "apply_template",
    "clean_text",
    "describe_plan",
    "parse_name",
    "plan_rename",
    "split_extension",
    "guess_media_type",
    "should_show_episode_fields",
]
