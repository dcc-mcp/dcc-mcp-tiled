"""Structurally verify the exact release workflow mutation contract."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml

ACTION_PINS = {
    "actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
    "actions/download-artifact": "634f93cb2916e3fdff6788551b99b062d0335ce0",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "googleapis/release-please-action": "45996ed1f6d02564a971a2fa1b5860e934307cf7",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}

STEP_IDS = {
    "release-please": [
        "release",
        "target",
        "checkout-release",
        "setup-python",
        "install-version-verifier",
        "verify-target",
        "release-identity",
    ],
    "build": [
        "checkout-release",
        "setup-python",
        "install-version-verifier",
        "verify-target",
        "install-build-toolchain",
        "build-bundle",
        "bundle-identity",
        "upload-bundle",
    ],
    "publish": [
        "checkout-release",
        "setup-python",
        "install-version-verifier",
        "verify-target",
        "recapture-bundle",
        "download-bundle",
        "verify-bundle",
        "recapture-release",
        "publish-pypi",
    ],
    "attach-release-assets": [
        "checkout-release",
        "setup-python",
        "install-version-verifier",
        "verify-target",
        "recapture-bundle",
        "download-bundle",
        "verify-bundle",
        "recapture-release",
        "upload-release-assets",
    ],
}

RUN_SHA256 = {
    (
        "release-please",
        "target",
    ): "1cfa19ff3cb26d808ccd2104ca675b1dfc2806f2cd4c15a5f71499bee7a032b1",
    ("release-please", "install-version-verifier"): (
        "8565e51cb1bd84bbf3a777223bac3f3efa83bd95556877519dd50fa58ea5c82b"
    ),
    ("release-please", "verify-target"): (
        "28ef3cbcbb8e2f484928e4520d866a1065076eb4c39e08378c301eb255bf314f"
    ),
    ("release-please", "release-identity"): (
        "491f3f484698f808dd183a3f67680db3c6c5bb1047c292cd1974bd4b243edb1c"
    ),
    ("build", "install-version-verifier"): (
        "8565e51cb1bd84bbf3a777223bac3f3efa83bd95556877519dd50fa58ea5c82b"
    ),
    ("build", "verify-target"): (
        "28ef3cbcbb8e2f484928e4520d866a1065076eb4c39e08378c301eb255bf314f"
    ),
    ("build", "install-build-toolchain"): (
        "308f0e905761d27d044cca35cfdcaa6f194bdf59e7badfc279695491b922e4c0"
    ),
    ("build", "build-bundle"): ("0465990c618430b3d461d1803937cfa82de00a2e34140fef77c735417dcdc178"),
    ("build", "bundle-identity"): (
        "171ec83824da0a6306c4033fbdeebabbd1d30a9e2ea5b9c5550cbe4c154732f1"
    ),
    ("publish", "install-version-verifier"): (
        "8565e51cb1bd84bbf3a777223bac3f3efa83bd95556877519dd50fa58ea5c82b"
    ),
    ("publish", "verify-target"): (
        "28ef3cbcbb8e2f484928e4520d866a1065076eb4c39e08378c301eb255bf314f"
    ),
    ("publish", "recapture-bundle"): (
        "0a718155a8773a8282407d39788398ad5198fea7196b15a8cd2ec7d9355ce7a6"
    ),
    ("publish", "verify-bundle"): (
        "8180a3c9bb8a39e094122b121f875e84cf1cdc5b1a0e4d790e86602e0fc1ab72"
    ),
    ("publish", "recapture-release"): (
        "0a92935a9a80520b7938db042ee79d6149dac7747592bc0a99864357b54a9895"
    ),
    ("attach-release-assets", "install-version-verifier"): (
        "8565e51cb1bd84bbf3a777223bac3f3efa83bd95556877519dd50fa58ea5c82b"
    ),
    ("attach-release-assets", "verify-target"): (
        "28ef3cbcbb8e2f484928e4520d866a1065076eb4c39e08378c301eb255bf314f"
    ),
    ("attach-release-assets", "recapture-bundle"): (
        "0a718155a8773a8282407d39788398ad5198fea7196b15a8cd2ec7d9355ce7a6"
    ),
    ("attach-release-assets", "verify-bundle"): (
        "8180a3c9bb8a39e094122b121f875e84cf1cdc5b1a0e4d790e86602e0fc1ab72"
    ),
    ("attach-release-assets", "recapture-release"): (
        "0a92935a9a80520b7938db042ee79d6149dac7747592bc0a99864357b54a9895"
    ),
    ("attach-release-assets", "upload-release-assets"): (
        "ff15b544c7d628000a1de3bdab0038d3a3d2bd9fc8ed663e06b2caa2df1c8dfa"
    ),
}

RELEASE_OUTPUTS = {
    "release_created": "${{ steps.release.outputs.release_created }}",
    "tag_name": "${{ steps.target.outputs.tag_name }}",
    "tag_sha": "${{ steps.target.outputs.tag_sha }}",
    "version": "${{ steps.target.outputs.version }}",
    "release_id": "${{ steps.release-identity.outputs.release_id }}",
    "release_node_id": "${{ steps.release-identity.outputs.release_node_id }}",
    "release_assets_sha256": "${{ steps.release-identity.outputs.release_assets_sha256 }}",
}

WORKFLOW_METADATA = {
    "name": "Release",
    "on": {"push": {"branches": ["main"]}},
    "concurrency": {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "false",
    },
    "permissions": {"contents": "read"},
}

JOB_METADATA = {
    "release-please": {
        "runs-on": "ubuntu-latest",
        "permissions": {"contents": "write", "pull-requests": "write"},
        "outputs": RELEASE_OUTPUTS,
    },
    "build": {
        "needs": "release-please",
        "if": "needs.release-please.outputs.release_created == 'true'",
        "runs-on": "ubuntu-latest",
        "permissions": {"contents": "read"},
        "outputs": {
            "bundle_manifest_sha256": "${{ steps.bundle-identity.outputs.sha256 }}",
            "bundle_artifact_id": "${{ steps.upload-bundle.outputs.artifact-id }}",
            "bundle_artifact_digest": "${{ steps.upload-bundle.outputs.artifact-digest }}",
        },
    },
    "publish": {
        "needs": ["release-please", "build"],
        "if": "needs.release-please.outputs.release_created == 'true'",
        "runs-on": "ubuntu-latest",
        "environment": {
            "name": "pypi",
            "url": "https://pypi.org/p/dcc-mcp-tiled",
        },
        "permissions": {"actions": "read", "contents": "read", "id-token": "write"},
    },
    "attach-release-assets": {
        "needs": ["release-please", "build", "publish"],
        "if": (
            "needs.release-please.outputs.release_created == 'true' "
            "&& needs.publish.result == 'success'"
        ),
        "runs-on": "ubuntu-latest",
        "permissions": {"actions": "read", "contents": "write"},
    },
}

STEP_NAMES = {
    ("release-please", "target"): "Resolve immutable release target",
    ("release-please", "checkout-release"): "Check out the exact release target",
    ("release-please", "setup-python"): "Set up Python for the release identity guard",
    ("release-please", "install-version-verifier"): ("Install the semantic version verifier"),
    ("release-please", "verify-target"): "Verify immutable release target",
    ("release-please", "release-identity"): (
        "Freeze the exact GitHub Release entity and asset baseline"
    ),
    ("build", "install-version-verifier"): "Install the semantic version verifier",
    ("build", "verify-target"): "Verify immutable release target",
    ("build", "install-build-toolchain"): "Install reviewed build toolchain",
    ("build", "build-bundle"): "Build and validate distributions",
    ("build", "bundle-identity"): "Freeze release bundle identity",
    ("build", "upload-bundle"): "Upload immutable release bundle",
    ("publish", "install-version-verifier"): "Install the semantic version verifier",
    ("publish", "verify-target"): "Verify immutable release target",
    ("publish", "recapture-bundle"): "Recapture exact GitHub Actions artifact identity",
    ("publish", "download-bundle"): "Download immutable release bundle",
    ("publish", "verify-bundle"): "Verify release artifacts",
    ("publish", "recapture-release"): (
        "Recapture remote release identity immediately before mutation"
    ),
    ("publish", "publish-pypi"): "Publish to PyPI with trusted publishing",
    ("attach-release-assets", "install-version-verifier"): (
        "Install the semantic version verifier"
    ),
    ("attach-release-assets", "verify-target"): "Verify immutable release target",
    ("attach-release-assets", "recapture-bundle"): (
        "Recapture exact GitHub Actions artifact identity"
    ),
    ("attach-release-assets", "download-bundle"): "Download immutable release bundle",
    ("attach-release-assets", "verify-bundle"): "Verify release artifacts",
    ("attach-release-assets", "recapture-release"): (
        "Recapture remote release identity immediately before mutation"
    ),
    ("attach-release-assets", "upload-release-assets"): (
        "Attach the exact verified assets to the frozen release"
    ),
}

ACTION_NAMES = {
    ("release-please", "release"): "googleapis/release-please-action",
    ("release-please", "checkout-release"): "actions/checkout",
    ("release-please", "setup-python"): "actions/setup-python",
    ("build", "checkout-release"): "actions/checkout",
    ("build", "setup-python"): "actions/setup-python",
    ("build", "upload-bundle"): "actions/upload-artifact",
    ("publish", "checkout-release"): "actions/checkout",
    ("publish", "setup-python"): "actions/setup-python",
    ("publish", "download-bundle"): "actions/download-artifact",
    ("publish", "publish-pypi"): "pypa/gh-action-pypi-publish",
    ("attach-release-assets", "checkout-release"): "actions/checkout",
    ("attach-release-assets", "setup-python"): "actions/setup-python",
    ("attach-release-assets", "download-bundle"): "actions/download-artifact",
}

NEEDS_CHECKOUT = {
    "ref": "${{ needs.release-please.outputs.tag_name }}",
    "fetch-depth": "0",
}
ACTION_INPUTS = {
    ("release-please", "release"): {
        "token": "${{ secrets.PERSONAL_ACCESS_TOKEN || github.token }}",
        "config-file": "release-please-config.json",
        "manifest-file": ".release-please-manifest.json",
    },
    ("release-please", "checkout-release"): {
        "ref": "${{ steps.target.outputs.tag_name }}",
        "fetch-depth": "0",
    },
    ("release-please", "setup-python"): {"python-version": "3.12"},
    ("build", "checkout-release"): NEEDS_CHECKOUT,
    ("build", "setup-python"): {"python-version": "3.12"},
    ("build", "upload-bundle"): {
        "name": "release-bundle",
        "path": "release-bundle/",
        "if-no-files-found": "error",
        "compression-level": "0",
        "retention-days": "7",
    },
    ("publish", "checkout-release"): NEEDS_CHECKOUT,
    ("publish", "setup-python"): {"python-version": "3.12"},
    ("publish", "download-bundle"): {
        "artifact-ids": "${{ needs.build.outputs.bundle_artifact_id }}",
        "path": "release-bundle",
    },
    ("publish", "publish-pypi"): {
        "packages-dir": "release-bundle/dist",
        "verbose": "true",
        "print-hash": "true",
    },
    ("attach-release-assets", "checkout-release"): NEEDS_CHECKOUT,
    ("attach-release-assets", "setup-python"): {"python-version": "3.12"},
    ("attach-release-assets", "download-bundle"): {
        "artifact-ids": "${{ needs.build.outputs.bundle_artifact_id }}",
        "path": "release-bundle",
    },
}

NEEDS_TARGET_ENV = {
    "EXPECTED_TAG": "${{ needs.release-please.outputs.tag_name }}",
    "EXPECTED_SHA": "${{ needs.release-please.outputs.tag_sha }}",
    "EXPECTED_VERSION": "${{ needs.release-please.outputs.version }}",
}
STEP_SHELLS = {
    ("release-please", "target"),
    ("release-please", "verify-target"),
    ("release-please", "release-identity"),
    ("build", "verify-target"),
    ("build", "bundle-identity"),
    ("publish", "verify-target"),
    ("publish", "recapture-bundle"),
    ("publish", "verify-bundle"),
    ("publish", "recapture-release"),
    ("attach-release-assets", "verify-target"),
    ("attach-release-assets", "recapture-bundle"),
    ("attach-release-assets", "verify-bundle"),
    ("attach-release-assets", "recapture-release"),
    ("attach-release-assets", "upload-release-assets"),
}

RELEASE_CONDITION = "steps.release.outputs.release_created == 'true'"
STEP_CONDITIONS = {
    ("release-please", step_id): RELEASE_CONDITION for step_id in STEP_IDS["release-please"][1:]
}

RECAPTURE_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "EXPECTED_TAG": "${{ needs.release-please.outputs.tag_name }}",
    "EXPECTED_SHA": "${{ needs.release-please.outputs.tag_sha }}",
    "EXPECTED_VERSION": "${{ needs.release-please.outputs.version }}",
    "EXPECTED_MANIFEST_SHA256": "${{ needs.build.outputs.bundle_manifest_sha256 }}",
    "EXPECTED_RELEASE_ID": "${{ needs.release-please.outputs.release_id }}",
    "EXPECTED_RELEASE_NODE_ID": "${{ needs.release-please.outputs.release_node_id }}",
    "EXPECTED_RELEASE_ASSETS_SHA256": ("${{ needs.release-please.outputs.release_assets_sha256 }}"),
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

ARTIFACT_RECAPTURE_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "EXPECTED_ARTIFACT_ID": "${{ needs.build.outputs.bundle_artifact_id }}",
    "EXPECTED_ARTIFACT_DIGEST": "${{ needs.build.outputs.bundle_artifact_digest }}",
    "EXPECTED_RUN_ID": "${{ github.run_id }}",
    "EXPECTED_SHA": "${{ needs.release-please.outputs.tag_sha }}",
}

ARTIFACT_RECAPTURE_COMMAND = [
    "python",
    "tools/verify_workflow_artifact.py",
    "--repository",
    "${GITHUB_REPOSITORY}",
    "--artifact-id",
    "${EXPECTED_ARTIFACT_ID}",
    "--artifact-name",
    "release-bundle",
    "--artifact-digest",
    "${EXPECTED_ARTIFACT_DIGEST}",
    "--run-id",
    "${EXPECTED_RUN_ID}",
    "--head-sha",
    "${EXPECTED_SHA}",
]

UPLOAD_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "EXPECTED_TAG": "${{ needs.release-please.outputs.tag_name }}",
    "EXPECTED_SHA": "${{ needs.release-please.outputs.tag_sha }}",
    "EXPECTED_VERSION": "${{ needs.release-please.outputs.version }}",
    "EXPECTED_MANIFEST_SHA256": "${{ needs.build.outputs.bundle_manifest_sha256 }}",
    "EXPECTED_RELEASE_ID": "${{ needs.release-please.outputs.release_id }}",
    "EXPECTED_RELEASE_NODE_ID": "${{ needs.release-please.outputs.release_node_id }}",
    "EXPECTED_RELEASE_ASSETS_SHA256": ("${{ needs.release-please.outputs.release_assets_sha256 }}"),
}

STEP_ENVS = {
    ("release-please", "target"): {
        "GH_TOKEN": "${{ github.token }}",
        "RELEASE_TAG": "${{ steps.release.outputs.tag_name }}",
        "RELEASE_VERSION": "${{ steps.release.outputs.version }}",
    },
    ("release-please", "verify-target"): {
        "EXPECTED_TAG": "${{ steps.target.outputs.tag_name }}",
        "EXPECTED_SHA": "${{ steps.target.outputs.tag_sha }}",
        "EXPECTED_VERSION": "${{ steps.target.outputs.version }}",
    },
    ("release-please", "release-identity"): {
        "GH_TOKEN": "${{ github.token }}",
        "EXPECTED_TAG": "${{ steps.target.outputs.tag_name }}",
        "EXPECTED_SHA": "${{ steps.target.outputs.tag_sha }}",
    },
    ("build", "verify-target"): NEEDS_TARGET_ENV,
    ("build", "build-bundle"): {"EXPECTED_VERSION": "${{ needs.release-please.outputs.version }}"},
    ("publish", "verify-target"): NEEDS_TARGET_ENV,
    ("publish", "recapture-bundle"): ARTIFACT_RECAPTURE_ENV,
    ("publish", "verify-bundle"): {
        "EXPECTED_MANIFEST_SHA256": "${{ needs.build.outputs.bundle_manifest_sha256 }}"
    },
    ("publish", "recapture-release"): RECAPTURE_ENV,
    ("attach-release-assets", "verify-target"): NEEDS_TARGET_ENV,
    ("attach-release-assets", "recapture-bundle"): ARTIFACT_RECAPTURE_ENV,
    ("attach-release-assets", "verify-bundle"): {
        "EXPECTED_MANIFEST_SHA256": "${{ needs.build.outputs.bundle_manifest_sha256 }}"
    },
    ("attach-release-assets", "recapture-release"): RECAPTURE_ENV,
    ("attach-release-assets", "upload-release-assets"): UPLOAD_ENV,
}

UPLOAD_COMMAND = [
    "python",
    "tools/upload_release_assets.py",
    "--repository",
    "${GITHUB_REPOSITORY}",
    "--release-id",
    "${EXPECTED_RELEASE_ID}",
    "--release-node-id",
    "${EXPECTED_RELEASE_NODE_ID}",
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
    "--release-assets-sha256",
    "${EXPECTED_RELEASE_ASSETS_SHA256}",
]

FREEZE_COMMAND = [
    "python",
    "tools/freeze_release_identity.py",
    "--repository",
    "${GITHUB_REPOSITORY}",
    "--tag",
    "${EXPECTED_TAG}",
    "--sha",
    "${EXPECTED_SHA}",
    "--github-output",
    "${GITHUB_OUTPUT}",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _command(step: dict[str, Any]) -> list[str]:
    script = step.get("run")
    _require(isinstance(script, str), "guarded release step must have an executable body")
    return shlex.split(script.replace("\\\n", " "), posix=True)


def _steps(job: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    steps = job.get("steps")
    _require(isinstance(steps, list), f"{job_name} steps are invalid")
    _require(all(isinstance(step, dict) for step in steps), f"{job_name} step is invalid")
    typed_steps = list(steps)
    ids = [step.get("id") for step in typed_steps]
    _require(ids == STEP_IDS[job_name], f"{job_name} executable step order changed")
    _require(len(ids) == len(set(ids)), f"{job_name} contains duplicate or decoy steps")
    return typed_steps


def _step_by_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    return next(step for step in steps if step["id"] == step_id)


def _verify_complete_shape(
    workflow: dict[str, Any],
    jobs: dict[str, Any],
    steps: dict[str, list[dict[str, Any]]],
) -> None:
    _require(
        set(workflow) == {*WORKFLOW_METADATA, "jobs"},
        "release workflow top-level shape changed",
    )
    for key, expected in WORKFLOW_METADATA.items():
        _require(workflow.get(key) == expected, f"release workflow {key} changed")

    _require(set(JOB_METADATA) == set(jobs), "release job contract is incomplete")
    for job_name, expected_metadata in JOB_METADATA.items():
        job = jobs[job_name]
        _require(
            set(job) == {*expected_metadata, "steps"},
            f"{job_name} job shape changed",
        )
        observed_metadata = {key: value for key, value in job.items() if key != "steps"}
        _require(observed_metadata == expected_metadata, f"{job_name} job controls changed")

    all_steps = {
        (job_name, step["id"]) for job_name, job_steps in steps.items() for step in job_steps
    }
    _require(
        set(ACTION_NAMES) == set(ACTION_INPUTS),
        "release action input contract is incomplete",
    )
    _require(
        all_steps == set(ACTION_NAMES) | set(RUN_SHA256),
        "release executable step contract is incomplete",
    )

    for job_name, job_steps in steps.items():
        for step in job_steps:
            key = (job_name, step["id"])
            is_action = key in ACTION_NAMES
            is_command = key in RUN_SHA256
            _require(is_action != is_command, f"{job_name}/{step['id']} execution is ambiguous")

            expected_keys = {"id", "uses", "with"} if is_action else {"id", "run"}
            if key in STEP_NAMES:
                expected_keys.add("name")
            if key in STEP_ENVS:
                expected_keys.add("env")
            if key in STEP_SHELLS:
                expected_keys.add("shell")
            if key in STEP_CONDITIONS:
                expected_keys.add("if")
            _require(set(step) == expected_keys, f"{job_name}/{step['id']} shape changed")

            if key in STEP_NAMES:
                _require(
                    step.get("name") == STEP_NAMES[key], f"{job_name}/{step['id']} name changed"
                )
            if is_action:
                action = ACTION_NAMES[key]
                _require(
                    step.get("uses") == f"{action}@{ACTION_PINS[action]}",
                    f"{job_name}/{step['id']} action changed",
                )
                _require(
                    step.get("with") == ACTION_INPUTS[key],
                    f"{job_name}/{step['id']} action inputs changed",
                )
            if key in STEP_ENVS:
                _require(step.get("env") == STEP_ENVS[key], f"{job_name}/{step['id']} env changed")
            if key in STEP_SHELLS:
                _require(step.get("shell") == "bash", f"{job_name}/{step['id']} shell changed")
            if key in STEP_CONDITIONS:
                _require(
                    step.get("if") == STEP_CONDITIONS[key],
                    f"{job_name}/{step['id']} condition changed",
                )


def _verify_action_pins(jobs: dict[str, Any]) -> None:
    seen: list[str] = []
    for job in jobs.values():
        for step in job["steps"]:
            value = step.get("uses")
            if value is None:
                continue
            _require(isinstance(value, str) and value.count("@") == 1, "release action is unpinned")
            action, commit = value.split("@")
            _require(ACTION_PINS.get(action) == commit, f"unexpected release action: {action}")
            seen.append(action)
    _require(seen.count("googleapis/release-please-action") == 1, "release creator is ambiguous")
    _require(seen.count("pypa/gh-action-pypi-publish") == 1, "PyPI publisher is ambiguous")


def _verify_no_hidden_mutation(jobs: dict[str, Any]) -> None:
    for job in jobs.values():
        for step in job["steps"]:
            condition = step.get("if", "")
            _require(
                "always(" not in str(condition).lower(), "always() may bypass publication gates"
            )
            uses = str(step.get("uses", "")).lower()
            _require(
                "softprops/action-gh-release" not in uses, "softprops release mutation is forbidden"
            )
            script = str(step.get("run", "")).lower()
            _require("gh release" not in script, "extra gh release mutation is forbidden")
            _require("--clobber" not in script, "release asset clobber is forbidden")
            _require("uploads.github.com" not in script, "unbound release upload is forbidden")


def _verify_executable_bodies(jobs: dict[str, Any]) -> None:
    observed: set[tuple[str, str]] = set()
    for job_name, job in jobs.items():
        for step in job["steps"]:
            _require(
                not ("uses" in step and "run" in step), "step mixes action and shell execution"
            )
            if "run" not in step:
                continue
            key = (job_name, step["id"])
            observed.add(key)
            digest = hashlib.sha256(step["run"].encode("utf-8")).hexdigest()
            _require(RUN_SHA256.get(key) == digest, f"{job_name}/{step['id']} body changed")
    _require(observed == set(RUN_SHA256), "release executable body set changed")


def verify_workflow(workflow: dict[str, Any]) -> None:
    _require(isinstance(workflow, dict), "release workflow document is invalid")
    jobs = workflow.get("jobs")
    _require(isinstance(jobs, dict), "release jobs are invalid")
    _require(list(jobs) == list(STEP_IDS), "release job set or order changed")
    typed_jobs = dict(jobs)
    steps = {name: _steps(typed_jobs[name], name) for name in STEP_IDS}
    _verify_complete_shape(workflow, typed_jobs, steps)

    release = typed_jobs["release-please"]
    _require(release.get("outputs") == RELEASE_OUTPUTS, "frozen release outputs changed")
    release_action = _step_by_id(steps["release-please"], "release")
    _require(
        release_action.get("uses")
        == "googleapis/release-please-action@" + ACTION_PINS["googleapis/release-please-action"],
        "release creator step changed",
    )
    freeze = _step_by_id(steps["release-please"], "release-identity")
    _require(
        freeze.get("if") == "steps.release.outputs.release_created == 'true'", "freeze gate changed"
    )
    _require(_command(freeze) == FREEZE_COMMAND, "release freeze executable body changed")
    for step in steps["release-please"][1:]:
        _require(
            step.get("if") == "steps.release.outputs.release_created == 'true'",
            "release identity step condition changed",
        )
        _require("continue-on-error" not in step, "release identity failure may be ignored")

    publish = typed_jobs["publish"]
    attach = typed_jobs["attach-release-assets"]
    release_gate = "needs.release-please.outputs.release_created == 'true'"
    _require(publish.get("needs") == ["release-please", "build"], "publish dependencies changed")
    _require(publish.get("if") == release_gate, "publish release-created gate changed")
    _require(
        attach.get("needs") == ["release-please", "build", "publish"],
        "release asset publish-success gate changed",
    )
    attach_gate = release_gate + " && needs.publish.result == 'success'"
    _require(attach.get("if") == attach_gate, "release asset publish-success gate changed")
    for job_name in ("build", "publish", "attach-release-assets"):
        for step in steps[job_name]:
            _require("if" not in step, f"{job_name} step condition changed")
            _require("continue-on-error" not in step, f"{job_name} failure may be ignored")

    for job_name in ("publish", "attach-release-assets"):
        artifact = _step_by_id(steps[job_name], "recapture-bundle")
        _require(
            set(artifact) == {"name", "id", "env", "shell", "run"},
            "artifact recapture shape changed",
        )
        _require(
            artifact.get("env") == ARTIFACT_RECAPTURE_ENV,
            "artifact recapture identity inputs changed",
        )
        _require(artifact.get("shell") == "bash", "artifact recapture shell changed")
        _require(
            _command(artifact) == ARTIFACT_RECAPTURE_COMMAND,
            "artifact recapture executable body changed",
        )

        recapture = _step_by_id(steps[job_name], "recapture-release")
        _require(set(recapture) == {"name", "id", "env", "shell", "run"}, "recapture shape changed")
        _require(recapture.get("env") == RECAPTURE_ENV, "recapture identity inputs changed")
        _require(recapture.get("shell") == "bash", "recapture shell changed")
        _require(_command(recapture) == RECAPTURE_COMMAND, "recapture executable body changed")

    publisher = _step_by_id(steps["publish"], "publish-pypi")
    _require(
        "if" not in publisher and "run" not in publisher, "PyPI publisher may bypass recapture"
    )
    _require(
        publisher.get("uses")
        == "pypa/gh-action-pypi-publish@" + ACTION_PINS["pypa/gh-action-pypi-publish"],
        "PyPI publisher step changed",
    )
    _require(
        publisher.get("with")
        == {
            "packages-dir": "release-bundle/dist",
            "verbose": "true",
            "print-hash": "true",
        },
        "PyPI publisher inputs changed",
    )
    uploader = _step_by_id(steps["attach-release-assets"], "upload-release-assets")
    _require(set(uploader) == {"name", "id", "env", "shell", "run"}, "uploader shape changed")
    _require(uploader.get("env") == UPLOAD_ENV, "uploader identity inputs changed")
    _require(uploader.get("shell") == "bash", "uploader shell changed")
    _require(_command(uploader) == UPLOAD_COMMAND, "uploader executable body changed")

    _verify_action_pins(typed_jobs)
    _verify_no_hidden_mutation(typed_jobs)
    _verify_executable_bodies(typed_jobs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    args = parser.parse_args(argv)
    payload = yaml.load(args.workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    verify_workflow(payload)
    print("release workflow mutation contract verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"release workflow verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
