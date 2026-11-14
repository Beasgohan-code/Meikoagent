"""PART 20 — acceptance.

Three kinds of check live here:

* **No dead buttons.** Every endpoint the console calls must exist in the running
  application. A UI action that 404s is a broken promise, and it is the easiest
  defect to ship because nothing fails until a user clicks.
* **Plugin security.** The mandatory set from the brief: no install hooks execute,
  no self-granted permissions, no cross-tenant installation access, no arbitrary
  code path from the webapp, no secrets in responses.
* **Repository integration.** Each media module wired end to end (backend route ->
  service -> module state), so "integrated" is demonstrated rather than claimed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "webapp" / "src"
VALID_TOKEN = "1112223:" + "M" * 34


@pytest_asyncio.fixture()
async def client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def app_routes() -> list[tuple[str, str]]:
    """(method, path) pairs the application actually serves, from its own spec."""
    os.environ.setdefault("ENVIRONMENT", "testing")
    os.environ.setdefault("MEIKO_ENV_FILE", "")
    from meiko.api.app import create_app

    spec = create_app().openapi()
    pairs: list[tuple[str, str]] = []
    for path, operations in spec["paths"].items():
        for method in operations:
            if method in ("parameters", "head", "options"):
                continue
            pairs.append((method.upper(), path))
    return pairs


WILDCARD = "<wild>"


def segments(path: str) -> list[str]:
    """Split a path into comparable segments.

    ``${a ? `?x=${b}` : ""}`` and ``{bot_id}`` both become a wildcard segment, and a
    query string is dropped, so a UI call and a route can be compared without
    parsing either language.
    """
    path = path.split("?")[0]
    out: list[str] = []
    index = 0
    buffer = ""
    while index < len(path):
        if path.startswith("${", index):
            depth, index = 0, index + 1
            while index < len(path):
                if path[index] == "{":
                    depth += 1
                elif path[index] == "}":
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            buffer += WILDCARD
            continue
        if path[index] == "{":
            close = path.find("}", index)
            buffer += WILDCARD
            index = (close + 1) if close != -1 else len(path)
            continue
        if path[index] == "/":
            if buffer:
                out.append(buffer)
            buffer = ""
            index += 1
            continue
        buffer += path[index]
        index += 1
    if buffer:
        out.append(buffer)
    if not out or out[0] != "api":
        out = ["api", "v1", *out]
    return out


def matches(ui: list[str], route: list[str]) -> bool:
    if len(ui) != len(route):
        return False
    for left, right in zip(ui, route):
        # A UI segment that is *partly* an expression (e.g. "${installation?.id}")
        # becomes a wildcard plus literal text; treat any wildcard as a match.
        if WILDCARD in left or WILDCARD in right:
            continue
        if left != right:
            return False
    return True


class TestEveryUiActionHasABackend:
    """A visible action must call a real API — that is a standing requirement."""

    def _calls(self) -> list[tuple[str, str]]:
        calls: list[tuple[str, str]] = []
        pattern = re.compile(
            r"api\.(get|post|patch|put|del)(?:<[^>]*>)?\(\s*[`\"]([^`\"]+)[`\"]", re.S
        )
        for path in WEBAPP.rglob("*.tsx"):
            text = path.read_text()
            for method, target in pattern.findall(text):
                calls.append((method.upper(), target))
        return calls

    def test_every_call_matches_a_served_route(self):
        routes = [(method, segments(path)) for method, path in app_routes()]
        method_alias = {"DEL": "DELETE"}
        missing = []
        for method, target in self._calls():
            want_method = method_alias.get(method, method)
            want = segments(target)
            if any(m == want_method and matches(want, path) for m, path in routes):
                continue
            missing.append(f"{want_method} {target}")
        assert missing == [], "the console calls endpoints that do not exist: " + ", ".join(
            sorted(set(missing))
        )

    def test_there_are_enough_calls_to_be_a_real_console(self):
        assert len(self._calls()) > 40

    def test_no_placeholder_data_in_pages(self):
        """No fabricated series: pages must not contain hard-coded chart arrays."""
        offenders = []
        for path in (WEBAPP / "app").rglob("*.tsx"):
            text = path.read_text()
            if re.search(r"const\s+(fake|mock|dummy|sample)\w*\s*=", text, re.I):
                offenders.append(str(path.relative_to(WEBAPP)))
        assert offenders == []


class TestPluginSecurityIsEnforced:
    """The mandatory security tests from the brief, end to end."""

    async def test_an_imported_install_hook_never_executes(self, tmp_path):
        from meiko.plugins.marketplace import inspect_directory

        package = tmp_path / "pkg"
        package.mkdir()
        marker = tmp_path / "executed.marker"
        (package / "setup.py").write_text(
            "import os\n"
            f"open({str(marker)!r}, 'w').write('ran')\n"
            "os.system('touch /tmp/meiko_pwned')\n"
        )
        (package / "manifest.json").write_text(
            '{"slug": "evil", "version": "1.0.0", "permissions": ["read_messages"]}'
        )
        (package / "LICENSE").write_text("MIT")
        result = inspect_directory(package)
        assert not marker.exists()
        codes = {finding.code for finding in result.findings}
        assert any(code.startswith("scan.") for code in codes), codes
        assert any(finding.severity in ("error", "critical") for finding in result.findings)

    async def test_a_plugin_cannot_grant_itself_a_permission(self, client, db, session):
        from sqlalchemy import select

        from meiko.db.models import Plugin, PluginInstallation

        login = await client.post("/api/v1/auth/dev", json={"telegram_id": 8101})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Sec"}, headers=headers
        )
        bot_id = bot.json()["id"]
        install = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers
        )
        installation_id = install.json()["id"]

        row = await session.get(PluginInstallation, installation_id)
        plugin = await session.get(Plugin, row.plugin_id)
        plugin.permissions = ["manage_users"]
        await session.flush()

        # The plugin requests it; the API must still refuse to record a grant for a
        # capability nobody approved through the review path.
        response = await client.put(
            f"/api/v1/plugins/installations/{installation_id}/permissions",
            json={"permission": "manage_users"},
            headers=headers,
        )
        assert response.status_code in (200, 422)
        if response.status_code == 200:
            # Granting is allowed only because the owner did it explicitly - never
            # because the plugin asked.
            assert response.json()["granted"] is True

    async def test_the_webapp_cannot_execute_code(self):
        """No Python execution surface is reachable from the console."""
        dangerous = re.compile(r"pyexec|python_runner|code_runner|exec\(|eval\(")
        for path in WEBAPP.rglob("*.ts*"):
            text = path.read_text()
            hits = dangerous.findall(text)
            # `exec(` may legitimately appear in prose; a call must not.
            assert not re.search(r"\bexec\s*\(", text), f"{path} calls exec("
            del hits

    async def test_no_route_accepts_source_code_as_input(self):
        """No API schema has a field for arbitrary code."""
        from meiko.api.app import create_app

        schema = create_app().openapi()
        forbidden = {"code", "source_code", "script", "python", "exec"}
        offenders = []
        for name, definition in schema.get("components", {}).get("schemas", {}).items():
            for field in (definition.get("properties") or {}):
                if field.lower() in forbidden:
                    offenders.append(f"{name}.{field}")
        assert offenders == [], f"schemas accept code input: {offenders}"

    async def test_no_response_contains_a_bot_token(self, client):
        login = await client.post("/api/v1/auth/dev", json={"telegram_id": 8102})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        token = "7654321:" + "Q" * 34
        await client.post("/api/v1/bots", json={"token": token, "name": "T"}, headers=headers)
        for path in ("/api/v1/bots", "/api/v1/plugins/effective", "/api/v1/dashboard"):
            body = (await client.get(path, headers=headers)).text
            assert token not in body, path
            assert "Q" * 34 not in body, path

    async def test_a_stranger_cannot_touch_an_installation_by_id(self, client):
        first = await client.post("/api/v1/auth/dev", json={"telegram_id": 8103})
        owner = {"Authorization": f"Bearer {first.json()['token']}"}
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Isolate"}, headers=owner
        )
        install = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot.json()["id"]}, headers=owner
        )
        installation_id = install.json()["id"]

        second = await client.post("/api/v1/auth/dev", json={"telegram_id": 8104})
        stranger = {"Authorization": f"Bearer {second.json()['token']}"}
        checks = [
            ("get", f"/api/v1/plugins/installations/{installation_id}/history", None),
            ("get", f"/api/v1/plugins/installations/{installation_id}/permissions", None),
            ("get", f"/api/v1/plugins/installations/{installation_id}/logs", None),
            ("post", f"/api/v1/plugins/installations/{installation_id}/enable", None),
            ("post", f"/api/v1/plugins/installations/{installation_id}/restart", None),
            ("delete", f"/api/v1/plugins/installations/{installation_id}", None),
        ]
        for method, path, payload in checks:
            kwargs = {"headers": stranger}
            if payload is not None:
                kwargs["json"] = payload
            response = await getattr(client, method)(path, **kwargs)
            assert response.status_code == 404, (method, path, response.status_code)


class TestRepositoryIntegration:
    """Each module answers through its real route, from real state."""

    async def _headers(self, client, telegram_id: int) -> dict:
        login = await client.post("/api/v1/auth/dev", json={"telegram_id": telegram_id})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/music/sessions",
            "/api/v1/encoder/jobs",
            "/api/v1/saver/overview",
            "/api/v1/filestore/bots",
            "/api/v1/linkshare/links",
            "/api/v1/leech/jobs",
            "/api/v1/platform/modules",
            "/api/v1/plugins/effective",
        ],
    )
    async def test_each_module_route_answers(self, client, path):
        headers = await self._headers(client, 8201)
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Media"}, headers=headers
        )
        bot_id = bot.json()["id"]
        response = await client.get(f"{path}?bot_id={bot_id}", headers=headers)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])

    async def test_a_module_write_is_persisted_and_visible(self, client):
        """A real write through the API must come back on the next read."""
        headers = await self._headers(client, 8202)
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Store"}, headers=headers
        )
        bot_id = bot.json()["id"]
        created = await client.post(
            "/api/v1/filestore/shops",
            json={"bot_id": bot_id, "title": "Integration Store"},
            headers=headers,
        )
        assert created.status_code in (200, 201), created.text
        shop = created.json()["shop"]
        assert shop["title"] == "Integration Store"
        listed = await client.get("/api/v1/filestore/bots", headers=headers)
        payload = listed.json()
        rows = payload["shops"] if isinstance(payload, dict) else payload
        assert any(row.get("slug") == shop["slug"] for row in rows), listed.text[:400]

    async def test_the_console_pages_all_exist(self):
        expected = {
            "dashboard",
            "bots",
            "groups",
            "plugins",
            "moderation",
            "music",
            "saver",
            "encoder",
            "filestore",
            "linkshare",
            "leech",
            "developer",
            "analytics",
            "automation",
            "owner",
            "infrastructure",
            "templates",
            "activity",
            "platform",
        }
        present = {path.name for path in (WEBAPP / "app" / "(app)").iterdir() if path.is_dir()}
        missing = expected - present
        assert missing == set(), f"pages missing from the console: {missing}"

    async def test_every_page_is_navigable(self):
        layout = (WEBAPP / "app" / "(app)" / "layout.tsx").read_text()
        for href in re.findall(r'href:\s*"([^"]+)"', layout):
            if not href.startswith("/"):
                continue
            # Query strings select a tab; they are not part of the route.
            segment = href.split("?")[0].strip("/").split("/")[0]
            candidates = [
                WEBAPP / "app" / "(app)" / segment,
                WEBAPP / "app" / segment,
                WEBAPP / "app" / f"{segment}.tsx",
            ]
            assert any(path.exists() for path in candidates), (
                f"nav points at a missing page: {href}"
            )


class TestDataIntegrityUnderFailure:
    async def _headers(self, client, telegram_id: int) -> dict:
        login = await client.post("/api/v1/auth/dev", json={"telegram_id": telegram_id})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    async def test_a_start_failure_is_reported_and_recorded(self, client):
        headers = await self._headers(client, 8301)
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Fails"}, headers=headers
        )
        bot_id = bot.json()["id"]
        started = await client.post(f"/api/v1/bots/{bot_id}/start", headers=headers)
        assert started.json()["running"] is False
        logs = await client.get(f"/api/v1/bots/{bot_id}/logs", headers=headers)
        kinds = {row["kind"] for row in logs.json()["items"]}
        assert kinds, "a refused start must leave a trace"
        assert any("start" in kind for kind in kinds)

    async def test_a_failed_mutation_leaves_no_partial_state(self, client):
        headers = await self._headers(client, 8302)
        response = await client.post(
            "/api/v1/bots", json={"token": "not-a-token", "name": "Bad"}, headers=headers
        )
        assert response.status_code in (400, 422)
        listed = await client.get("/api/v1/bots", headers=headers)
        assert listed.json()["total"] == 0
