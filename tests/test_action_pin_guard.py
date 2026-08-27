"""Tests for the live upstream action-pin provenance guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_action_pins.py"
APPROVED = {
    "actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
    "actions/download-artifact": "634f93cb2916e3fdff6788551b99b062d0335ce0",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "googleapis/release-please-action": "45996ed1f6d02564a971a2fa1b5860e934307cf7",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def _module():
    spec = importlib.util.spec_from_file_location("verify_action_pins", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_binds_each_pin_to_its_named_upstream_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    observed: list[tuple[str, str]] = []

    def verify(repo: str, sha: str) -> None:
        observed.append((repo, sha))

    monkeypatch.setattr(module, "verify_upstream_commit", verify)
    assert module.main([str(ROOT / ".github" / "workflows" / "release.yml")]) == 0
    assert module.APPROVED_ACTION_PINS == APPROVED
    assert set(observed) == set(APPROVED.items())
    assert observed
    assert (
        "googleapis/release-please-action",
        "45996ed1f6d02564a971a2fa1b5860e934307cf7",
    ) in observed


def test_guard_rejects_a_tag_object_or_unknown_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()

    def reject(_repo: str, _sha: str) -> None:
        raise module.PinError("pin is not a commit in the named upstream repository")

    monkeypatch.setattr(module, "verify_upstream_commit", reject)
    with pytest.raises(module.PinError, match="not a commit"):
        module.main([str(ROOT / ".github" / "workflows" / "release.yml")])


@pytest.mark.parametrize(
    ("repo", "sha"),
    [
        ("dcc-mcp/dcc-mcp-tiled", "1f0cca70d357d8f9ccd1d9d59600c7dc9de4a3c0"),
        (
            "googleapis/release-please-action",
            "0dfd8532f9ae3c7b9fd77720f42e6b050e852afd",
        ),
        ("actions/checkout", "0" * 40),
    ],
)
def test_guard_rejects_unreviewed_repo_or_commit_before_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo: str,
    sha: str,
) -> None:
    module = _module()
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        f"jobs:\n  release:\n    steps:\n      - uses: {repo}@{sha}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "verify_upstream_commit",
        lambda *_args: pytest.fail("unreviewed pins must fail before network I/O"),
    )

    with pytest.raises(module.PinError, match="approved action pin"):
        module.main([str(workflow)])
