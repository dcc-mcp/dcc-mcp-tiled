"""Tests for the executable pre-mutation release identity verifier."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_release_identity.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_release_identity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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
