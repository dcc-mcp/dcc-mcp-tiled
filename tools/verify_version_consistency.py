"""Verify every canonical release version source and its Release Please ownership."""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10 test lanes install tomli.
    import tomli as tomllib  # type: ignore[no-redef]

SEMVER = r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})"
SEMVER_RE = re.compile(rf"^{SEMVER}$")

MANIFEST = ".release-please-manifest.json"
PROJECT = "pyproject.toml"
SOURCE = "src/dcc_mcp_tiled/__version__.py"
SKILL = "src/dcc_mcp_tiled/skills/tiled-maps/SKILL.md"
CONFIG = "release-please-config.json"

RELEASE_PLEASE_OWNERS = {
    PROJECT: {"type": "toml", "path": PROJECT, "jsonpath": "$.project.version"},
    SOURCE: {"type": "generic", "path": SOURCE},
    SKILL: {"type": "generic", "path": SKILL},
}

EXPECTED_RELEASE_PLEASE_CONFIG = {
    "packages": {
        ".": {
            "release-type": "python",
            "package-name": "dcc-mcp-tiled",
            "changelog-path": "CHANGELOG.md",
            "include-component-in-tag": False,
            "extra-files": list(RELEASE_PLEASE_OWNERS.values()),
        }
    }
}


class VersionConsistencyError(RuntimeError):
    """A canonical version source or its generator ownership is invalid."""


def _read_text(root: Path, relative: str) -> str:
    path = root / relative
    try:
        if not path.is_file():
            raise VersionConsistencyError(f"required version source is missing: {relative}")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise VersionConsistencyError(f"required version source is unreadable: {relative}") from exc


def _canonical_version(value: Any, label: str) -> str:
    if not isinstance(value, str) or SEMVER_RE.fullmatch(value) is None:
        raise VersionConsistencyError(f"{label} is invalid")
    return value


def _manifest_version(root: Path) -> str:
    try:
        payload = json.loads(_read_text(root, MANIFEST))
    except json.JSONDecodeError as exc:
        raise VersionConsistencyError("Release Please manifest is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"."}:
        raise VersionConsistencyError("Release Please manifest is invalid")
    return _canonical_version(payload["."], "Release Please manifest version")


def _project_version(root: Path) -> str:
    try:
        payload = tomllib.loads(_read_text(root, PROJECT))
        project = payload["project"]
        version = project["version"]
    except (KeyError, TypeError, ValueError) as exc:
        raise VersionConsistencyError("project version is invalid") from exc
    if not isinstance(project, dict):
        raise VersionConsistencyError("project version is invalid")
    return _canonical_version(version, "project version")


def _source_version(root: Path) -> str:
    text = _read_text(root, SOURCE)
    try:
        tree = ast.parse(text, filename=SOURCE)
        comments = [
            token
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT
            and token.string.strip() == "# x-release-please-version"
        ]
    except (SyntaxError, tokenize.TokenError) as exc:
        raise VersionConsistencyError("source version is invalid") from exc
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
    ]
    stored_names = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "__version__"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    if len(assignments) != 1 or len(stored_names) != 1 or len(comments) != 1:
        raise VersionConsistencyError("source version is invalid")
    assignment = assignments[0]
    if comments[0].start[0] != assignment.lineno:
        raise VersionConsistencyError("source version is invalid")
    value = assignment.value.value if isinstance(assignment.value, ast.Constant) else None
    return _canonical_version(value, "source version")


def _mapping_value(node: yaml.Node, key: str, label: str) -> yaml.Node:
    if not isinstance(node, yaml.MappingNode):
        raise VersionConsistencyError(f"{label} is invalid")
    matches = [
        value
        for candidate, value in node.value
        if isinstance(candidate, yaml.ScalarNode) and candidate.value == key
    ]
    if len(matches) != 1:
        raise VersionConsistencyError(f"{label} is invalid")
    return matches[0]


def _skill_version(root: Path) -> str:
    text = _read_text(root, SKILL)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise VersionConsistencyError("bundled Skill version is invalid")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise VersionConsistencyError("bundled Skill version is invalid") from exc
    frontmatter_lines = lines[1:closing]
    try:
        document = yaml.compose("\n".join(frontmatter_lines), Loader=yaml.SafeLoader)
        metadata = _mapping_value(document, "metadata", "bundled Skill metadata")
        dcc_metadata = _mapping_value(metadata, "dcc-mcp", "bundled Skill metadata")
        version_node = _mapping_value(dcc_metadata, "version", "bundled Skill version")
    except yaml.YAMLError as exc:
        raise VersionConsistencyError("bundled Skill version is invalid") from exc
    if not isinstance(version_node, yaml.ScalarNode) or version_node.tag != "tag:yaml.org,2002:str":
        raise VersionConsistencyError("bundled Skill version is invalid")
    version = _canonical_version(version_node.value, "bundled Skill version")
    marker = "x-release-please-version"
    if text.count(marker) != 1 or version_node.start_mark.line >= len(frontmatter_lines):
        raise VersionConsistencyError("bundled Skill version is invalid")
    expected_line = f'    version: "{version}"  # {marker}'
    if frontmatter_lines[version_node.start_mark.line] != expected_line:
        raise VersionConsistencyError("bundled Skill version is invalid")
    return version


def read_version_sources(root: Path) -> dict[str, str]:
    """Return each independently parsed canonical release version source."""

    return {
        MANIFEST: _manifest_version(root),
        PROJECT: _project_version(root),
        SOURCE: _source_version(root),
        SKILL: _skill_version(root),
    }


def _verify_release_please_ownership(root: Path) -> None:
    try:
        payload = json.loads(_read_text(root, CONFIG))
    except json.JSONDecodeError as exc:
        raise VersionConsistencyError("Release Please version ownership is invalid") from exc
    if payload != EXPECTED_RELEASE_PLEASE_CONFIG:
        raise VersionConsistencyError("Release Please version ownership is not exact")


def verify_version_consistency(root: Path, expected: str | None = None) -> str:
    """Fail closed unless all sources and generator ownership name one version."""

    root = root.resolve()
    _verify_release_please_ownership(root)
    versions = read_version_sources(root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        raise VersionConsistencyError("release version sources are inconsistent")
    version = distinct.pop()
    if expected is not None and version != _canonical_version(expected, "expected release version"):
        raise VersionConsistencyError("release version does not match the expected target")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--expected")
    args = parser.parse_args(argv)
    version = verify_version_consistency(args.root, args.expected)
    print(f"release version sources verified: {version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VersionConsistencyError as exc:
        print(f"release version verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
