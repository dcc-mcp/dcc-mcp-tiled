"""Fail closed unless a release mutation targets one frozen release and bundle."""

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.verify_package_artifacts import (
        PackageValidationError,
        validate_distribution_archives,
    )
except ModuleNotFoundError:  # Direct execution places tools/ at sys.path[0].
    from verify_package_artifacts import (  # type: ignore[no-redef]
        PackageValidationError,
        validate_distribution_archives,
    )

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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_TAG_DEREFERENCES = 8
MAX_RELEASE_ASSETS = 99


class IdentityError(RuntimeError):
    """The release identity could not be proven."""


@dataclass(frozen=True)
class AssetIdentity:
    asset_id: int
    node_id: str
    name: str
    size: int
    digest: str
    state: str

    def canonical(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "id": self.asset_id,
            "name": self.name,
            "node_id": self.node_id,
            "size": self.size,
            "state": self.state,
        }


@dataclass(frozen=True)
class ReleaseSnapshot:
    release_id: int
    node_id: str
    tag_name: str
    target_commitish: str
    draft: bool
    prerelease: bool
    immutable: bool
    assets: tuple[AssetIdentity, ...]
    assets_sha256: str


def resolve_remote_tag(tag: str, *, fetch: Callable[[str], Any]) -> str:
    payload = fetch(f"git/ref/tags/{tag}")
    if not isinstance(payload, dict):
        raise IdentityError("remote release tag response is invalid")
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
        if not isinstance(payload, dict):
            raise IdentityError("remote annotated tag response is invalid")
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
    if SHA256_RE.fullmatch(manifest_sha256) is None:
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
    try:
        validate_distribution_archives(dist, version)
    except PackageValidationError as exc:
        raise IdentityError("package archive validation failed") from exc


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IdentityError(f"release {key.replace('_', ' ')} is invalid")
    return value


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise IdentityError(f"release {key.replace('_', ' ')} is invalid")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise IdentityError(f"release {key.replace('_', ' ')} is invalid")
    return value


def parse_asset_identity(payload: Any) -> AssetIdentity:
    if not isinstance(payload, dict):
        raise IdentityError("release asset identity is invalid")
    asset = AssetIdentity(
        asset_id=_required_int(payload, "id"),
        node_id=_required_string(payload, "node_id"),
        name=_required_string(payload, "name"),
        size=_required_int(payload, "size"),
        digest=_required_string(payload, "digest"),
        state=_required_string(payload, "state"),
    )
    if DIGEST_RE.fullmatch(asset.digest) is None or asset.state != "uploaded":
        raise IdentityError("release asset digest or state is invalid")
    if "/" in asset.name or "\\" in asset.name:
        raise IdentityError("release asset name is invalid")
    return asset


def asset_baseline_sha256(assets: Sequence[AssetIdentity]) -> str:
    canonical = [asset.canonical() for asset in sorted(assets, key=lambda item: item.name)]
    encoded = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_release_snapshot(
    release_id: int,
    *,
    fetch: Callable[[str], Any],
) -> ReleaseSnapshot:
    if isinstance(release_id, bool) or not isinstance(release_id, int) or release_id <= 0:
        raise IdentityError("release id is invalid")
    payload = fetch(f"releases/{release_id}")
    if not isinstance(payload, dict):
        raise IdentityError("release entity response is invalid")
    observed_id = _required_int(payload, "id")
    if observed_id != release_id:
        raise IdentityError("release id mismatch")
    assets_payload = fetch(f"releases/{release_id}/assets?per_page=100")
    if not isinstance(assets_payload, list):
        raise IdentityError("release asset baseline response is invalid")
    if len(assets_payload) > MAX_RELEASE_ASSETS:
        raise IdentityError("release asset baseline exceeds the bounded observation")
    assets = tuple(
        sorted((parse_asset_identity(item) for item in assets_payload), key=lambda item: item.name)
    )
    if len({asset.asset_id for asset in assets}) != len(assets):
        raise IdentityError("release asset ids are not unique")
    if len({asset.node_id for asset in assets}) != len(assets):
        raise IdentityError("release asset node ids are not unique")
    if len({asset.name for asset in assets}) != len(assets):
        raise IdentityError("release asset names are not unique")
    return ReleaseSnapshot(
        release_id=observed_id,
        node_id=_required_string(payload, "node_id"),
        tag_name=_required_string(payload, "tag_name"),
        target_commitish=_required_string(payload, "target_commitish"),
        draft=_required_bool(payload, "draft"),
        prerelease=_required_bool(payload, "prerelease"),
        immutable=_required_bool(payload, "immutable"),
        assets=assets,
        assets_sha256=asset_baseline_sha256(assets),
    )


def verify_release_snapshot(
    snapshot: ReleaseSnapshot,
    *,
    release_id: int,
    release_node_id: str,
    tag: str,
    commit: str,
    assets_sha256: str,
) -> None:
    if snapshot.release_id != release_id:
        raise IdentityError("release id mismatch")
    if snapshot.node_id != release_node_id:
        raise IdentityError("release node id mismatch")
    if snapshot.tag_name != tag:
        raise IdentityError("release tag name mismatch")
    if snapshot.target_commitish != commit:
        raise IdentityError("release target commit mismatch")
    if snapshot.draft or snapshot.prerelease or snapshot.immutable:
        raise IdentityError("release state does not permit the requested mutation")
    if SHA256_RE.fullmatch(assets_sha256) is None:
        raise IdentityError("frozen release asset baseline identity is invalid")
    if snapshot.assets_sha256 != assets_sha256:
        raise IdentityError("release asset baseline changed")


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IdentityError("local release identity could not be observed") from exc
    return result.stdout.strip()


def verify_versions(version: str) -> None:
    try:
        verify_version_consistency(Path.cwd(), expected=version)
    except VersionConsistencyError as exc:
        raise IdentityError("checked-out release versions changed") from exc


def _remote_fetch(repository: str, token: str) -> Callable[[str], Any]:
    def fetch(path: str) -> Any:
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
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise IdentityError("remote release identity could not be observed") from exc

    return fetch


def freeze_remote_release(*, repository: str, tag: str, sha: str, token: str) -> ReleaseSnapshot:
    if REPOSITORY_RE.fullmatch(repository) is None or not token:
        raise IdentityError("release repository identity or token is invalid")
    if not tag.startswith("v") or SEMVER_RE.fullmatch(tag[1:]) is None:
        raise IdentityError("release tag is not canonical")
    if SHA_RE.fullmatch(sha) is None:
        raise IdentityError("frozen release commit identity is invalid")
    fetch = _remote_fetch(repository, token)
    if resolve_remote_tag(tag, fetch=fetch) != sha:
        raise IdentityError("remote release tag does not match the requested commit")
    tagged_release = fetch(f"releases/tags/{tag}")
    if not isinstance(tagged_release, dict):
        raise IdentityError("tagged release response is invalid")
    release_id = _required_int(tagged_release, "id")
    snapshot = capture_release_snapshot(release_id, fetch=fetch)
    verify_release_snapshot(
        snapshot,
        release_id=release_id,
        release_node_id=snapshot.node_id,
        tag=tag,
        commit=sha,
        assets_sha256=snapshot.assets_sha256,
    )
    if snapshot.assets:
        raise IdentityError("new release asset baseline must be empty")
    if resolve_remote_tag(tag, fetch=fetch) != sha:
        raise IdentityError("remote release tag changed while freezing the release entity")
    return snapshot


def verify_release_identity(
    *,
    repository: str,
    tag: str,
    sha: str,
    version: str,
    bundle: Path,
    manifest_sha256: str,
    release_id: int,
    release_node_id: str,
    release_assets_sha256: str,
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
    fetch = _remote_fetch(repository, token)
    if resolve_remote_tag(tag, fetch=fetch) != sha:
        raise IdentityError("remote release tag moved after the bundle was built")
    snapshot = capture_release_snapshot(release_id, fetch=fetch)
    verify_release_snapshot(
        snapshot,
        release_id=release_id,
        release_node_id=release_node_id,
        tag=tag,
        commit=sha,
        assets_sha256=release_assets_sha256,
    )
    if snapshot.assets:
        raise IdentityError("release asset baseline must be empty before publication")
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
    parser.add_argument("--release-id", required=True, type=int)
    parser.add_argument("--release-node-id", required=True)
    parser.add_argument("--release-assets-sha256", required=True)
    args = parser.parse_args(argv)
    verify_release_identity(
        repository=args.repository,
        tag=args.tag,
        sha=args.sha,
        version=args.version,
        bundle=args.bundle,
        manifest_sha256=args.manifest_sha256,
        release_id=args.release_id,
        release_node_id=args.release_node_id,
        release_assets_sha256=args.release_assets_sha256,
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
