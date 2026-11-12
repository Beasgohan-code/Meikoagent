"""ai plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="ai",
    name="AI Assistant",
    version="1.0.0",
    description="Answers /ask questions through a configured OpenAI-compatible endpoint.",
    category="ai",
    icon="orb",
    permissions=["messages.send", "http.outbound", "storage.read", "storage.write"],
    commands=["ask", "ai"],
    events=["message"],
    config_schema={
        "api_base": {
            "type": "string",
            "default": "https://api.openai.com/v1",
            "label": "OpenAI-compatible base URL",
        },
        "model": {"type": "string", "default": "gpt-4o-mini", "label": "Model"},
        "api_key": {"type": "secret", "required": True, "label": "API key"},
        "max_tokens": {"type": "int", "default": 400, "label": "Max tokens"},
        "system_prompt": {
            "type": "string",
            "default": "You are MEIKO, a concise Telegram assistant.",
            "label": "System prompt",
        },
    },
)
