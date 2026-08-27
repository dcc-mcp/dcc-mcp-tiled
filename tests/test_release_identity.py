"""Tests for the executable pre-mutation release identity verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_release_identity.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_release_identity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_remote_resolver_is_bounded_and_peels_annotated_tags() -> None:
    module = _module()
    commit = "a" * 40
    replies = {
        "git/ref/tags/v0.4.0": {"object": {"type": "tag", "sha": "b" * 40}},
        f"git/tags/{'b' * 40}": {"object": {"type": "commit", "sha": commit}},
    }
    seen: list[str] = []

    def fetch(path: str) -> dict:
        seen.append(path)
        return replies[path]

    assert module.resolve_remote_tag("v0.4.0", fetch=fetch) == commit
    assert seen == ["git/ref/tags/v0.4.0", f"git/tags/{'b' * 40}"]


def test_bundle_verifier_requires_exact_artifact_set_and_hashes(tmp_path: Path) -> None:
    module = _module()
    bundle = tmp_path / "bundle"
    dist = bundle / "dist"
    dist.mkdir(parents=True)
    expected = [
        dist / "dcc_mcp_tiled-0.4.0-py3-none-any.whl",
        dist / "dcc_mcp_tiled-0.4.0.tar.gz",
    ]
    for index, path in enumerate(expected):
        path.write_bytes(f"artifact-{index}".encode())
    sums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  dist/{path.name}\n" for path in expected
    )
    manifest = bundle / "SHA256SUMS"
    manifest.write_text(sums, encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()

    module.verify_bundle(bundle, "0.4.0", manifest_sha)
    (dist / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(module.IdentityError, match="exactly"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_release_snapshot_freezes_entity_and_existing_asset_baseline() -> None:
    module = _module()
    commit = "a" * 40
    release_id = 42
    node_id = "RE_kwDOFrozen"
    empty_assets_sha256 = hashlib.sha256(b"[]").hexdigest()
    responses = {
        f"releases/{release_id}": {
            "id": release_id,
            "node_id": node_id,
            "tag_name": "v0.4.0",
            "target_commitish": commit,
            "draft": False,
            "prerelease": False,
            "immutable": False,
        },
        f"releases/{release_id}/assets?per_page=100": [],
    }

    snapshot = module.capture_release_snapshot(
        release_id,
        fetch=lambda path: responses[path],
    )

    assert snapshot.release_id == release_id
    assert snapshot.node_id == node_id
    assert snapshot.assets_sha256 == empty_assets_sha256
    module.verify_release_snapshot(
        snapshot,
        release_id=release_id,
        release_node_id=node_id,
        tag="v0.4.0",
        commit=commit,
        assets_sha256=empty_assets_sha256,
    )


def test_release_snapshot_rejects_same_tag_recreated_release() -> None:
    module = _module()
    commit = "b" * 40
    original_assets_sha256 = hashlib.sha256(b"[]").hexdigest()
    recreated = {
        "id": 43,
        "node_id": "RE_kwDOReplacement",
        "tag_name": "v0.4.0",
        "target_commitish": commit,
        "draft": False,
        "prerelease": False,
        "immutable": False,
    }

    with pytest.raises(module.IdentityError, match="release id mismatch"):
        module.capture_release_snapshot(
            42,
            fetch=lambda path: recreated if path == "releases/42" else [],
        )

    original = module.ReleaseSnapshot(
        release_id=42,
        node_id="RE_kwDOOriginal",
        tag_name="v0.4.0",
        target_commitish=commit,
        draft=False,
        prerelease=False,
        immutable=False,
        assets=(),
        assets_sha256=original_assets_sha256,
    )
    with pytest.raises(module.IdentityError, match="release node id mismatch"):
        module.verify_release_snapshot(
            original,
            release_id=42,
            release_node_id="RE_kwDOReplacement",
            tag="v0.4.0",
            commit=commit,
            assets_sha256=original_assets_sha256,
        )


def test_release_snapshot_digest_changes_when_asset_identity_changes() -> None:
    module = _module()
    commit = "c" * 40

    def snapshot(size: int):
        release = {
            "id": 42,
            "node_id": "RE_kwDOFrozen",
            "tag_name": "v0.4.0",
            "target_commitish": commit,
            "draft": False,
            "prerelease": False,
            "immutable": False,
        }
        assets = [
            {
                "id": 101,
                "node_id": "RA_kwDOAsset",
                "name": "dcc_mcp_tiled-0.4.0-py3-none-any.whl",
                "size": size,
                "digest": "sha256:" + "d" * 64,
                "state": "uploaded",
            }
        ]
        return module.capture_release_snapshot(
            42,
            fetch=lambda path: assets if path.endswith("assets?per_page=100") else release,
        )

    assert snapshot(10).assets_sha256 != snapshot(11).assets_sha256
    canonical_empty = json.dumps([], separators=(",", ":"), sort_keys=True).encode()
    assert hashlib.sha256(canonical_empty).hexdigest() == hashlib.sha256(b"[]").hexdigest()


def test_freeze_recaptures_the_exact_release_id_and_requires_empty_assets(monkeypatch) -> None:
    module = _module()
    commit = "d" * 40
    release = {
        "id": 42,
        "node_id": "RE_kwDOFrozen",
        "tag_name": "v0.4.0",
        "target_commitish": commit,
        "draft": False,
        "prerelease": False,
        "immutable": False,
    }
    seen: list[str] = []

    def fetch(path: str):
        seen.append(path)
        if path == "git/ref/tags/v0.4.0":
            return {"object": {"type": "commit", "sha": commit}}
        if path == "releases/tags/v0.4.0":
            return {"id": 42}
        if path == "releases/42":
            return release
        if path == "releases/42/assets?per_page=100":
            return []
        raise AssertionError(path)

    monkeypatch.setattr(module, "_remote_fetch", lambda repository, token: fetch)
    frozen = module.freeze_remote_release(
        repository="dcc-mcp/dcc-mcp-tiled",
        tag="v0.4.0",
        sha=commit,
        token="test-token",
    )

    assert frozen.release_id == 42
    assert seen == [
        "git/ref/tags/v0.4.0",
        "releases/tags/v0.4.0",
        "releases/42",
        "releases/42/assets?per_page=100",
        "git/ref/tags/v0.4.0",
    ]
