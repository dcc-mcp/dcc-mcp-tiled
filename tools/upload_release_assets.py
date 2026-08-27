"""Preflight and transactionally attach one exact release bundle without clobbering."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    from tools.verify_release_identity import (
        REPOSITORY_RE,
        SEMVER_RE,
        SHA256_RE,
        SHA_RE,
        AssetIdentity,
        IdentityError,
        ReleaseSnapshot,
        _remote_fetch,
        asset_baseline_sha256,
        capture_release_snapshot,
        parse_asset_identity,
        verify_bundle,
        verify_release_snapshot,
    )
except ModuleNotFoundError:  # Direct execution places tools/ at sys.path[0].
    from verify_release_identity import (  # type: ignore[no-redef]
        REPOSITORY_RE,
        SEMVER_RE,
        SHA256_RE,
        SHA_RE,
        AssetIdentity,
        IdentityError,
        ReleaseSnapshot,
        _remote_fetch,
        asset_baseline_sha256,
        capture_release_snapshot,
        parse_asset_identity,
        verify_bundle,
        verify_release_snapshot,
    )

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_ASSET_BYTES = 100 * 1024 * 1024


class ReleaseAssetError(RuntimeError):
    """The exact release asset transaction could not be proven safe or complete."""


@dataclass(frozen=True)
class PlannedAsset:
    path: Path
    name: str
    size: int
    digest: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_release_assets(bundle: Path, version: str) -> tuple[PlannedAsset, ...]:
    paths = (
        bundle / "SHA256SUMS",
        bundle / "dist" / f"dcc_mcp_tiled-{version}-py3-none-any.whl",
        bundle / "dist" / f"dcc_mcp_tiled-{version}.tar.gz",
    )
    planned: list[PlannedAsset] = []
    for path in sorted(paths, key=lambda item: item.name):
        if not path.is_file():
            raise ReleaseAssetError(f"planned release asset is missing: {path.name}")
        size = path.stat().st_size
        if size <= 0 or size > MAX_ASSET_BYTES:
            raise ReleaseAssetError(f"planned release asset size is invalid: {path.name}")
        planned.append(
            PlannedAsset(
                path=path,
                name=path.name,
                size=size,
                digest=f"sha256:{_sha256(path)}",
            )
        )
    if len({asset.name for asset in planned}) != 3:
        raise ReleaseAssetError("planned release asset names are not unique")
    return tuple(planned)


def _verify_same_entity(observed: ReleaseSnapshot, frozen: ReleaseSnapshot) -> None:
    if observed.release_id != frozen.release_id:
        raise ReleaseAssetError("release id mismatch")
    if observed.node_id != frozen.node_id:
        raise ReleaseAssetError("release node id mismatch")
    if observed.tag_name != frozen.tag_name:
        raise ReleaseAssetError("release tag name mismatch")
    if observed.target_commitish != frozen.target_commitish:
        raise ReleaseAssetError("release target commit mismatch")
    if observed.draft or observed.prerelease or observed.immutable:
        raise ReleaseAssetError("release state does not permit asset upload")


def _preflight(
    frozen: ReleaseSnapshot,
    planned: Sequence[PlannedAsset],
    observed: ReleaseSnapshot,
) -> None:
    _verify_same_entity(observed, frozen)
    if observed.assets_sha256 != frozen.assets_sha256:
        raise ReleaseAssetError("release asset baseline changed")
    planned_names = {asset.name for asset in planned}
    collisions = planned_names.intersection(asset.name for asset in observed.assets)
    if collisions:
        raise ReleaseAssetError(f"release asset already exists: {sorted(collisions)[0]}")
    if observed.assets:
        raise ReleaseAssetError("release asset baseline must be empty")
    if len(planned) != 3 or len(planned_names) != 3:
        raise ReleaseAssetError("exactly three unique release assets are required")
    for asset in planned:
        if asset.size <= 0 or DIGEST_RE.fullmatch(asset.digest) is None:
            raise ReleaseAssetError("planned release asset identity is invalid")


def _verify_uploaded(uploaded: AssetIdentity, planned: PlannedAsset) -> None:
    if (
        uploaded.name != planned.name
        or uploaded.size != planned.size
        or uploaded.digest != planned.digest
        or uploaded.state != "uploaded"
    ):
        raise ReleaseAssetError(f"uploaded release asset identity mismatch: {planned.name}")


def _rollback(
    uploaded: Sequence[AssetIdentity],
    *,
    frozen: ReleaseSnapshot,
    observe: Callable[[], ReleaseSnapshot],
    delete: Callable[[int], None],
    cause: Exception,
) -> None:
    try:
        for asset in reversed(uploaded):
            delete(asset.asset_id)
        restored = observe()
        _verify_same_entity(restored, frozen)
        if restored.assets_sha256 != frozen.assets_sha256:
            raise ReleaseAssetError("release asset baseline was not restored")
    except Exception as rollback_error:
        raise ReleaseAssetError(
            "release asset upload failed; release state is indeterminate"
        ) from rollback_error
    raise ReleaseAssetError("release asset upload failed; rollback restored baseline") from cause


def apply_release_assets(
    *,
    frozen: ReleaseSnapshot,
    planned: Sequence[PlannedAsset],
    observe: Callable[[], ReleaseSnapshot],
    upload: Callable[[PlannedAsset], AssetIdentity],
    delete: Callable[[int], None],
) -> ReleaseSnapshot:
    ordered = tuple(sorted(planned, key=lambda item: item.name))
    _preflight(frozen, ordered, observe())
    uploaded: list[AssetIdentity] = []
    try:
        for asset in ordered:
            result = upload(asset)
            _verify_uploaded(result, asset)
            uploaded.append(result)
        final = observe()
        _verify_same_entity(final, frozen)
        expected = tuple(sorted(uploaded, key=lambda item: item.name))
        if final.assets != expected or final.assets_sha256 != asset_baseline_sha256(expected):
            raise ReleaseAssetError("final release asset set is incomplete or changed")
    except Exception as exc:
        _rollback(uploaded, frozen=frozen, observe=observe, delete=delete, cause=exc)
    return final


def _github_mutations(
    repository: str,
    release_id: int,
    token: str,
) -> tuple[Callable[[PlannedAsset], AssetIdentity], Callable[[int], None]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "dcc-mcp-tiled-release-asset-uploader",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def upload(asset: PlannedAsset) -> AssetIdentity:
        data = asset.path.read_bytes()
        if len(data) != asset.size or f"sha256:{hashlib.sha256(data).hexdigest()}" != asset.digest:
            raise ReleaseAssetError(f"planned release asset changed before upload: {asset.name}")
        query = urllib.parse.urlencode({"name": asset.name})
        request = urllib.request.Request(
            f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets?{query}",
            data=data,
            method="POST",
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ReleaseAssetError(f"release asset upload request failed: {asset.name}") from exc
        return parse_asset_identity(payload)

    def delete(asset_id: int) -> None:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/releases/assets/{asset_id}",
            method="DELETE",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status != 204:
                    raise ReleaseAssetError("release asset rollback response is invalid")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ReleaseAssetError("release asset rollback request failed") from exc

    return upload, delete


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-id", required=True, type=int)
    parser.add_argument("--release-node-id", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--release-assets-sha256", required=True)
    args = parser.parse_args(argv)
    if REPOSITORY_RE.fullmatch(args.repository) is None:
        raise ReleaseAssetError("release repository identity is invalid")
    if args.tag != f"v{args.version}" or SEMVER_RE.fullmatch(args.version) is None:
        raise ReleaseAssetError("release tag is not canonical or version-bound")
    if SHA_RE.fullmatch(args.sha) is None:
        raise ReleaseAssetError("frozen release commit identity is invalid")
    if SHA256_RE.fullmatch(args.release_assets_sha256) is None:
        raise ReleaseAssetError("frozen release asset baseline identity is invalid")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise ReleaseAssetError("release asset token is unavailable")
    verify_bundle(args.bundle, args.version, args.manifest_sha256)
    fetch = _remote_fetch(args.repository, token)
    frozen = capture_release_snapshot(args.release_id, fetch=fetch)
    try:
        verify_release_snapshot(
            frozen,
            release_id=args.release_id,
            release_node_id=args.release_node_id,
            tag=args.tag,
            commit=args.sha,
            assets_sha256=args.release_assets_sha256,
        )
    except IdentityError as exc:
        raise ReleaseAssetError(str(exc)) from exc
    upload, delete = _github_mutations(args.repository, args.release_id, token)
    final = apply_release_assets(
        frozen=frozen,
        planned=plan_release_assets(args.bundle, args.version),
        observe=lambda: capture_release_snapshot(args.release_id, fetch=fetch),
        upload=upload,
        delete=delete,
    )
    print(f"attached {len(final.assets)} exact release assets to release {final.release_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IdentityError, ReleaseAssetError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release asset publication failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
