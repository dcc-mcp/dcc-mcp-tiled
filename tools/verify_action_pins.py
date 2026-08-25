"""Verify workflow action pins against commit objects in their named upstream repositories."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

PIN_RE = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^@]+)?@(?P<sha>[0-9a-f]{40})$")

APPROVED_ACTION_PINS = {
    "actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
    "actions/download-artifact": "634f93cb2916e3fdff6788551b99b062d0335ce0",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "googleapis/release-please-action": "45996ed1f6d02564a971a2fa1b5860e934307cf7",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
    "softprops/action-gh-release": "3bb12739c298aeb8a4eeaf626c5b8d85266b0e65",
}


class PinError(RuntimeError):
    """A workflow action pin is not a verified upstream commit."""


def workflow_pins(path: Path) -> list[tuple[str, str]]:
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    pins: list[tuple[str, str]] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if not uses or uses.startswith("./"):
                continue
            match = PIN_RE.fullmatch(uses)
            if match is None:
                raise PinError(f"{path}: action is not pinned to a 40-character SHA: {uses}")
            pins.append((match.group("repo"), match.group("sha")))
    if not pins:
        raise PinError(f"{path}: no remote action pins found")
    return pins


def verify_upstream_commit(repo: str, sha: str) -> None:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/git/commits/{sha}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "User-Agent": "dcc-mcp-tiled-release-pin-guard",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if os.environ.get("GITHUB_TOKEN")
        else {
            "Accept": "application/vnd.github+json",
            "User-Agent": "dcc-mcp-tiled-release-pin-guard",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise PinError(
            f"{repo}@{sha}: pin is not a reachable commit in the named upstream repository"
        ) from exc
    if payload.get("sha") != sha:
        raise PinError(f"{repo}@{sha}: upstream commit identity mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflows", nargs="+", type=Path)
    args = parser.parse_args(argv)

    pins = sorted({pin for path in args.workflows for pin in workflow_pins(path)})
    for repo, sha in pins:
        if APPROVED_ACTION_PINS.get(repo) != sha:
            raise PinError(f"{repo}@{sha}: pin is not in the approved action pin set")
        verify_upstream_commit(repo, sha)
        print(f"verified {repo}@{sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PinError as exc:
        print(f"release action pin verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
