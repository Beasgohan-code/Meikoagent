"""``code_runner`` manifest.

This plugin is the platform's explicit code-execution surface. It is *off* by
default twice over:

1. the installation must be enabled **and** configured, and
2. the platform owner must open the ``code_exec`` capability gate and place the
   caller on its allow-list (see :mod:`meiko.plugins.support.gates`).

Nothing is executed in-process: every snippet runs in a separate, resource
limited interpreter process (``python -I -S``, no network via a stub socket
layer, capped CPU/memory/output, wall-clock timeout).
"""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="code_runner",
    name="Code Runner (sandboxed)",
    version="1.0.0",
    description=(
        "Runs short Python snippets in an isolated interpreter process. Double-gated: "
        "owner capability + per-caller allow-list, with CPU/memory/time/output limits."
    ),
    category="developer",
    icon="code",
    permissions=["messages.send", "storage.read", "storage.write"],
    commands=["run", "py", "runlang", "sandbox"],
    events=["message"],
    resources={"cpu": "medium", "memory_mb": 256, "timeout_seconds": 15},
    config_schema={
        "enabled": {"type": "bool", "default": False, "label": "Allow code execution at all"},
        "languages": {
            "type": "list",
            "default": ["python", "node", "bash"],
            "label": "Allowed language keys",
        },
        "timeout_seconds": {"type": "int", "default": 10, "label": "Wall-clock timeout (s)"},
        "max_output_chars": {"type": "int", "default": 3500, "label": "Output cap (chars)"},
        "max_source_chars": {"type": "int", "default": 4000, "label": "Source cap (chars)"},
        "memory_mb": {"type": "int", "default": 256, "label": "Address space limit (MB)"},
        "cpu_seconds": {"type": "int", "default": 5, "label": "CPU time limit (s)"},
        "daily_runs": {"type": "int", "default": 50, "label": "Runs per user per day"},
        "allow_network": {"type": "bool", "default": False, "label": "Allow outbound sockets"},
        "allow_file_write": {"type": "bool", "default": False, "label": "Allow writing files"},
        "echo_source": {"type": "bool", "default": True, "label": "Echo the snippet in the reply"},
        "notify_owner": {"type": "bool", "default": True, "label": "DM the bot owner on each run"},
    },
)
