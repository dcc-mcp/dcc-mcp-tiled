"""Fail closed unless a release mutation still targets one immutable release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from tools.verify_version_consistency import (
        VersionConsistencyError,
        verify_version_consistency,
    )
except ModuleNotFoundError:  # Direct execution places tools/ at sys.path[0].
    from verify_version_consistency import (  # type: ignore[no-redef]
        VersionConsistencyError,
        verify_version_consistency,
    )

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_TAG_DEREFERENCES = 8


class IdentityError(RuntimeError):
    """The release identity could not be proven."""


def resolve_remote_tag(
    tag: str,
    *,
    fetch: Callable[[str], dict[str, Any]],
) -> str:
    payload = fetch(f"git/ref/tags/{tag}")
    target = payload.get("object", {})
    object_type = target.get("type")
    object_sha = target.get("sha")
    for _ in range(MAX_TAG_DEREFERENCES):
        if object_type == "commit":
            if not isinstance(object_sha, str) or SHA_RE.fullmatch(object_sha) is None:
                raise IdentityError("remote release commit identity is invalid")
            return object_sha
        if object_type != "tag" or not isinstance(object_sha, str):
            raise IdentityError("remote release tag does not resolve to a commit")
        payload = fetch(f"git/tags/{object_sha}")
        target = payload.get("object", {})
        object_type = target.get("type")
        object_sha = target.get("sha")
    raise IdentityError("remote release tag dereference limit exceeded")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(bundle: Path, version: str, manifest_sha256: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None:
        raise IdentityError("release bundle manifest identity is invalid")
    manifest = bundle / "SHA256SUMS"
    if not manifest.is_file() or _sha256(manifest) != manifest_sha256:
        raise IdentityError("release bundle manifest identity changed")

    expected_names = {
        f"dcc_mcp_tiled-{version}-py3-none-any.whl",
        f"dcc_mcp_tiled-{version}.tar.gz",
    }
    dist = bundle / "dist"
    actual_names = (
        {path.name for path in dist.iterdir() if path.is_file()} if dist.is_dir() else set()
    )
    if actual_names != expected_names:
        raise IdentityError("release bundle must contain exactly the reviewed wheel and sdist")

    recorded: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  dist/([^/]+)", line)
        if match is None or match.group(2) in recorded:
            raise IdentityError("release bundle manifest is not canonical")
        recorded[match.group(2)] = match.group(1)
    if set(recorded) != expected_names:
        raise IdentityError("release bundle manifest does not name the exact artifact set")
    for name, digest in recorded.items():
        artifact = dist / name
        if artifact.stat().st_size <= 0 or _sha256(artifact) != digest:
            raise IdentityError("release artifact identity changed")


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IdentityError("local release identity could not be observed") from exc
    return result.stdout.strip()


def verify_versions(version: str) -> None:
    try:
        verify_version_consistency(Path.cwd(), expected=version)
    except VersionConsistencyError as exc:
        raise IdentityError("checked-out release versions changed") from exc


def _remote_fetch(repository: str, token: str) -> Callable[[str], dict[str, Any]]:
    def fetch(path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "dcc-mcp-tiled-release-identity-guard",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise IdentityError("remote release identity could not be observed") from exc
        if not isinstance(payload, dict):
            raise IdentityError("remote release identity response is invalid")
        return payload

    return fetch


def verify_release_identity(
    *,
    repository: str,
    tag: str,
    sha: str,
    version: str,
    bundle: Path,
    manifest_sha256: str,
    token: str,
) -> None:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise IdentityError("release repository identity is invalid")
    if tag != f"v{version}" or SEMVER_RE.fullmatch(version) is None:
        raise IdentityError("release tag is not canonical or version-bound")
    if SHA_RE.fullmatch(sha) is None:
        raise IdentityError("frozen release commit identity is invalid")
    if not token:
        raise IdentityError("remote release identity token is unavailable")
    if resolve_remote_tag(tag, fetch=_remote_fetch(repository, token)) != sha:
        raise IdentityError("remote release tag moved after the bundle was built")
    if _git("rev-parse", "--verify", "HEAD") != sha:
        raise IdentityError("checked-out release commit identity changed")
    if _git("rev-parse", "--verify", f"{tag}^{{commit}}") != sha:
        raise IdentityError("local release tag identity changed")
    verify_versions(version)
    verify_bundle(bundle, version, manifest_sha256)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    verify_release_identity(
        repository=args.repository,
        tag=args.tag,
        sha=args.sha,
        version=args.version,
        bundle=args.bundle,
        manifest_sha256=args.manifest_sha256,
        token=os.environ.get("GH_TOKEN", ""),
    )
    print("release identity verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IdentityError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"release identity verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
