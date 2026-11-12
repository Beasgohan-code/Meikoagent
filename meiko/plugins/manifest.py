"""Typed plugin manifests.

A manifest is the plugin's contract: identity, compatibility, permissions it
needs and the configuration it accepts.  Nothing is executed because a manifest
exists – execution requires an approved manifest, matching compatibility, a
satisfied permission grant and an explicit owner enable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_NAMES = ("meiko_plugin.json", "meiko_plugin.yaml", "plugin.json")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")

#: Permissions a plugin may request.  Anything else fails validation.
PERMISSIONS = {
    "moderation.read": "read moderation history",
    "moderation.write": "warn/mute/kick/ban members",
    "groups.read": "read group metadata and settings",
    "groups.write": "change group settings",
    "messages.send": "send messages as the bot",
    "messages.delete": "delete messages",
    "storage.read": "read plugin key/value storage",
    "storage.write": "write plugin key/value storage",
    "scheduler": "register scheduled jobs",
    "events.subscribe": "receive platform events",
    "users.read": "read platform user records for this tenant",
    "http.outbound": "call external HTTP services",
    "ai.invoke": "call the AI integration",
}

CATEGORIES = {
    "moderation",
    "security",
    "management",
    "automation",
    "utilities",
    "analytics",
    "integrations",
    "developer",
    "ai",
}


class ManifestError(ValueError):
    """Invalid or incompatible manifest."""


def version_tuple(value: str) -> tuple[int, int, int]:
    core = str(value or "0.0.0").lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for chunk in core.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


@dataclass
class PluginManifest:
    slug: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = "MEIKO AGENT"
    category: str = "utilities"
    icon: str = "plug"
    license: str = "MIT"
    homepage: str = ""
    entry: str = ""
    source: str = "builtin"
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    min_platform_version: str = "1.0.0"
    max_platform_version: str = ""
    config_schema: dict[str, Any] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    scheduler: list[dict[str, Any]] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    approved: bool = False

    # -- validation ------------------------------------------------------
    def validate(self, platform_version: str = "1.0.0") -> None:
        if not SLUG_RE.match(self.slug):
            raise ManifestError(
                f"invalid plugin slug {self.slug!r}: lowercase letters, digits and _ only"
            )
        if not VERSION_RE.match(self.version):
            raise ManifestError(f"invalid version {self.version!r} for plugin {self.slug}")
        if self.category not in CATEGORIES:
            raise ManifestError(f"unknown category {self.category!r}")
        unknown = [p for p in self.permissions if p not in PERMISSIONS]
        if unknown:
            raise ManifestError(f"unknown permissions: {', '.join(unknown)}")
        if self.source == "builtin":
            return
        if not self.approved:
            raise ManifestError(
                "third-party plugin is not approved by the platform owner and cannot be loaded"
            )
        if not self.checksum:
            raise ManifestError("third-party plugin requires a checksum")
        current = version_tuple(platform_version)
        if version_tuple(self.min_platform_version) > current:
            raise ManifestError(
                f"plugin {self.slug} requires platform >= {self.min_platform_version}"
            )
        if self.max_platform_version and version_tuple(self.max_platform_version) < current:
            raise ManifestError(
                f"plugin {self.slug} supports platform <= {self.max_platform_version}"
            )

    def validate_config_schema(self) -> list[str]:
        errors = []
        for key, spec in (self.config_schema or {}).items():
            if not isinstance(spec, dict):
                errors.append(f"{key}: schema entry must be an object")
                continue
            if spec.get("type", "string") not in (
                "string",
                "int",
                "float",
                "bool",
                "list",
                "secret",
                "choice",
            ):
                errors.append(f"{key}: unsupported type {spec.get('type')!r}")
        return errors

    # -- (de)serialisation ----------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "icon": self.icon,
            "license": self.license,
            "homepage": self.homepage,
            "entry": self.entry,
            "source": self.source,
            "permissions": list(self.permissions),
            "dependencies": list(self.dependencies),
            "min_platform_version": self.min_platform_version,
            "max_platform_version": self.max_platform_version,
            "config_schema": self.config_schema,
            "commands": self.commands,
            "events": self.events,
            "scheduler": self.scheduler,
            "resources": self.resources,
            "checksum": self.checksum,
            "approved": self.approved,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_file(cls, path: Path) -> "PluginManifest":
        import json

        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ManifestError("PyYAML is required to read YAML manifests") from exc
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ManifestError("manifest must be an object")
        return cls.from_dict(data)


def compute_checksum(directory: Path) -> str:
    """Deterministic checksum over a plugin directory (excluding caches)."""
    digest = hashlib.sha256()
    for file in sorted(directory.rglob("*")):
        if file.is_dir() or "__pycache__" in file.parts or file.name.startswith("."):
            continue
        digest.update(file.relative_to(directory).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()
