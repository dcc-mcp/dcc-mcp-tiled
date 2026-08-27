from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "upload_release_assets.py"


def _module():
    spec = importlib.util.spec_from_file_location("upload_release_assets", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "release-bundle"
    dist = bundle / "dist"
    dist.mkdir(parents=True)
    wheel = dist / "dcc_mcp_tiled-0.4.0-py3-none-any.whl"
    sdist = dist / "dcc_mcp_tiled-0.4.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    (bundle / "SHA256SUMS").write_text(
        f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  dist/{wheel.name}\n"
        f"{hashlib.sha256(sdist.read_bytes()).hexdigest()}  dist/{sdist.name}\n",
        encoding="utf-8",
    )
    return bundle


def _snapshot(module, assets=()):
    return module.ReleaseSnapshot(
        release_id=42,
        node_id="RE_kwDOFrozen",
        tag_name="v0.4.0",
        target_commitish="a" * 40,
        draft=False,
        prerelease=False,
        immutable=False,
        assets=tuple(assets),
        assets_sha256=module.asset_baseline_sha256(tuple(assets)),
    )


def test_preflight_rejects_same_name_asset_even_when_digest_matches(tmp_path: Path) -> None:
    module = _module()
    planned = module.plan_release_assets(_bundle(tmp_path), "0.4.0")
    collision = module.AssetIdentity(
        asset_id=101,
        node_id="RA_kwDOCollision",
        name=planned[0].name,
        size=planned[0].size,
        digest=planned[0].digest,
        state="uploaded",
    )
    uploads: list[str] = []

    with pytest.raises(module.ReleaseAssetError, match="already exists"):
        module.apply_release_assets(
            frozen=_snapshot(module, (collision,)),
            planned=planned,
            observe=lambda: _snapshot(module, (collision,)),
            upload=lambda asset: uploads.append(asset.name),
            delete=lambda asset_id: None,
        )

    assert uploads == []


def test_uploads_are_deterministic_and_roll_back_after_later_failure(tmp_path: Path) -> None:
    module = _module()
    frozen = _snapshot(module)
    planned = module.plan_release_assets(_bundle(tmp_path), "0.4.0")
    calls: list[tuple[str, object]] = []
    uploaded = module.AssetIdentity(
        asset_id=101,
        node_id="RA_kwDOFirst",
        name=planned[0].name,
        size=planned[0].size,
        digest=planned[0].digest,
        state="uploaded",
    )
    observations = iter((frozen, frozen))

    def upload(asset):
        calls.append(("upload", asset.name))
        if asset.name == planned[1].name:
            raise module.ReleaseAssetError("synthetic upload failure")
        return uploaded

    def delete(asset_id: int) -> None:
        calls.append(("delete", asset_id))

    with pytest.raises(module.ReleaseAssetError, match="upload failed; rollback restored baseline"):
        module.apply_release_assets(
            frozen=frozen,
            planned=planned,
            observe=lambda: next(observations),
            upload=upload,
            delete=delete,
        )

    assert calls == [
        ("upload", planned[0].name),
        ("upload", planned[1].name),
        ("delete", 101),
    ]


def test_upload_failure_never_claims_unchanged_when_rollback_is_uncertain(tmp_path: Path) -> None:
    module = _module()
    frozen = _snapshot(module)
    planned = module.plan_release_assets(_bundle(tmp_path), "0.4.0")

    def upload(asset):
        if asset.name == planned[1].name:
            raise module.ReleaseAssetError("synthetic upload failure")
        return module.AssetIdentity(
            asset_id=101,
            node_id="RA_kwDOFirst",
            name=asset.name,
            size=asset.size,
            digest=asset.digest,
            state="uploaded",
        )

    def fail_delete(asset_id: int) -> None:
        raise OSError(f"delete failed for {asset_id}")

    with pytest.raises(module.ReleaseAssetError, match="release state is indeterminate"):
        module.apply_release_assets(
            frozen=frozen,
            planned=planned,
            observe=lambda: frozen,
            upload=upload,
            delete=fail_delete,
        )


def test_release_recreation_fails_before_first_upload(tmp_path: Path) -> None:
    module = _module()
    frozen = _snapshot(module)
    recreated = module.ReleaseSnapshot(
        release_id=42,
        node_id="RE_kwDOReplacement",
        tag_name=frozen.tag_name,
        target_commitish=frozen.target_commitish,
        draft=False,
        prerelease=False,
        immutable=False,
        assets=(),
        assets_sha256=frozen.assets_sha256,
    )
    uploads: list[str] = []

    with pytest.raises(module.ReleaseAssetError, match="release node id mismatch"):
        module.apply_release_assets(
            frozen=frozen,
            planned=module.plan_release_assets(_bundle(tmp_path), "0.4.0"),
            observe=lambda: recreated,
            upload=lambda asset: uploads.append(asset.name),
            delete=lambda asset_id: None,
        )

    assert uploads == []


def test_success_requires_the_exact_final_asset_set(tmp_path: Path) -> None:
    module = _module()
    frozen = _snapshot(module)
    planned = module.plan_release_assets(_bundle(tmp_path), "0.4.0")
    uploaded = tuple(
        module.AssetIdentity(
            asset_id=101 + index,
            node_id=f"RA_kwDO{index}",
            name=asset.name,
            size=asset.size,
            digest=asset.digest,
            state="uploaded",
        )
        for index, asset in enumerate(planned)
    )
    final = _snapshot(module, uploaded)
    observations = iter((frozen, final))
    calls: list[str] = []

    result = module.apply_release_assets(
        frozen=frozen,
        planned=tuple(reversed(planned)),
        observe=lambda: next(observations),
        upload=lambda asset: (calls.append(asset.name), uploaded[planned.index(asset)])[1],
        delete=lambda asset_id: pytest.fail(f"unexpected rollback of {asset_id}"),
    )

    assert result == final
    assert calls == [asset.name for asset in planned]
