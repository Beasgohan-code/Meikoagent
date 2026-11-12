"""Integrations plugin: outbound webhooks and RSS-style feeds."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="integrations",
    name="Integrations",
    version="1.0.0",
    description="Signed outbound webhooks for moderation and activity events.",
    category="integrations",
    icon="link",
    permissions=["http.outbound", "storage.read", "storage.write", "events.subscribe"],
    commands=[],
    events=["moderation", "join", "message"],
    config_schema={
        "webhook_url": {"type": "string", "default": "", "required": True, "label": "Webhook URL"},
        "secret": {"type": "secret", "default": "", "label": "HMAC signing secret"},
        "timeout_seconds": {"type": "int", "default": 10, "label": "Request timeout"},
    },
)


@plugin
class IntegrationsPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        if not str(self.config("webhook_url", "") or ""):
            ctx.log("warning", "integrations plugin enabled without a webhook URL")
        ctx.subscribe("moderation")

    async def sign(self, body: bytes) -> str:
        secret = str(self.config("secret", "") or "")
        if not secret:
            return ""
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    async def deliver(self, event: str, payload: dict) -> dict:
        ctx = self.context
        assert ctx is not None
        url = str(self.config("webhook_url", "") or "")
        if not url:
            return {"ok": False, "reason": "webhook not configured"}
        body = json.dumps({"event": event, "ts": int(time.time()), "data": payload}).encode()
        headers = {"content-type": "application/json"}
        signature = self.sign(body)
        if signature:
            headers["x-meiko-signature"] = signature
        import httpx

        try:
            async with httpx.AsyncClient(timeout=int(self.config("timeout_seconds", 10))) as client:
                response = await client.post(url, content=body, headers=headers)
            ctx.log("info", "webhook delivered", event=event, status=response.status_code)
            return {"ok": response.status_code < 300, "status": response.status_code}
        except Exception as exc:
            ctx.log("error", "webhook delivery failed", event=event, error=str(exc))
            return {"ok": False, "error": str(exc)}


__all__ = ["IntegrationsPlugin", "MANIFEST"]
