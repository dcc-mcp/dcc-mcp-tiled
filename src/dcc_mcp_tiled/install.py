"""Compatibility entry point that verifies the official Tiled CLI runtime."""

from __future__ import annotations

import json

from .bridge import TiledCli


def main() -> None:
    """Print a machine-readable runtime check; no fake plug-in is installed."""
    status = TiledCli.from_env().status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not status.get("ready"):
        raise SystemExit(1)
