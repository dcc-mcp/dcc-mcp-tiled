"""Version-source contracts shared by pull requests and release consumers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.verify_version_consistency import (
    VersionConsistencyError,
    read_version_sources,
    verify_version_consistency,
)

ROOT = Path(__file__).resolve().parents[1]


def _copy_version_contract(tmp_path: Path) -> Path:
    for relative in (
        ".release-please-manifest.json",
        "pyproject.toml",
        "release-please-config.json",
        "src/dcc_mcp_tiled/__version__.py",
        "src/dcc_mcp_tiled/skills/tiled-maps/SKILL.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_current_version_sources_and_release_please_ownership_are_consistent() -> None:
    assert verify_version_consistency(ROOT, expected="0.4.0") == "0.4.0"


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        (".release-please-manifest.json", '"0.4.0"', '"0.4.1"'),
        ("pyproject.toml", 'version = "0.4.0"', 'version = "0.4.1"'),
        (
            "src/dcc_mcp_tiled/__version__.py",
            '__version__ = "0.4.0"',
            '__version__ = "0.4.1"',
        ),
        (
            "src/dcc_mcp_tiled/skills/tiled-maps/SKILL.md",
            'version: "0.4.0"',
            'version: "0.4.1"',
        ),
    ],
)
def test_each_version_source_drift_fails_closed(
    tmp_path: Path, relative: str, old: str, new: str
) -> None:
    root = _copy_version_contract(tmp_path)
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")

    with pytest.raises(VersionConsistencyError, match="version sources are inconsistent"):
        verify_version_consistency(root)


def test_release_please_ownership_drift_fails_closed(tmp_path: Path) -> None:
    root = _copy_version_contract(tmp_path)
    config_path = root / "release-please-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["packages"]["."]["extra-files"].pop()
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(VersionConsistencyError, match="ownership"):
        verify_version_consistency(root)


@pytest.mark.parametrize("source", ["toml", "python", "yaml"])
def test_dead_text_cannot_impersonate_a_semantic_version_source(
    tmp_path: Path, source: str
) -> None:
    root = _copy_version_contract(tmp_path)
    if source == "toml":
        path = root / "pyproject.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'version = "0.4.0"',
                'version = """0.4.1\nversion = "0.4.0"\n"""',
                1,
            ),
            encoding="utf-8",
        )
    elif source == "python":
        path = root / "src/dcc_mcp_tiled/__version__.py"
        path.write_text(
            '__version__ = """0.4.1\n__version__ = "0.4.0"  # x-release-please-version\n"""\n',
            encoding="utf-8",
        )
    else:
        path = root / "src/dcc_mcp_tiled/skills/tiled-maps/SKILL.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            '    version: "0.4.0"  # x-release-please-version',
            '    version: "0.4.1"',
            1,
        )
        text = text.replace(
            "name: tiled-maps\n",
            'name: tiled-maps\ndecoy: |\n    version: "0.4.0"  # x-release-please-version\n',
            1,
        )
        path.write_text(text, encoding="utf-8")

    with pytest.raises(VersionConsistencyError):
        verify_version_consistency(root)


@pytest.mark.parametrize(
    "mutation",
    ["extra-package", "extra-file", "duplicate-file", "wrong-type", "wrong-jsonpath"],
)
def test_release_please_ownership_is_one_exact_manifest_package(
    tmp_path: Path, mutation: str
) -> None:
    root = _copy_version_contract(tmp_path)
    config_path = root / "release-please-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    package = config["packages"]["."]
    if mutation == "extra-package":
        config["packages"]["extra"] = dict(package)
    elif mutation == "extra-file":
        package["extra-files"].append({"type": "generic", "path": "unknown.txt"})
    elif mutation == "duplicate-file":
        package["extra-files"].append(dict(package["extra-files"][-1]))
    elif mutation == "wrong-type":
        package["extra-files"][-1]["type"] = "json"
    else:
        package["extra-files"][0]["jsonpath"] = "$.tool.version"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(VersionConsistencyError, match="ownership"):
        verify_version_consistency(root)


def test_next_patch_version_is_parsed_from_all_generated_locations(tmp_path: Path) -> None:
    root = _copy_version_contract(tmp_path)
    for relative, current in read_version_sources(root).items():
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(current, "0.4.1", 1),
            encoding="utf-8",
        )

    assert verify_version_consistency(root, expected="0.4.1") == "0.4.1"


@pytest.mark.parametrize(
    "value",
    ["v0.4.0", "00.4.0", "0.04.0", "0.4.00", "0.4.0rc1", "0.4.0garbage"],
)
def test_skill_version_parser_rejects_noncanonical_values(tmp_path: Path, value: str) -> None:
    root = _copy_version_contract(tmp_path)
    skill = root / "src/dcc_mcp_tiled/skills/tiled-maps/SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8").replace('version: "0.4.0"', f'version: "{value}"'),
        encoding="utf-8",
    )

    with pytest.raises(VersionConsistencyError, match="bundled Skill version is invalid"):
        read_version_sources(root)
