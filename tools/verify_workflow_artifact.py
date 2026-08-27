"""Recapture and verify one exact GitHub Actions artifact before download."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT_BYTES = 300 * 1024 * 1024


class ArtifactIdentityError(RuntimeError):
    """The workflow artifact identity could not be proven."""


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactIdentityError(f"artifact {key.replace('_', ' ')} is invalid")
    return value


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ArtifactIdentityError(f"artifact {key.replace('_', ' ')} is invalid")
    return value


def _remote_fetch(repository: str, token: str) -> Callable[[str], Any]:
    base_url = f"https://api.github.com/repos/{repository}"

    def fetch(path: str) -> Any:
        request = urllib.request.Request(
            base_url if not path else f"{base_url}/{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "dcc-mcp-tiled-workflow-artifact-guard",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ArtifactIdentityError("workflow artifact identity could not be observed") from exc

    return fetch


def verify_workflow_artifact(
    *,
    repository: str,
    artifact_id: int,
    artifact_name: str,
    artifact_digest: str,
    run_id: int,
    head_sha: str,
    token: str,
) -> None:
    if REPOSITORY_RE.fullmatch(repository) is None or not token:
        raise ArtifactIdentityError("artifact repository identity or token is invalid")
    if artifact_name != "release-bundle":
        raise ArtifactIdentityError("artifact name is invalid")
    if DIGEST_RE.fullmatch(artifact_digest) is None:
        raise ArtifactIdentityError("artifact digest is invalid")
    if SHA_RE.fullmatch(head_sha) is None:
        raise ArtifactIdentityError("artifact source commit is invalid")
    if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id <= 0:
        raise ArtifactIdentityError("artifact id is invalid")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ArtifactIdentityError("artifact run id is invalid")

    fetch = _remote_fetch(repository, token)
    repository_payload = fetch("")
    if not isinstance(repository_payload, dict):
        raise ArtifactIdentityError("artifact repository response is invalid")
    repository_id = _required_int(repository_payload, "id")
    if _required_string(repository_payload, "full_name") != repository:
        raise ArtifactIdentityError("artifact repository changed")

    payload = fetch(f"actions/artifacts/{artifact_id}")
    if not isinstance(payload, dict):
        raise ArtifactIdentityError("workflow artifact response is invalid")
    if _required_int(payload, "id") != artifact_id:
        raise ArtifactIdentityError("artifact id changed")
    _required_string(payload, "node_id")
    if _required_string(payload, "name") != artifact_name:
        raise ArtifactIdentityError("artifact name changed")
    size = _required_int(payload, "size_in_bytes")
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactIdentityError("artifact size exceeds the bounded download")
    expected_url = f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}"
    if _required_string(payload, "url") != expected_url:
        raise ArtifactIdentityError("artifact repository URL changed")
    if _required_string(payload, "archive_download_url") != f"{expected_url}/zip":
        raise ArtifactIdentityError("artifact download URL changed")
    if payload.get("expired") is not False:
        raise ArtifactIdentityError("artifact is expired or expiry is unknown")
    if _required_string(payload, "digest") != f"sha256:{artifact_digest}":
        raise ArtifactIdentityError("artifact server digest changed")

    workflow_run = payload.get("workflow_run")
    if not isinstance(workflow_run, dict):
        raise ArtifactIdentityError("artifact workflow run identity is invalid")
    if _required_int(workflow_run, "id") != run_id:
        raise ArtifactIdentityError("artifact workflow run changed")
    if _required_int(workflow_run, "repository_id") != repository_id:
        raise ArtifactIdentityError("artifact repository id changed")
    if _required_int(workflow_run, "head_repository_id") != repository_id:
        raise ArtifactIdentityError("artifact head repository id changed")
    if _required_string(workflow_run, "head_sha") != head_sha:
        raise ArtifactIdentityError("artifact source commit changed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    verify_workflow_artifact(
        repository=args.repository,
        artifact_id=args.artifact_id,
        artifact_name=args.artifact_name,
        artifact_digest=args.artifact_digest,
        run_id=args.run_id,
        head_sha=args.head_sha,
        token=os.environ.get("GH_TOKEN", ""),
    )
    print("workflow artifact identity verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactIdentityError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"workflow artifact verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
