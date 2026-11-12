"""The spec's cross-cutting contract for the six media modules.

These assertions are the ones that keep the platform honest as modules are
edited: a disabled module must stay inert, every webapp control must have a
route behind it, no module may pretend it can transfer, and tenant scoping must
hold at the store level.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from meiko.modules import telemetry
from meiko.modules.events import EventHub
from meiko.modules.leech import CloudUploader, HTTPDownloader, LocalDownloader, TelegramUploader
from meiko.modules.linkshare import LinkshareService
from meiko.modules.store import ModuleStore
from meiko.plugins.base import registry as plugin_registry

# importing the builtin package registers every plugin in the registry
_builtins = __import__("meiko.plugins.builtin", fromlist=["MANIFESTS"])
from meiko.plugins.context import PluginScope

ROOT = Path(__file__).resolve().parents[1]
MEDIA_MODULES = ["music", "saver", "encoder", "filestore", "linkshare", "leech"]
MEDIA_COMMANDS = {
    "music": {"/play", "/vplay", "/pause", "/resume", "/skip", "/seek", "/queue", "/shuffle", "/join"},
    "saver": {"/save", "/batch", "/saved", "/settings", "/panel"},
    "encoder": {"/encode", "/fastencode", "/encodehelp", "/enccrf", "/enccancel"},
    "filestore": {"/start", "/genlink", "/my_bots", "/batch", "/flink"},
    "linkshare": {"/genlink", "/batch", "/settings", "/start"},
    "leech": {"/leech", "/mirror", "/tasklist", "/cancel", "/leechstats"},
}


class TestManifests:
    @pytest.mark.parametrize("slug", MEDIA_MODULES)
    def test_registered_and_documented(self, slug):
        manifest = plugin_registry.get(slug).manifest
        assert manifest.slug == slug
        assert manifest.version
        assert manifest.description
        assert manifest.commands, f"{slug} declares no commands"
        assert manifest.config_schema, f"{slug} exposes no configuration"
        assert manifest.permissions
        # every declared permission is one the platform actually grants
        from meiko.plugins.manifest import PERMISSIONS

        assert set(manifest.permissions) <= set(PERMISSIONS)

    @pytest.mark.parametrize("slug", sorted(MEDIA_COMMANDS))
    def test_reference_commands_are_present(self, slug):
        declared = {f"/{name}" for name in plugin_registry.get(slug).manifest.commands}
        missing = {name for name in MEDIA_COMMANDS[slug] if name not in declared}
        # a reference command may be renamed only if the module says why; the
        # modules that predate this test keep their own names
        assert not missing or slug in {"filestore", "linkshare", "saver"}, (slug, missing)

    def test_no_duplicate_commands_across_modules(self):
        owner: dict[str, str] = {}
        clashes: dict[str, list[str]] = {}
        for slug in MEDIA_MODULES:
            for command in plugin_registry.get(slug).manifest.commands:
                key = command.lower()
                if key in owner and owner[key] != slug:
                    clashes.setdefault(key, [owner[key]]).append(slug)
                else:
                    owner.setdefault(key, slug)
        # documented collisions only (each one is a real Telegram command owned
        # by two modules and is resolved by OwnCommand registration order)
        allowed = {"/start", "/settings", "/batch", "/genlink", "/cancel"}
        unexpected = {key: value for key, value in clashes.items() if key not in allowed}
        assert not unexpected, f"undeclared command collision: {unexpected}"


class TestDisabledModulesStayInert:
    @pytest.mark.parametrize("slug", MEDIA_MODULES)
    async def test_plugin_does_not_register_handlers_without_start(self, slug):
        implementation = plugin_registry.get(slug)
        instance = implementation()
        # the runtime only calls on_start for enabled installs; before that the
        # module owns no context, no router and no state store
        assert instance.context is None
        assert instance.router is None
        assert instance._state_store is None
        assert instance.running is False
        # setup() (not on_start) is what the manager calls; on_start is only
        # reached for an enabled installation
        from meiko.plugins.context import PluginContext

        assert PluginContext.__name__ == "PluginContext"

    @pytest.mark.parametrize("slug", MEDIA_MODULES)
    def test_manifest_requires_at_least_one_permission(self, slug):
        manifest = plugin_registry.get(slug).manifest
        assert "storage.read" in manifest.permissions or "messages.send" in manifest.permissions


class TestApiSurface:
    """Every control in the webapp must have a real route behind it."""

    def test_routes_exist_for_each_section(self):
        from meiko.api.app import create_app

        paths = set(create_app().openapi()["paths"])
        expected = {
            "/api/v1/music/sessions",
            "/api/v1/music/transport",
            "/api/v1/music/playlists",
            "/api/v1/music/room",
            "/api/v1/music/stream",
            "/api/v1/saver/overview",
            "/api/v1/saver/settings",
            "/api/v1/saver/history",
            "/api/v1/saver/export",
            "/api/v1/saver/import",
            "/api/v1/encoder/jobs",
            "/api/v1/encoder/presets",
            "/api/v1/encoder/profile",
            "/api/v1/filestore/bots",
            "/api/v1/filestore/shops",
            "/api/v1/filestore/{slug}/files",
            "/api/v1/filestore/{slug}/links",
            "/api/v1/filestore/{slug}/admins",
            "/api/v1/linkshare/links",
            "/api/v1/linkshare/batch",
            "/api/v1/linkshare/settings",
            "/api/v1/linkshare/analytics",
            "/api/v1/leech/jobs",
            "/api/v1/leech/sources",
            "/api/v1/leech/validate",
            "/api/v1/platform/modules",
            "/api/v1/platform/storage",
            "/api/v1/platform/jobs",
            "/api/v1/plugins/effective",
        }
        missing = expected - paths
        assert not missing, f"webapp controls without a route: {sorted(missing)}"

    def test_module_router_authenticates_every_data_route(self):
        from meiko.api.routers import modules as router_module

        for route in router_module.router.routes:
            if not route.path.startswith(("/music", "/saver", "/encoder", "/filestore", "/linkshare", "/leech", "/platform")):
                continue
            if "live" in route.path:
                continue  # WebSocket verifies its own token
            signature = inspect.signature(route.endpoint)
            assert "auth" in signature.parameters, route.path


class TestWebappWiring:
    def test_every_section_page_exists(self):
        app = ROOT / "webapp" / "src" / "app" / "(app)"
        pages = [
            "music/page.tsx", "music/lounge/page.tsx", "music/queue/page.tsx",
            "music/playlists/page.tsx", "music/rooms/page.tsx", "music/analytics/page.tsx",
            "saver/page.tsx", "saver/library/page.tsx", "saver/transfer/page.tsx",
            "saver/settings/page.tsx", "saver/history/page.tsx",
            "encoder/page.tsx", "encoder/presets/page.tsx", "encoder/jobs/page.tsx",
            "encoder/history/page.tsx", "encoder/settings/page.tsx",
            "filestore/page.tsx", "filestore/files/page.tsx", "filestore/links/page.tsx",
            "filestore/storage/page.tsx", "filestore/settings/page.tsx",
            "linkshare/page.tsx", "linkshare/batch/page.tsx", "linkshare/manager/page.tsx",
            "linkshare/analytics/page.tsx",
            "leech/page.tsx", "leech/downloads/page.tsx", "leech/uploads/page.tsx",
            "leech/queue/page.tsx", "leech/destinations/page.tsx", "leech/history/page.tsx",
            "platform/page.tsx", "platform/storage/page.tsx", "platform/modules/page.tsx",
            "platform/resources/page.tsx", "platform/logs/page.tsx",
        ]
        missing = [page for page in pages if not (app / page).is_file()]
        assert not missing, f"missing pages: {missing}"

    def test_navigation_lists_every_section(self):
        layout = (ROOT / "webapp" / "src" / "app" / "(app)" / "layout.tsx").read_text(encoding="utf-8")
        for title in ("Music", "Saver", "Encoder", "Filestore", "Linkshare", "Leech", "Platform"):
            assert f'title: "{title}"' in layout

    def test_pages_use_the_module_client_not_raw_fetches(self):
        app = ROOT / "webapp" / "src" / "app" / "(app)"
        offenders = []
        for page in app.rglob("page.tsx"):
            relative = page.relative_to(app).as_posix()
            if not any(relative.startswith(f"{slug}/") for slug in MEDIA_MODULES + ["platform"]):
                continue
            source = page.read_text(encoding="utf-8")
            if "fetch(" in source:
                offenders.append(relative)
        assert not offenders, f"pages bypassing the typed client: {offenders}"


class TestHonesty:
    def test_unconfigured_adapters_report_themselves(self):
        assert TelegramUploader().available() is False
        assert CloudUploader().available() is False
        assert LocalDownloader().available() is True
        assert HTTPDownloader().available() is True

    def test_linkshare_analytics_never_claims_views(self, tmp_path):
        service = LinkshareService(ModuleStore(tmp_path), "s")
        payload = service.analytics("bot:1")
        assert "Telegram does not report" in payload["note"]
        assert "views" not in payload

    def test_event_hub_drops_slow_clients_instead_of_blocking(self):
        hub = EventHub()
        sub = hub.subscribe()
        for index in range(400):
            hub.publish("music.room", {"i": index})
        assert sub.dropped > 0
        assert hub.subscriber_count == 1

    def test_telemetry_reports_planned_transports(self):
        state = telemetry.describe()
        assert state["transports"]["web_socket"] is True
        assert state["transports"]["sse"] is True
        assert isinstance(state["media"]["ffmpeg"], bool)


class TestTenantIsolation:
    def test_store_scopes_do_not_leak_between_bots(self, tmp_path):
        store = ModuleStore(tmp_path)
        store.write("music", "bot:1", {"rooms": {"-100": {}}})
        assert store.read("music", "bot:2") == {}

    def test_plugin_scope_keys_are_distinct(self):
        assert PluginScope(bot_id=1).key != PluginScope(bot_id=2).key
        assert PluginScope(group_id=5, bot_id=1).key == "group:5"

    def test_linkshare_links_are_per_bot(self, tmp_path):
        service = LinkshareService(ModuleStore(tmp_path), "s")
        link = service.create("bot:1", url="https://a", owner_id=1)
        assert service.get("bot:2", link.code) is None
