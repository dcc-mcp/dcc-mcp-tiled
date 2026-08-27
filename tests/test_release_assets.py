from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import zipfile
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
    metadata = b"Metadata-Version: 2.4\nName: dcc-mcp-tiled\nVersion: 0.4.0\n"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("dcc_mcp_tiled/__init__.py", "")
        archive.writestr("dcc_mcp_tiled-0.4.0.dist-info/METADATA", metadata)
    with tarfile.open(sdist, "w:gz") as archive:
        for name, payload in (
            ("dcc_mcp_tiled-0.4.0/PKG-INFO", metadata),
            ("dcc_mcp_tiled-0.4.0/pyproject.toml", b"[project]\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
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


def _asset_payload(asset) -> dict[str, object]:
    return {
        "id": asset.asset_id,
        "node_id": asset.node_id,
        "name": asset.name,
        "size": asset.size,
        "digest": asset.digest,
        "state": asset.state,
    }


def _main_simulation(module, tmp_path: Path, monkeypatch, tag_shas: list[str]):
    bundle = _bundle(tmp_path)
    manifest_sha256 = hashlib.sha256((bundle / "SHA256SUMS").read_bytes()).hexdigest()
    source_sha = "a" * 40
    remote_assets = []
    uploads: list[str] = []
    deletes: list[int] = []
    paths: list[str] = []
    remaining_tag_shas = iter(tag_shas)
    release = {
        "id": 42,
        "node_id": "RE_kwDOFrozen",
        "tag_name": "v0.4.0",
        "target_commitish": source_sha,
        "draft": False,
        "prerelease": False,
        "immutable": False,
    }

    def fetch(path: str):
        paths.append(path)
        if path == "git/ref/tags/v0.4.0":
            return {"object": {"type": "commit", "sha": next(remaining_tag_shas)}}
        if path == "releases/42":
            return release
        if path == "releases/42/assets?per_page=100":
            return [_asset_payload(asset) for asset in remote_assets]
        raise AssertionError(path)

    def upload(planned):
        asset = module.AssetIdentity(
            asset_id=101 + len(remote_assets),
            node_id=f"RA_kwDO{len(remote_assets)}",
            name=planned.name,
            size=planned.size,
            digest=planned.digest,
            state="uploaded",
        )
        uploads.append(planned.name)
        remote_assets.append(asset)
        return asset

    def delete(asset_id: int) -> None:
        deletes.append(asset_id)
        remote_assets[:] = [asset for asset in remote_assets if asset.asset_id != asset_id]

    def remote_fetch(repository: str, token: str):
        assert repository == "dcc-mcp/dcc-mcp-tiled"
        assert token == "test-token"
        return fetch

    def github_mutations(repository: str, release_id: int, token: str):
        assert repository == "dcc-mcp/dcc-mcp-tiled"
        assert release_id == 42
        assert token == "test-token"
        return upload, delete

    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(module, "_remote_fetch", remote_fetch)
    monkeypatch.setattr(module, "_github_mutations", github_mutations)
    argv = [
        "--repository",
        "dcc-mcp/dcc-mcp-tiled",
        "--release-id",
        "42",
        "--release-node-id",
        "RE_kwDOFrozen",
        "--tag",
        "v0.4.0",
        "--sha",
        source_sha,
        "--version",
        "0.4.0",
        "--bundle",
        str(bundle),
        "--manifest-sha256",
        manifest_sha256,
        "--release-assets-sha256",
        module.asset_baseline_sha256(()),
    ]
    return argv, uploads, deletes, paths, remote_assets


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
            verify_tag=lambda: None,
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
            verify_tag=lambda: None,
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
            verify_tag=lambda: None,
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
            verify_tag=lambda: None,
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
        verify_tag=lambda: None,
    )

    assert result == final
    assert calls == [asset.name for asset in planned]


def test_main_rechecks_repository_tag_before_first_upload(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    argv, uploads, deletes, paths, remote_assets = _main_simulation(
        module,
        tmp_path,
        monkeypatch,
        tag_shas=["b" * 40],
    )

    with pytest.raises(module.ReleaseAssetError, match="remote release tag changed"):
        module.main(argv)

    assert uploads == []
    assert deletes == []
    assert remote_assets == []
    assert paths.count("git/ref/tags/v0.4.0") == 1


def test_main_stops_and_rolls_back_when_repository_tag_moves_mid_loop(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    argv, uploads, deletes, paths, remote_assets = _main_simulation(
        module,
        tmp_path,
        monkeypatch,
        tag_shas=["a" * 40, "b" * 40],
    )
    planned = module.plan_release_assets(Path(argv[argv.index("--bundle") + 1]), "0.4.0")

    with pytest.raises(module.ReleaseAssetError, match="rollback restored baseline"):
        module.main(argv)

    assert uploads == [planned[0].name]
    assert deletes == [101]
    assert remote_assets == []
    assert paths.count("git/ref/tags/v0.4.0") == 2


def test_main_rechecks_repository_tag_before_final_success(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    argv, uploads, deletes, paths, remote_assets = _main_simulation(
        module,
        tmp_path,
        monkeypatch,
        tag_shas=["a" * 40, "a" * 40, "a" * 40, "b" * 40],
    )
    planned = module.plan_release_assets(Path(argv[argv.index("--bundle") + 1]), "0.4.0")

    with pytest.raises(module.ReleaseAssetError, match="rollback restored baseline"):
        module.main(argv)

    assert uploads == [asset.name for asset in planned]
    assert deletes == [103, 102, 101]
    assert remote_assets == []
    assert paths.count("git/ref/tags/v0.4.0") == 4
