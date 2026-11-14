"""HMAC keys must never be values an attacker can recompute.

Three separate components signed security tokens with a fallback that was either a
literal in the source or derived from public data:

* ``LinkshareService`` (API claim tokens)      -> ``"meiko-linkshare"``
* the linkshare verify button (plugin)         -> ``"<chat id>:linkshare"``
* ``LinkRegistry`` (plugin state)               -> ``"meiko-links"``

Anybody can produce those strings, so they can mint a token and pass the gate.
These tests fail if that class of fallback returns.
"""

from __future__ import annotations

import pytest


class TestDerivedSecrets:
    def test_a_derived_secret_is_long_and_not_a_known_constant(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        from meiko.config import get_settings
        from meiko.security.crypto import signing_secret

        get_settings.cache_clear()
        try:
            secret = signing_secret("linkshare-plugin")
        finally:
            get_settings.cache_clear()
        assert len(secret) >= 32
        assert "linkshare" not in secret.lower()
        assert secret not in ("meiko-links", "meiko-linkshare")

    def test_purposes_do_not_share_a_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        from meiko.config import get_settings
        from meiko.security.crypto import signing_secret

        get_settings.cache_clear()
        try:
            assert signing_secret("a") != signing_secret("b")
            # stable across calls, so existing links keep verifying
            assert signing_secret("a") == signing_secret("a")
        finally:
            get_settings.cache_clear()

    def test_the_gate_never_uses_the_public_constant(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        from meiko.config import get_settings
        from meiko.plugins.builtin.linkshare.gate import LinkRegistry

        get_settings.cache_clear()
        try:
            registry = LinkRegistry({}, "")
        finally:
            get_settings.cache_clear()
        assert registry.secret != "meiko-links"
        assert len(registry.secret) >= 32

    def test_the_plugin_does_not_sign_with_a_guessable_scope_string(self, monkeypatch):
        """`on_start` used ``f"{scope.key}:linkshare"`` when no secret was configured."""
        import inspect

        from meiko.plugins.builtin.linkshare import plugin as plugin_module

        source = inspect.getsource(plugin_module.LinkSharePlugin.on_start)
        assert "signing_secret(" in source
        assert "scope.key}:{MANIFEST.slug}" not in source


class TestNoLiteralsRemain:
    def test_the_old_constants_are_gone_from_the_source(self):
        """Parsed with `ast`, so prose in a docstring is not mistaken for a value."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "meiko"
        banned = {"meiko-linkshare", "meiko-links"}
        offenders = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(errors="ignore"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = getattr(node, "body", [])
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                        docstrings.add(id(body[0].value))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in banned
                    and id(node) not in docstrings
                ):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        assert not offenders, f"public signing constants are back: {offenders}"
