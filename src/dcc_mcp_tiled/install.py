"""Install the Tiled Python plug-in into a user plug-in directory."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def default_plugin_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        return root / "TILED/3.0/plug-ins"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "TILED/3.0/plug-ins"


def install(destination: Path | None = None) -> Path:
    target = (destination or default_plugin_dir()).expanduser().resolve() / "dcc_mcp_tiled"
    target.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "tiled_plugin" / "dcc_mcp_tiled.py"
    if not source.is_file():
        source = (
            Path(__file__).resolve().parents[2]
            / "bridge"
            / "tiled-plugin"
            / "dcc_mcp_tiled.py"
        )
    if not source.is_file():
        raise FileNotFoundError(f"Bundled TILED plug-in not found: {source}")
    shutil.copy2(source, target / source.name)
    if os.name != "nt":
        (target / source.name).chmod(0o755)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path)
    print(install(parser.parse_args().destination))
