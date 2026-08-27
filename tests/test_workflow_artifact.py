"""Tests for exact GitHub Actions artifact identity recapture."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_workflow_artifact.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_workflow_artifact", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_main_binds_artifact_to_repository_run_and_head(monkeypatch) -> None:
    module = _module()
    repository = "dcc-mcp/dcc-mcp-tiled"
    repository_id = 1311305911
    artifact_id = 9560803213
    run_id = 32841853172
    head_sha = "a" * 40
    artifact_output_digest = "b" * 64
    server_digest = "sha256:" + artifact_output_digest
    paths: list[str] = []
    responses = {
        "": {"id": repository_id, "full_name": repository},
        f"actions/artifacts/{artifact_id}": {
            "id": artifact_id,
            "node_id": "MDg6QXJ0aWZhY3Q5NTYwODAzMjEz",
            "name": "release-bundle",
            "size_in_bytes": 102646,
            "url": f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}",
            "archive_download_url": (
                f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip"
            ),
            "expired": False,
            "digest": server_digest,
            "workflow_run": {
                "id": run_id,
                "repository_id": repository_id,
                "head_repository_id": repository_id,
                "head_sha": head_sha,
            },
        },
    }

    def remote_fetch(observed_repository: str, token: str):
        assert observed_repository == repository
        assert token == "test-token"

        def fetch(path: str):
            paths.append(path)
            return responses[path]

        return fetch

    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(module, "_remote_fetch", remote_fetch)

    assert (
        module.main(
            [
                "--repository",
                repository,
                "--artifact-id",
                str(artifact_id),
                "--artifact-name",
                "release-bundle",
                "--artifact-digest",
                artifact_output_digest,
                "--run-id",
                str(run_id),
                "--head-sha",
                head_sha,
            ]
        )
        == 0
    )
    assert paths == ["", f"actions/artifacts/{artifact_id}"]


def _responses() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    repository = "dcc-mcp/dcc-mcp-tiled"
    repository_id = 1311305911
    artifact_id = 9560803213
    run_id = 32841853172
    head_sha = "a" * 40
    artifact_output_digest = "b" * 64
    repository_payload = {"id": repository_id, "full_name": repository}
    artifact_payload = {
        "id": artifact_id,
        "node_id": "MDg6QXJ0aWZhY3Q5NTYwODAzMjEz",
        "name": "release-bundle",
        "size_in_bytes": 102646,
        "url": f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}",
        "archive_download_url": (
            f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip"
        ),
        "expired": False,
        "digest": "sha256:" + artifact_output_digest,
        "workflow_run": {
            "id": run_id,
            "repository_id": repository_id,
            "head_repository_id": repository_id,
            "head_sha": head_sha,
        },
    }
    expected = {
        "repository": repository,
        "artifact_id": artifact_id,
        "artifact_name": "release-bundle",
        "artifact_digest": artifact_output_digest,
        "run_id": run_id,
        "head_sha": head_sha,
        "token": "test-token",
    }
    return repository_payload, artifact_payload, expected


def _nested_set(payload: dict[str, object], key: str, value: object) -> None:
    nested = payload["workflow_run"]
    assert isinstance(nested, dict)
    nested[key] = value


@pytest.mark.parametrize(
    ("target", "mutate"),
    [
        pytest.param("repository", lambda value: value.__setitem__("id", 0), id="repository-id"),
        pytest.param(
            "repository",
            lambda value: value.__setitem__("full_name", "other/project"),
            id="repository-name",
        ),
        pytest.param("artifact", lambda value: value.__setitem__("id", 9560803214), id="id"),
        pytest.param("artifact", lambda value: value.__setitem__("node_id", ""), id="node-id"),
        pytest.param("artifact", lambda value: value.__setitem__("name", "decoy"), id="name"),
        pytest.param("artifact", lambda value: value.__setitem__("size_in_bytes", 0), id="size"),
        pytest.param(
            "artifact",
            lambda value: value.__setitem__("size_in_bytes", (300 * 1024 * 1024) + 1),
            id="oversized",
        ),
        pytest.param(
            "artifact", lambda value: value.__setitem__("url", "https://example.test"), id="url"
        ),
        pytest.param(
            "artifact",
            lambda value: value.__setitem__("archive_download_url", "https://example.test"),
            id="download-url",
        ),
        pytest.param("artifact", lambda value: value.__setitem__("expired", True), id="expired"),
        pytest.param("artifact", lambda value: value.pop("expired"), id="unknown-expiry"),
        pytest.param(
            "artifact",
            lambda value: value.__setitem__("digest", "sha256:" + ("c" * 64)),
            id="digest",
        ),
        pytest.param(
            "artifact", lambda value: value.__setitem__("workflow_run", None), id="workflow-run"
        ),
        pytest.param("artifact", lambda value: _nested_set(value, "id", 32841853173), id="run-id"),
        pytest.param(
            "artifact",
            lambda value: _nested_set(value, "repository_id", 1311305912),
            id="run-repository-id",
        ),
        pytest.param(
            "artifact",
            lambda value: _nested_set(value, "head_repository_id", 1311305912),
            id="head-repository-id",
        ),
        pytest.param(
            "artifact", lambda value: _nested_set(value, "head_sha", "c" * 40), id="head-sha"
        ),
    ],
)
def test_artifact_recapture_rejects_identity_drift(target, mutate, monkeypatch) -> None:
    module = _module()
    repository_payload, artifact_payload, expected = _responses()
    repository_payload = copy.deepcopy(repository_payload)
    artifact_payload = copy.deepcopy(artifact_payload)
    mutate(repository_payload if target == "repository" else artifact_payload)

    def remote_fetch(repository: str, token: str):
        assert repository == expected["repository"]
        assert token == expected["token"]

        def fetch(path: str):
            return repository_payload if path == "" else artifact_payload

        return fetch

    monkeypatch.setattr(module, "_remote_fetch", remote_fetch)
    with pytest.raises(module.ArtifactIdentityError):
        module.verify_workflow_artifact(**expected)
