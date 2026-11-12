"""Builtin plugin packages.

Importing this package registers every builtin implementation in the plugin
registry.  Nothing is *started* here – lifecycle is owned by the manager.
"""

from meiko.plugins.builtin import (  # noqa: F401
    ai,
    analytics,
    anti_spam,
    automation,
    developer,
    filters,
    integrations,
    reports,
    security,
    utilities,
    welcome,
)
from meiko.plugins.manifest import PluginManifest

BUILTIN_MANIFESTS: list[PluginManifest] = [
    anti_spam.MANIFEST,
    welcome.MANIFEST,
    filters.MANIFEST,
    reports.MANIFEST,
    analytics.MANIFEST,
    automation.MANIFEST,
    security.MANIFEST,
    utilities.MANIFEST,
    ai.MANIFEST,
    integrations.MANIFEST,
    developer.MANIFEST,
]

__all__ = ["BUILTIN_MANIFESTS"]
