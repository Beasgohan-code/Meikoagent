"""Builtin plugin packages.

Importing this package registers every builtin implementation in the plugin
registry.  Nothing is *started* here – lifecycle is owned by the manager.

Every plugin is a package (``<slug>/manifest.py`` + logic modules + ``plugin.py``)
so that the interesting behaviour stays importable and unit-testable without a
running bot, bot token or database.
"""

from meiko.plugins.builtin import (  # noqa: F401
    ai,
    analytics,
    anti_spam,
    automation,
    code_runner,
    developer,
    encoder,
    file_store,
    filestore,
    filters,
    integrations,
    keep_alive,
    leech,
    linkshare,
    music,
    post_maker,
    renamer,
    reports,
    saver,
    security,
    utilities,
    welcome,
)
from meiko.plugins.manifest import PluginManifest

BUILTIN_MANIFESTS: list[PluginManifest] = [
    # moderation & security
    anti_spam.MANIFEST,
    security.MANIFEST,
    welcome.MANIFEST,
    filters.MANIFEST,
    reports.MANIFEST,
    analytics.MANIFEST,
    # automation & management
    automation.MANIFEST,
    utilities.MANIFEST,
    post_maker.MANIFEST,
    keep_alive.MANIFEST,
    # media & storage
    file_store.MANIFEST,
    saver.MANIFEST,
    renamer.MANIFEST,
    music.MANIFEST,
    encoder.MANIFEST,
    filestore.MANIFEST,
    linkshare.MANIFEST,
    leech.MANIFEST,
    # platform
    ai.MANIFEST,
    integrations.MANIFEST,
    developer.MANIFEST,
    code_runner.MANIFEST,
]

__all__ = ["BUILTIN_MANIFESTS"]
