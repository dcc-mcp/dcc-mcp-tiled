"""Tests for the executable pre-mutation release identity verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import random
import sys
import tarfile
import zipfile
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


def _package_bundle(
    tmp_path: Path,
    *,
    project: str = "dcc-mcp-tiled",
    metadata_version: str = "0.4.0",
    metadata_headers: bytes = b"",
    wheel_extra: tuple[tuple[str, bytes], ...] = (),
    sdist_extra: tuple[tuple[str, bytes, bytes], ...] = (),
) -> tuple[Path, str]:
    bundle = tmp_path / "bundle"
    dist = bundle / "dist"
    dist.mkdir(parents=True)
    wheel = dist / "dcc_mcp_tiled-0.4.0-py3-none-any.whl"
    sdist = dist / "dcc_mcp_tiled-0.4.0.tar.gz"
    metadata = (
        (f"Metadata-Version: 2.4\nName: {project}\nVersion: {metadata_version}\n").encode()
        + metadata_headers
        + b"\n"
    )
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("dcc_mcp_tiled/__init__.py", "")
        archive.writestr("dcc_mcp_tiled-0.4.0.dist-info/METADATA", metadata)
        for name, payload in wheel_extra:
            archive.writestr(name, payload)
    with tarfile.open(sdist, "w:gz") as archive:
        for name, payload in (
            ("dcc_mcp_tiled-0.4.0/PKG-INFO", metadata),
            ("dcc_mcp_tiled-0.4.0/pyproject.toml", b"[project]\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        for name, payload, member_type in sdist_extra:
            info = tarfile.TarInfo(name)
            info.type = member_type
            info.size = len(payload) if member_type == tarfile.REGTYPE else 0
            archive.addfile(info, io.BytesIO(payload) if info.size else None)
    artifacts = (wheel, sdist)
    manifest = bundle / "SHA256SUMS"
    manifest.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  dist/{path.name}\n"
            for path in artifacts
        ),
        encoding="utf-8",
    )
    return bundle, hashlib.sha256(manifest.read_bytes()).hexdigest()


def _package_bundle_with_members(
    tmp_path: Path,
    *,
    archive_kind: str,
    members: tuple[tuple[str, bytes, bytes], ...],
) -> tuple[Path, str]:
    if archive_kind == "wheel":
        return _package_bundle(
            tmp_path,
            wheel_extra=tuple(
                (f"dcc_mcp_tiled/{name}", payload) for name, payload, _member_type in members
            ),
        )
    assert archive_kind == "sdist"
    return _package_bundle(
        tmp_path,
        sdist_extra=tuple(
            (f"dcc_mcp_tiled-0.4.0/{name}", payload, member_type)
            for name, payload, member_type in members
        ),
    )


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


def test_bundle_verifier_rejects_digest_consistent_invalid_archives(tmp_path: Path) -> None:
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

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_bundle_verifier_rejects_wrong_internal_project(tmp_path: Path) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle(tmp_path, project="unrelated-project")

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_bundle_verifier_accepts_canonical_package_archives(tmp_path: Path) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle(tmp_path)

    module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_bundle_verifier_rejects_wrong_internal_version(tmp_path: Path) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle(tmp_path, metadata_version="9.9.9")

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_bundle_verifier_rejects_ambiguous_package_metadata(tmp_path: Path) -> None:
    module = _module()
    metadata = b"Metadata-Version: 2.4\nName: dcc-mcp-tiled\nVersion: 0.4.0\n"
    bundle, manifest_sha = _package_bundle(
        tmp_path,
        wheel_extra=(("decoy-0.4.0.dist-info/METADATA", metadata),),
        sdist_extra=(("dcc_mcp_tiled-0.4.0/nested/PKG-INFO", metadata, tarfile.REGTYPE),),
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_bundle_verifier_rejects_invalid_duplicate_metadata_headers(tmp_path: Path) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle(
        tmp_path,
        metadata_headers=b"Name: dcc-mcp-tiled\nVersion: 0.4.0\n",
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_bundle_verifier_rejects_duplicate_archive_members(
    tmp_path: Path, archive_kind: str
) -> None:
    module = _module()
    if archive_kind == "wheel":
        with pytest.warns(UserWarning, match="Duplicate name"):
            bundle, manifest_sha = _package_bundle(
                tmp_path,
                wheel_extra=(("dcc_mcp_tiled/__init__.py", b"duplicate"),),
            )
    else:
        bundle, manifest_sha = _package_bundle(
            tmp_path,
            sdist_extra=(("dcc_mcp_tiled-0.4.0/pyproject.toml", b"duplicate", tarfile.REGTYPE),),
        )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Alias.py", "alias.py"),
        ("caf\u00e9.py", "cafe\u0301.py"),
        ("Alias.py", "\uff21lias.py"),
    ],
    ids=["casefold", "unicode-composition", "unicode-width"],
)
def test_bundle_verifier_rejects_portable_member_aliases(
    tmp_path: Path, archive_kind: str, first: str, second: str
) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle_with_members(
        tmp_path,
        archive_kind=archive_kind,
        members=tuple(
            (f"data/{name}", name.encode("utf-8"), tarfile.REGTYPE) for name in (first, second)
        ),
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "name",
    [
        "trailing.",
        "trailing ",
        "CON",
        "nul.txt",
        "LPT9.log",
        "COM\u00b9.txt",
        "payload.txt:stream",
        "payload.txt\uff1astream",
        "nested\uff0fname.py",
        "CON/child.py",
        "trailing./child.py",
        "payload:stream/child.py",
    ],
    ids=[
        "trailing-dot",
        "trailing-space",
        "con-device",
        "nul-device-extension",
        "lpt-device-extension",
        "compatibility-device-digit",
        "ads",
        "normalized-ads",
        "normalized-separator",
        "reserved-parent",
        "trailing-dot-parent",
        "ads-parent",
    ],
)
def test_bundle_verifier_rejects_nonportable_member_segments(
    tmp_path: Path, archive_kind: str, name: str
) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle_with_members(
        tmp_path,
        archive_kind=archive_kind,
        members=((f"data/{name}", b"payload", tarfile.REGTYPE),),
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


PORTABLE_TOPOLOGY_CASES = {
    "file-before-child": (
        ("data/node", tarfile.REGTYPE),
        ("data/node/child.py", tarfile.REGTYPE),
    ),
    "child-before-file": (
        ("data/node/child.py", tarfile.REGTYPE),
        ("data/node", tarfile.REGTYPE),
    ),
    "portable-file-before-child": (
        ("data/Alias", tarfile.REGTYPE),
        ("data/alias/child.py", tarfile.REGTYPE),
    ),
    "portable-child-before-file": (
        ("data/Alias/child.py", tarfile.REGTYPE),
        ("data/alias", tarfile.REGTYPE),
    ),
    "file-before-explicit-dir": (
        ("data/node", tarfile.REGTYPE),
        ("data/node/", tarfile.DIRTYPE),
    ),
    "explicit-dir-before-file": (
        ("data/node/", tarfile.DIRTYPE),
        ("data/node", tarfile.REGTYPE),
    ),
    "implicit-dir-alias": (
        ("data/Alias/first.py", tarfile.REGTYPE),
        ("data/alias/second.py", tarfile.REGTYPE),
    ),
    "explicit-implicit-dir-alias": (
        ("data/Alias/", tarfile.DIRTYPE),
        ("data/alias/child.py", tarfile.REGTYPE),
    ),
    "implicit-explicit-dir-alias": (
        ("data/Alias/child.py", tarfile.REGTYPE),
        ("data/alias/", tarfile.DIRTYPE),
    ),
    "duplicate-normalized-dirs": (
        ("data/Alias/", tarfile.DIRTYPE),
        ("data/alias/", tarfile.DIRTYPE),
    ),
}


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "members",
    PORTABLE_TOPOLOGY_CASES.values(),
    ids=PORTABLE_TOPOLOGY_CASES,
)
def test_bundle_verifier_rejects_portable_topology_collisions(
    tmp_path: Path,
    archive_kind: str,
    members: tuple[tuple[str, bytes], ...],
) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle_with_members(
        tmp_path,
        archive_kind=archive_kind,
        members=tuple(
            (
                name,
                b"" if member_type == tarfile.DIRTYPE else b"payload",
                member_type,
            )
            for name, member_type in members
        ),
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_bundle_verifier_accepts_explicit_directory_with_child(
    tmp_path: Path, archive_kind: str
) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle_with_members(
        tmp_path,
        archive_kind=archive_kind,
        members=(
            ("data/node/", b"", tarfile.DIRTYPE),
            ("data/node/child.py", b"payload", tarfile.REGTYPE),
        ),
    )

    module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize(
    ("wheel_extra", "sdist_extra"),
    [
        ((("../escape.py", b"unsafe"),), ()),
        ((), (("other-root/escape.py", b"unsafe", tarfile.REGTYPE),)),
        (
            (),
            (("dcc_mcp_tiled-0.4.0/link", b"", tarfile.SYMTYPE),),
        ),
    ],
)
def test_bundle_verifier_rejects_unsafe_archive_members(
    tmp_path: Path,
    wheel_extra,
    sdist_extra,
) -> None:
    module = _module()
    bundle, manifest_sha = _package_bundle(
        tmp_path,
        wheel_extra=wheel_extra,
        sdist_extra=sdist_extra,
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


def test_bundle_verifier_rejects_oversized_package_metadata(tmp_path: Path) -> None:
    module = _module()
    padding = b"X-Padding: " + (b"x" * 64) + b"\n"
    bundle, manifest_sha = _package_bundle(tmp_path, metadata_headers=padding * 1024)

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_bundle_verifier_rejects_excessive_archive_members(
    tmp_path: Path, archive_kind: str
) -> None:
    module = _module()
    if archive_kind == "wheel":
        wheel_extra = tuple((f"dcc_mcp_tiled/data/{index}.txt", b"") for index in range(255))
        sdist_extra = ()
    else:
        wheel_extra = ()
        sdist_extra = tuple(
            (f"dcc_mcp_tiled-0.4.0/data/{index}.txt", b"", tarfile.REGTYPE) for index in range(255)
        )
    bundle, manifest_sha = _package_bundle(
        tmp_path,
        wheel_extra=wheel_extra,
        sdist_extra=sdist_extra,
    )

    with pytest.raises(module.IdentityError, match="package archive"):
        module.verify_bundle(bundle, "0.4.0", manifest_sha)


@pytest.mark.parametrize(
    "case",
    ["wheel-member", "sdist-member", "wheel-ratio", "sdist-ratio", "wheel-total"],
)
def test_bundle_verifier_enforces_archive_resource_bounds(tmp_path: Path, case: str) -> None:
    module = _module()
    wheel_extra = ()
    sdist_extra = ()
    if case.endswith("member"):
        payload = b"x" * ((4 * 1024 * 1024) + 1)
    elif case.endswith("ratio"):
        payload = b"x" * (1024 * 1024)
    else:
        generator = random.Random(0)
        wheel_extra = tuple(
            (f"dcc_mcp_tiled/data/{index}.bin", generator.randbytes(3_500_000))
            for index in range(5)
        )
        payload = b""
    if case.startswith("wheel") and not wheel_extra:
        wheel_extra = (("dcc_mcp_tiled/data/payload.bin", payload),)
    if case.startswith("sdist"):
        sdist_extra = (("dcc_mcp_tiled-0.4.0/data/payload.bin", payload, tarfile.REGTYPE),)
    bundle, manifest_sha = _package_bundle(
        tmp_path,
        wheel_extra=wheel_extra,
        sdist_extra=sdist_extra,
    )

    with pytest.raises(module.IdentityError, match="package archive"):
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
