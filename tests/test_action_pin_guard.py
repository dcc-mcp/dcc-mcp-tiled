"""Tests for the live upstream action-pin provenance guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_action_pins.py"


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
