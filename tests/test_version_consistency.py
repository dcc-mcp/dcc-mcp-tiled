"""Version-source contracts shared by pull requests and release consumers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.verify_version_consistency import (
    MANIFEST,
    PROJECT,
    SKILL,
    SOURCE,
    VersionConsistencyError,
    read_version_sources,
    verify_version_consistency,
)

ROOT = Path(__file__).resolve().parents[1]
SIMULATED_RELEASE_VERSIONS = ("0.4.0", "0.4.1")
VERSION_SOURCES = (MANIFEST, PROJECT, SOURCE, SKILL)


def _replace_exact(path: Path, old: str, new: str) -> None:
    assert old != new
    before = path.read_text(encoding="utf-8")
    assert before.count(old) == 1, (path, old)
    after = before.replace(old, new, 1)
    assert after != before
    path.write_text(after, encoding="utf-8")


def _next_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _version_token(relative: str, version: str) -> str:
    if relative == PROJECT:
        return f'version = "{version}"'
    if relative == SOURCE:
        return f'__version__ = "{version}"  # x-release-please-version'
    if relative == SKILL:
        return f'    version: "{version}"  # x-release-please-version'
    raise AssertionError(relative)


def _set_one_version_source(root: Path, relative: str, version: str) -> None:
    current = read_version_sources(root)[relative]
    assert current != version, (relative, current)
    path = root / relative
    if relative == MANIFEST:
        before = path.read_text(encoding="utf-8")
        payload = json.loads(before)
        assert payload == {".": current}
        payload["."] = version
        after = json.dumps(payload, separators=(",", ":")) + "\n"
        assert after != before
        path.write_text(after, encoding="utf-8")
        return
    _replace_exact(path, _version_token(relative, current), _version_token(relative, version))


def _set_all_version_sources(root: Path, version: str) -> None:
    for relative, current in read_version_sources(root).items():
        if current != version:
            _set_one_version_source(root, relative, version)
    assert set(read_version_sources(root).values()) == {version}


def _copy_version_contract(tmp_path: Path, version: str | None = None) -> Path:
    for relative in (
        MANIFEST,
        PROJECT,
        "release-please-config.json",
        SOURCE,
        SKILL,
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    if version is not None:
        _set_all_version_sources(tmp_path, version)
    return tmp_path


def _write_changed_json(path: Path, payload: object, before_payload: object) -> None:
    assert payload != before_payload
    before = path.read_text(encoding="utf-8")
    after = json.dumps(payload, separators=(",", ":")) + "\n"
    assert after != before
    path.write_text(after, encoding="utf-8")


def test_current_version_is_read_semantically_without_a_fixed_release_number() -> None:
    sources = read_version_sources(ROOT)
    assert len(set(sources.values())) == 1
    current = sources[MANIFEST]
    assert verify_version_consistency(ROOT) == current


@pytest.mark.parametrize("fixture_version", SIMULATED_RELEASE_VERSIONS)
@pytest.mark.parametrize("relative", VERSION_SOURCES)
def test_each_version_source_drift_fails_closed(
    tmp_path: Path, fixture_version: str, relative: str
) -> None:
    root = _copy_version_contract(tmp_path, fixture_version)
    path = root / relative
    before = path.read_text(encoding="utf-8")
    _set_one_version_source(root, relative, _next_patch(fixture_version))
    assert path.read_text(encoding="utf-8") != before

    with pytest.raises(VersionConsistencyError, match="version sources are inconsistent"):
        verify_version_consistency(root)


@pytest.mark.parametrize("fixture_version", SIMULATED_RELEASE_VERSIONS)
@pytest.mark.parametrize("source", ["toml", "python", "yaml"])
def test_dead_text_cannot_impersonate_a_semantic_version_source(
    tmp_path: Path, fixture_version: str, source: str
) -> None:
    root = _copy_version_contract(tmp_path, fixture_version)
    next_version = _next_patch(fixture_version)
    if source == "toml":
        path = root / PROJECT
        _replace_exact(
            path,
            _version_token(PROJECT, fixture_version),
            f'version = """{next_version}\nversion = "{fixture_version}"\n"""',
        )
    elif source == "python":
        path = root / SOURCE
        _replace_exact(
            path,
            _version_token(SOURCE, fixture_version),
            (
                f'__version__ = """{next_version}\n'
                f'__version__ = "{fixture_version}"  # x-release-please-version\n"""'
            ),
        )
    else:
        path = root / SKILL
        _replace_exact(
            path,
            _version_token(SKILL, fixture_version),
            f'    version: "{next_version}"',
        )
        _replace_exact(
            path,
            "name: tiled-maps\n",
            (
                "name: tiled-maps\n"
                "decoy: |\n"
                f'    version: "{fixture_version}"  # x-release-please-version\n'
            ),
        )

    with pytest.raises(VersionConsistencyError):
        verify_version_consistency(root)


@pytest.mark.parametrize("fixture_version", SIMULATED_RELEASE_VERSIONS)
@pytest.mark.parametrize(
    "mutation",
    [
        "missing-file",
        "extra-package",
        "extra-file",
        "duplicate-file",
        "wrong-type",
        "wrong-jsonpath",
    ],
)
def test_release_please_ownership_is_one_exact_manifest_package(
    tmp_path: Path, fixture_version: str, mutation: str
) -> None:
    root = _copy_version_contract(tmp_path, fixture_version)
    config_path = root / "release-please-config.json"
    original = json.loads(config_path.read_text(encoding="utf-8"))
    config = json.loads(json.dumps(original))
    package = config["packages"]["."]
    if mutation == "missing-file":
        package["extra-files"].pop()
    elif mutation == "extra-package":
        config["packages"]["extra"] = dict(package)
    elif mutation == "extra-file":
        package["extra-files"].append({"type": "generic", "path": "unknown.txt"})
    elif mutation == "duplicate-file":
        package["extra-files"].append(dict(package["extra-files"][-1]))
    elif mutation == "wrong-type":
        package["extra-files"][-1]["type"] = "json"
    else:
        package["extra-files"][0]["jsonpath"] = "$.tool.version"
    _write_changed_json(config_path, config, original)

    with pytest.raises(VersionConsistencyError, match="ownership"):
        verify_version_consistency(root)


@pytest.mark.parametrize("fixture_version", SIMULATED_RELEASE_VERSIONS)
def test_next_patch_version_is_parsed_from_all_generated_locations(
    tmp_path: Path, fixture_version: str
) -> None:
    root = _copy_version_contract(tmp_path, fixture_version)
    next_version = _next_patch(fixture_version)
    _set_all_version_sources(root, next_version)

    assert verify_version_consistency(root, expected=next_version) == next_version


@pytest.mark.parametrize("fixture_version", SIMULATED_RELEASE_VERSIONS)
@pytest.mark.parametrize(
    "case",
    ["prefixed", "major-leading-zero", "minor-leading-zero", "patch-leading-zero", "rc", "suffix"],
)
def test_skill_version_parser_rejects_noncanonical_values(
    tmp_path: Path, fixture_version: str, case: str
) -> None:
    root = _copy_version_contract(tmp_path, fixture_version)
    major, minor, patch = fixture_version.split(".")
    values = {
        "prefixed": f"v{fixture_version}",
        "major-leading-zero": f"0{major}.{minor}.{patch}",
        "minor-leading-zero": f"{major}.0{minor}.{patch}",
        "patch-leading-zero": f"{major}.{minor}.0{patch}",
        "rc": f"{fixture_version}rc1",
        "suffix": f"{fixture_version}garbage",
    }
    invalid = values[case]
    assert invalid != fixture_version
    skill = root / SKILL
    _replace_exact(
        skill,
        _version_token(SKILL, fixture_version),
        _version_token(SKILL, invalid),
    )

    with pytest.raises(VersionConsistencyError, match="bundled Skill version is invalid"):
        read_version_sources(root)
