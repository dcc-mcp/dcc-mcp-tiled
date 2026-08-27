"""Release workflow supply-chain contracts."""

from __future__ import annotations

import copy
import importlib.util
import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
WORKFLOW_GUARD = ROOT / "tools" / "verify_release_workflow.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = ROOT / "tools" / "release-build-requirements.txt"
VERSION_REQUIREMENTS = ROOT / "tools" / "release-version-requirements.txt"

SEMANTIC_INSTALL = (
    "python -m pip install --disable-pip-version-check --only-binary=:all: "
    "--require-hashes -r tools/release-version-requirements.txt"
)

ACTION_PINS = {
    "actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
    "actions/download-artifact": "634f93cb2916e3fdff6788551b99b062d0335ce0",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "googleapis/release-please-action": "45996ed1f6d02564a971a2fa1b5860e934307cf7",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}

RECAPTURE_COMMAND = [
    "python",
    "tools/verify_release_identity.py",
    "--repository",
    "${GITHUB_REPOSITORY}",
    "--tag",
    "${EXPECTED_TAG}",
    "--sha",
    "${EXPECTED_SHA}",
    "--version",
    "${EXPECTED_VERSION}",
    "--bundle",
    "release-bundle",
    "--manifest-sha256",
    "${EXPECTED_MANIFEST_SHA256}",
    "--release-id",
    "${EXPECTED_RELEASE_ID}",
    "--release-node-id",
    "${EXPECTED_RELEASE_NODE_ID}",
    "--release-assets-sha256",
    "${EXPECTED_RELEASE_ASSETS_SHA256}",
]


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _workflow_guard():
    spec = importlib.util.spec_from_file_location("verify_release_workflow", WORKFLOW_GUARD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _step(job: dict, name: str) -> dict:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, (name, matches)
    return matches[0]


def _step_id(job: dict, step_id: str) -> dict:
    matches = [step for step in job["steps"] if step.get("id") == step_id]
    assert len(matches) == 1, (step_id, matches)
    return matches[0]


def _checkout(job: dict) -> dict:
    matches = [
        step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_exact_recapture_command(script: str) -> None:
    assert shlex.split(script.replace("\\\n", " ")) == RECAPTURE_COMMAND


def test_every_release_action_is_pinned_to_an_exact_commit() -> None:
    workflow = _workflow()
    uses = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    ]

    assert uses
    for value in uses:
        match = re.fullmatch(r"([^@]+)@([0-9a-f]{40})", value)
        assert match is not None, value
        assert ACTION_PINS[match.group(1)] == match.group(2), value


def test_pull_requests_run_a_non_mutating_online_release_guard() -> None:
    workflow = yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    guard = workflow["jobs"]["release-guard"]

    assert "pull_request" in workflow["on"]
    assert guard["permissions"] == {"contents": "read"}
    assert "id-token" not in guard["permissions"]
    scripts = "\n".join(step.get("run", "") for step in guard["steps"])
    assert "tests/test_release_workflow.py" in scripts
    assert "tests/test_version_consistency.py" in scripts
    assert "python tools/verify_version_consistency.py" in scripts
    assert SEMANTIC_INSTALL in scripts.replace("\n", " ")
    assert "tools/verify_action_pins.py .github/workflows/release.yml" in scripts
    assert (
        "python -m pip install --disable-pip-version-check --only-binary=:all: "
        "--require-hashes -r tools/release-build-requirements.txt"
    ) in scripts.replace("\n", " ")
    assert "python -m build --no-isolation" in scripts
    assert "python -m twine check dist/*" in scripts
    assert "expected-files.txt" in scripts


def test_release_please_freezes_a_canonical_tag_to_one_commit() -> None:
    workflow = _workflow()
    release = workflow["jobs"]["release-please"]

    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert release["outputs"] == {
        "release_created": "${{ steps.release.outputs.release_created }}",
        "tag_name": "${{ steps.target.outputs.tag_name }}",
        "tag_sha": "${{ steps.target.outputs.tag_sha }}",
        "version": "${{ steps.target.outputs.version }}",
        "release_id": "${{ steps.release-identity.outputs.release_id }}",
        "release_node_id": "${{ steps.release-identity.outputs.release_node_id }}",
        "release_assets_sha256": ("${{ steps.release-identity.outputs.release_assets_sha256 }}"),
    }
    target = _step(release, "Resolve immutable release target")
    assert target["if"] == "steps.release.outputs.release_created == 'true'"
    assert target["env"]["RELEASE_TAG"] == "${{ steps.release.outputs.tag_name }}"
    script = target["run"]
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in script
    assert "git/ref/tags/${RELEASE_TAG}" in script
    assert "git/tags/${object_sha}" in script
    assert "tag_sha=$object_sha" in script
    freeze = _step_id(release, "release-identity")
    assert freeze["if"] == "steps.release.outputs.release_created == 'true'"
    assert "tools/freeze_release_identity.py" in freeze["run"]


def test_every_release_consumer_rechecks_head_tag_sha_version_and_artifacts() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]

    for name in ("build", "publish", "attach-release-assets"):
        job = jobs[name]
        needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
        assert "release-please" in needs
        checkout = _checkout(job)
        assert checkout["with"] == {
            "ref": "${{ needs.release-please.outputs.tag_name }}",
            "fetch-depth": "0",
        }
        verify = _step(job, "Verify immutable release target")
        verify_index = job["steps"].index(verify)
        semantic_install = job["steps"][verify_index - 1]
        assert semantic_install["name"] == "Install the semantic version verifier"
        assert SEMANTIC_INSTALL in semantic_install["run"].replace("\n", " ")
        assert verify["env"] == {
            "EXPECTED_TAG": "${{ needs.release-please.outputs.tag_name }}",
            "EXPECTED_SHA": "${{ needs.release-please.outputs.tag_sha }}",
            "EXPECTED_VERSION": "${{ needs.release-please.outputs.version }}",
        }
        script = verify["run"]
        assert "git rev-parse HEAD" in script
        assert 'git rev-parse "${EXPECTED_TAG}^{commit}"' in script
        assert 'python tools/verify_version_consistency.py --expected "$EXPECTED_VERSION"' in script

    assert VERSION_REQUIREMENTS.read_text(encoding="utf-8") == (
        "pyyaml==6.0.3 \\\n"
        "    --hash=sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc\n"
    )

    for name in ("publish", "attach-release-assets"):
        assert "build" in jobs[name]["needs"]
        artifact_check = _step(jobs[name], "Verify release artifacts")
        assert artifact_check["env"] == {
            "EXPECTED_MANIFEST_SHA256": "${{ needs.build.outputs.bundle_manifest_sha256 }}"
        }
        assert "sha256sum --check --strict" in artifact_check["run"]
        assert "EXPECTED_MANIFEST_SHA256" in artifact_check["run"]

    assert jobs["build"]["outputs"] == {
        "bundle_manifest_sha256": "${{ steps.bundle-identity.outputs.sha256 }}"
    }


def test_mutations_immediately_follow_fresh_remote_identity_recapture() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    cases = {"publish": "publish-pypi", "attach-release-assets": "upload-release-assets"}

    for job_name, mutation_id in cases.items():
        steps = jobs[job_name]["steps"]
        mutation_index = next(
            index for index, step in enumerate(steps) if step.get("id") == mutation_id
        )
        assert mutation_index > 0
        recapture = steps[mutation_index - 1]
        assert recapture["name"] == "Recapture remote release identity immediately before mutation"
        assert recapture["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "EXPECTED_TAG": "${{ needs.release-please.outputs.tag_name }}",
            "EXPECTED_SHA": "${{ needs.release-please.outputs.tag_sha }}",
            "EXPECTED_VERSION": "${{ needs.release-please.outputs.version }}",
            "EXPECTED_MANIFEST_SHA256": "${{ needs.build.outputs.bundle_manifest_sha256 }}",
            "EXPECTED_RELEASE_ID": "${{ needs.release-please.outputs.release_id }}",
            "EXPECTED_RELEASE_NODE_ID": ("${{ needs.release-please.outputs.release_node_id }}"),
            "EXPECTED_RELEASE_ASSETS_SHA256": (
                "${{ needs.release-please.outputs.release_assets_sha256 }}"
            ),
        }
        _assert_exact_recapture_command(recapture["run"])


def test_mutation_guard_contract_rejects_comment_deception() -> None:
    deceptive = "echo '# python tools/verify_release_identity.py --tag fake'"
    with pytest.raises(AssertionError):
        _assert_exact_recapture_command(deceptive)


def test_release_jobs_have_least_privilege_and_pypi_is_oidc_only() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    text = WORKFLOW.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["release-please"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert jobs["attach-release-assets"]["permissions"] == {"contents": "write"}
    assert "PYPI_API_TOKEN" not in text
    publishers = [
        step
        for step in jobs["publish"]["steps"]
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    ]
    assert len(publishers) == 1
    assert "password" not in publishers[0].get("with", {})


def test_release_assets_wait_for_publish_and_use_fail_closed_uploader() -> None:
    workflow = _workflow()
    attach = workflow["jobs"]["attach-release-assets"]
    steps = attach["steps"]

    assert "publish" in attach["needs"]
    assert attach["if"] == (
        "needs.release-please.outputs.release_created == 'true' && "
        "needs.publish.result == 'success'"
    )
    assert all(
        not step.get("uses", "").startswith("softprops/action-gh-release@") for step in steps
    )
    assert steps[-2]["id"] == "recapture-release"
    assert steps[-1]["id"] == "upload-release-assets"
    assert shlex.split(steps[-1]["run"], posix=True)[0:2] == [
        "python",
        "tools/upload_release_assets.py",
    ]
    assert "--clobber" not in steps[-1]["run"]


def test_release_workflow_matches_structural_mutation_contract() -> None:
    _workflow_guard().verify_workflow(_workflow())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"].append(
            {"id": "extra-release-mutation", "run": "gh release upload v0.4.0 evil --clobber"}
        ),
        lambda workflow: workflow["jobs"]["attach-release-assets"].__setitem__(
            "if", "${{ always() }}"
        ),
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"].insert(
            -1,
            copy.deepcopy(workflow["jobs"]["attach-release-assets"]["steps"][-2]),
        ),
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"][-2].__setitem__(
            "continue-on-error", "true"
        ),
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"][-1].__setitem__(
            "id", "renamed-real-uploader"
        ),
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"][-3].__setitem__(
            "run",
            workflow["jobs"]["attach-release-assets"]["steps"][-3]["run"]
            + "\ngh release upload v0.4.0 evil --clobber",
        ),
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"].reverse(),
        lambda workflow: workflow["jobs"]["attach-release-assets"]["steps"][-1].update(
            {
                "uses": "softprops/action-gh-release@3bb0d6d05975cfd60b10da845f6a7b1cbd1e2a67",
                "with": {"overwrite_files": "true"},
            }
        ),
    ],
)
def test_release_workflow_guard_rejects_decoys_reordering_and_extra_mutation(mutate) -> None:
    workflow = copy.deepcopy(_workflow())
    mutate(workflow)

    with pytest.raises(ValueError):
        _workflow_guard().verify_workflow(workflow)


def test_build_toolchain_is_hash_locked() -> None:
    workflow = _workflow()
    install = _step(workflow["jobs"]["build"], "Install reviewed build toolchain")
    requirements = REQUIREMENTS.read_text(encoding="utf-8")

    assert "--require-hashes" in install["run"]
    assert "tools/release-build-requirements.txt" in install["run"]
    assert "build==" in requirements
    assert "twine==" in requirements
    assert "--hash=sha256:" in requirements


def test_released_030_notes_are_not_left_under_unreleased() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased, released_030 = (
        changelog.split("## [0.4.0]", 1)[0],
        changelog.split("## [0.3.0]", 1)[1],
    )
    released_030 = released_030.split("## [0.2.0]", 1)[0]
    unreleased = re.sub(r"\s+", " ", unreleased)
    released_030 = re.sub(r"\s+", " ", released_030)

    assert "typed map, object, tileset" not in unreleased
    assert "subprocess cancellation and deadlines" not in unreleased
    assert "pinned Tiled 1.12.2" not in unreleased
    assert "typed map, object, tileset" in released_030
    assert "subprocess cancellation and deadlines" in released_030
    assert "pinned Tiled 1.12.2" in released_030
