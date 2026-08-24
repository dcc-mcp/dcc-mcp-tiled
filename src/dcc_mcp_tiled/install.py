"""Standalone Tiled verification backed by Core Install SOP v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from dcc_mcp_core import __version__ as core_version
from dcc_mcp_core.deployment import INSTALL_SOP_SCHEMA_VERSION

from .__version__ import __version__
from .bridge import TiledCli, TiledError, TiledLaunchError, TiledTimeoutError

MIN_CORE_VERSION = "0.20.14"
MIN_TILED_VERSION = "1.10.0"
INSTALL_GUIDE_URL = "https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-tiled/main/install.md"
_VERSION_COMPONENT = r"(?:0|[1-9][0-9]{0,5})"
_FINAL_RELEASE = re.compile(
    r"^(%s)\.(%s)\.(%s)$" % (_VERSION_COMPONENT, _VERSION_COMPONENT, _VERSION_COMPONENT)
)
_MAX_VERSION_LENGTH = 32


@dataclass(frozen=True)
class DoctorRequest:
    """Arguments needed to evaluate one read-only runtime verification."""

    operation: str
    executable: Optional[str]
    install_alias: bool


@dataclass(frozen=True)
class CoreCliRelease:
    """Pinned identity from an attested official Core update manifest."""

    platform_key: str
    asset_name: str
    size: int
    sha256: str
    manifest_sha256: str
    manifest_bundle_sha256: str


@dataclass(frozen=True)
class VerifiedCoreCli:
    """Exact CLI identity that remained stable across bounded verification."""

    path: str
    version: str
    sha256: str
    manifest_sha256: str
    manifest_bundle_sha256: str


@dataclass(frozen=True)
class CoreCliProbe:
    """Fail-closed result of resolving and authenticating the Core CLI."""

    verified: Optional[VerifiedCoreCli]
    failure_reason: Optional[str]


_CORE_CLI_RELEASES = {
    "linux-x86_64": CoreCliRelease(
        platform_key="linux-x86_64",
        asset_name="dcc-mcp-cli-linux-x86_64",
        size=40_613_120,
        sha256="312bc63f744b62c34ccd48405f77d900d0a8a6d0b7dd18cddfaa4cb34aa4cb56",
        manifest_sha256="fae68f7d7285d5b63acfd8eb312bc5cd3b9f86ee436566809d5c086c76ce9ebf",
        manifest_bundle_sha256=("10416da17bff09a09f263ea7f9a68d665ea1859fc6f1021e0aa1a8e55a519edf"),
    ),
    "windows-x86_64": CoreCliRelease(
        platform_key="windows-x86_64",
        asset_name="dcc-mcp-cli-windows-x86_64.exe",
        size=43_001_856,
        sha256="b463ace343322712baa667846ddbd1bbced09b854829d087d61372108c156b62",
        manifest_sha256="7d0751fba84406a206bbfd31d7852ae216d84ec9f4d718e493cee48b713a48a8",
        manifest_bundle_sha256=("1c7fde6d725be60b5b80076d37f5a691f40585896f297f48f4139a4736198511"),
    ),
    "macos-universal2": CoreCliRelease(
        platform_key="macos-universal2",
        asset_name="dcc-mcp-cli-macos-universal2",
        size=73_712_432,
        sha256="99730209fed774e5254905215ab80975abe9b21ca3a6675cdb645e53126c7d27",
        manifest_sha256="8be7561f08139e68e755f55f2e54c971e500a0b520912397e8fa91299bcf385e",
        manifest_bundle_sha256=("c27e26aba9fd420e89e51e656d96f18dce209bb03ae3eeaf127d9f78350121d2"),
    ),
}


def _version_tuple(value: object) -> Optional[tuple[int, int, int]]:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_LENGTH:
        return None
    match = _FINAL_RELEASE.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _version_at_least(value: object, minimum: str) -> bool:
    current = _version_tuple(value)
    floor = _version_tuple(minimum)
    return current is not None and floor is not None and current >= floor


def _bounded_version_text(value: object) -> str:
    if isinstance(value, str) and 0 < len(value) <= _MAX_VERSION_LENGTH:
        return value
    return "invalid"


def _core_cli_release() -> Optional[CoreCliRelease]:
    machine = platform.machine().lower()
    if machine not in {"amd64", "x86_64", "arm64", "aarch64"}:
        return None
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return _CORE_CLI_RELEASES["windows-x86_64"]
    if sys.platform.startswith("linux") and machine in {"amd64", "x86_64"}:
        return _CORE_CLI_RELEASES["linux-x86_64"]
    if sys.platform == "darwin":
        return _CORE_CLI_RELEASES["macos-universal2"]
    return None


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    stat_result = path.stat()
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def _sha256(path: Path, expected_size: int) -> Optional[str]:
    try:
        if path.stat().st_size != expected_size:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _resolved_core_cli_candidate() -> tuple[Optional[Path], str]:
    configured = os.environ.get("DCC_MCP_TILED_CORE_CLI")
    candidate = configured or shutil.which("dcc-mcp-cli")
    if not candidate:
        return None, "not_found"
    try:
        path = Path(candidate)
        if configured and not path.is_absolute():
            return None, "configured_path_not_absolute"
        return path.resolve(strict=True), "configured" if configured else "path"
    except (OSError, RuntimeError):
        return None, "path_unresolvable"


def _same_resolved_candidate(expected: Path, source: str) -> bool:
    configured = os.environ.get("DCC_MCP_TILED_CORE_CLI")
    candidate = configured if source == "configured" else shutil.which("dcc-mcp-cli")
    if not candidate:
        return False
    try:
        return os.path.normcase(str(Path(candidate).resolve(strict=True))) == os.path.normcase(
            str(expected)
        )
    except (OSError, RuntimeError):
        return False


def _probe_core_cli() -> CoreCliProbe:
    if core_version != MIN_CORE_VERSION:
        return CoreCliProbe(None, "release_not_attested")
    release = _core_cli_release()
    if release is None:
        return CoreCliProbe(None, "platform_not_attested")
    executable, source = _resolved_core_cli_candidate()
    if executable is None:
        return CoreCliProbe(None, source)
    try:
        initial_identity = _file_identity(executable)
    except OSError:
        return CoreCliProbe(None, "identity_unavailable")
    digest = _sha256(executable, release.size)
    if digest != release.sha256:
        return CoreCliProbe(None, "digest_mismatch")
    try:
        if _file_identity(executable) != initial_identity:
            return CoreCliProbe(None, "identity_changed")
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CoreCliProbe(None, "version_probe_failed")
    if completed.returncode != 0:
        return CoreCliProbe(None, "version_probe_failed")
    prefix = "dcc-mcp-cli "
    output = completed.stdout.strip()
    version = output[len(prefix) :] if output.startswith(prefix) else ""
    if _version_tuple(version) is None:
        return CoreCliProbe(None, "version_invalid")
    if not _same_resolved_candidate(executable, source):
        return CoreCliProbe(None, "path_changed")
    try:
        if _file_identity(executable) != initial_identity:
            return CoreCliProbe(None, "identity_changed")
    except OSError:
        return CoreCliProbe(None, "identity_unavailable")
    if _sha256(executable, release.size) != release.sha256:
        return CoreCliProbe(None, "digest_changed")
    return CoreCliProbe(
        VerifiedCoreCli(
            path=str(executable),
            version=version,
            sha256=release.sha256,
            manifest_sha256=release.manifest_sha256,
            manifest_bundle_sha256=release.manifest_bundle_sha256,
        ),
        None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the official Tiled CLI runtime; no host plug-in is installed."
    )
    parser.add_argument(
        "operation", nargs="?", choices=("doctor", "verify", "catalog-plan"), default="doctor"
    )
    parser.add_argument("--json", action="store_true", help="Emit the stable JSON contract.")
    parser.add_argument("--executable", help="Exact Tiled executable to verify.")
    return parser


def _install_alias() -> bool:
    invoked_name = Path(sys.argv[0]).name.lower()
    return invoked_name in {"dcc-mcp-tiled-install", "dcc-mcp-tiled-install.exe"}


def _status_snapshot(cli: TiledCli) -> dict[str, Any]:
    return {
        "ready": False,
        "executable": cli.executable,
        "instance_type": "standalone",
        "driver": "official_tiled_cli_script_api",
        "allowed_roots": [str(root) for root in cli.allowed_roots],
        "limits": {"max_timeout_secs": cli.max_timeout_secs},
    }


def _command_step(
    identifier: str, description: str, command: list[str], why: str, **details: Any
) -> dict[str, Any]:
    step = {
        "id": identifier,
        "description": description,
        "command": command,
        "why": why,
    }
    step.update(details)
    return step


def _tiled_blocker() -> dict[str, Any]:
    return {
        "reason": "verified_tiled_acquisition_required",
        "instructions_url": INSTALL_GUIDE_URL,
        "minimum_version": MIN_TILED_VERSION,
    }


def _core_cli_blocker(probe: CoreCliProbe) -> dict[str, Any]:
    release = _core_cli_release()
    blocker: dict[str, Any] = {
        "reason": "verified_core_cli_required",
        "probe_failure": probe.failure_reason,
        "minimum_version": MIN_CORE_VERSION,
        "release_url": "https://github.com/dcc-mcp/dcc-mcp-core/releases/tag/v%s"
        % MIN_CORE_VERSION,
    }
    if release is not None:
        blocker.update(
            {
                "asset_name": release.asset_name,
                "asset_sha256": release.sha256,
                "manifest_sha256": release.manifest_sha256,
                "manifest_bundle_sha256": release.manifest_bundle_sha256,
            }
        )
    return blocker


def _exact_retry_command(request: DoctorRequest, status: dict[str, Any]) -> list[str]:
    command = ["dcc-mcp-tiled-doctor", request.operation, "--json"]
    executable = status.get("executable") or request.executable
    if executable:
        try:
            exact_path = str(Path(str(executable)).resolve(strict=True))
        except (OSError, RuntimeError):
            exact_path = None
        if exact_path:
            command.extend(["--executable", exact_path])
    return command


def _catalog_plan_step(core_cli: VerifiedCoreCli) -> dict[str, Any]:
    return _command_step(
        "resolve-adapter-artifact",
        "Resolve the bounded Tiled adapter catalog plan with the reverified official Core CLI.",
        ["dcc-mcp-tiled-doctor", "catalog-plan", "--json"],
        "The bundled fixed Tiled driver is missing and the catalog must be resolved safely.",
        action="resolve_adapter_artifact",
        executes_install=False,
        core_cli_path=core_cli.path,
        core_cli_sha256=core_cli.sha256,
        manifest_sha256=core_cli.manifest_sha256,
        manifest_bundle_sha256=core_cli.manifest_bundle_sha256,
    )


def _report(
    request: DoctorRequest,
    status: dict[str, Any],
    exit_code: int,
    failure: Optional[dict[str, str]],
    next_steps: list[dict[str, Any]],
    *,
    core_cli: Optional[VerifiedCoreCli] = None,
    blocker: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    status = dict(status)
    if "version" in status:
        status["version"] = _bounded_version_text(status["version"])
    reported_core_version = _bounded_version_text(core_version)
    core_cli_version = core_cli.version if core_cli is not None else None
    if request.executable:
        discovery_source = "explicit_argument"
    elif os.environ.get("DCC_MCP_TILED_EXECUTABLE"):
        discovery_source = "environment"
    elif status.get("executable"):
        discovery_source = "path_or_common_location"
    else:
        discovery_source = "not_found"
    directly_usable = exit_code == 0
    failure_stage = failure.get("stage") if failure else None
    failure_reason = failure.get("reason") if failure else None
    steps = [
        {
            "id": failure_stage or "verify-runtime",
            "status": "failed" if failure else "ok",
            **({"message": failure_reason} if failure_reason else {}),
        }
    ]
    return {
        "schema_version": INSTALL_SOP_SCHEMA_VERSION,
        "legacy_schema_version": "1.0",
        "status": "ok" if directly_usable else "failed",
        "legacy_status": "ok" if directly_usable else "error" if exit_code == 40 else "not_ready",
        "dcc_type": "tiled",
        "adapter_version": __version__,
        "core_version": reported_core_version,
        "steps": steps,
        "receipt_path": None,
        "verify": {
            "directly_usable": directly_usable,
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
        },
        "operation": request.operation,
        "exit_code": exit_code,
        "directly_usable": directly_usable,
        "adapter": {
            "name": "dcc-mcp-tiled",
            "version": __version__,
            "runtime_shape": "standalone",
            "contract": "core_install_sop_v1_verify_only",
        },
        "compatibility": {
            "deprecated_install_alias": request.install_alias,
            "mode": "verify_only",
            "writes_performed": False,
            "replacement_command": ["dcc-mcp-tiled-doctor", "doctor", "--json"],
        },
        "requirements": {
            "min_core_version": MIN_CORE_VERSION,
            "min_core_cli_version": MIN_CORE_VERSION,
            "min_tiled_version": MIN_TILED_VERSION,
            "core_version": reported_core_version,
            "core_cli_version": (
                _bounded_version_text(core_cli_version) if core_cli_version is not None else None
            ),
            "core_cli_matches_python": (
                core_cli_version == core_version if core_cli_version is not None else None
            ),
            "core_cli_path": core_cli.path if core_cli is not None else None,
            "core_cli_sha256": core_cli.sha256 if core_cli is not None else None,
            "core_cli_manifest_sha256": (
                core_cli.manifest_sha256 if core_cli is not None else None
            ),
            "core_cli_manifest_bundle_sha256": (
                core_cli.manifest_bundle_sha256 if core_cli is not None else None
            ),
        },
        "configuration": {
            "allowed_roots": status.get("allowed_roots", []),
            "limits": status.get("limits", {}),
            "executable_environment": "DCC_MCP_TILED_EXECUTABLE",
            "roots_environment": "DCC_MCP_TILED_ALLOWED_ROOTS",
        },
        "discovery": {
            "executable": status.get("executable"),
            "source": discovery_source,
            "driver": status.get("driver"),
            "endpoint_kind": "local_cli",
        },
        "provisioning": {
            "auto_provision": False,
            "binary_source": "operating_system",
            "persistent_adapter_cache": None,
        },
        "failure": failure,
        "blocker": blocker,
        "next_steps": next_steps,
        "runtime": status,
    }


def _configuration_failure(request: DoctorRequest) -> dict[str, Any]:
    return _report(
        request,
        {"ready": False},
        10,
        {"stage": "configuration_preflight", "reason": "invalid_environment"},
        [
            _command_step(
                "fix-environment",
                "Correct DCC_MCP_TILED_* values and rerun doctor.",
                ["dcc-mcp-tiled-doctor", "doctor", "--json"],
                "Invalid environment values prevent bounded runtime verification.",
                action="fix_environment",
            )
        ],
    )


def _evaluate(request: DoctorRequest, cli: TiledCli) -> dict[str, Any]:
    if _version_tuple(core_version) is None:
        return _report(
            request,
            _status_snapshot(cli),
            10,
            {"stage": "core_preflight", "reason": "core_version_invalid"},
            [
                _command_step(
                    "reinstall-core",
                    "Install a canonical final DCC-MCP Core release.",
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "dcc-mcp-core>=%s" % MIN_CORE_VERSION,
                    ],
                    "The installed Core version is not a bounded canonical final release.",
                    action="upgrade_core",
                    minimum_version=MIN_CORE_VERSION,
                )
            ],
        )
    if not _version_at_least(core_version, MIN_CORE_VERSION):
        return _report(
            request,
            _status_snapshot(cli),
            10,
            {"stage": "core_preflight", "reason": "core_version_below_floor"},
            [
                _command_step(
                    "upgrade-core",
                    "Upgrade DCC-MCP Core to the supported floor.",
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "dcc-mcp-core>=%s" % MIN_CORE_VERSION,
                    ],
                    "The installed Core release is older than this adapter supports.",
                    action="upgrade_core",
                    minimum_version=MIN_CORE_VERSION,
                )
            ],
        )
    try:
        status = cli.status()
    except TiledError as exc:
        if isinstance(exc, TiledTimeoutError):
            reason = "tiled_timeout"
        elif isinstance(exc, TiledLaunchError):
            reason = "tiled_launch_failed"
        else:
            reason = "tiled_runtime_error"
        return _report(
            request,
            _status_snapshot(cli),
            40,
            {"stage": "runtime_verification", "reason": reason},
            [
                _command_step(
                    "retry-runtime-verification",
                    "Check the Tiled launch environment, then retry verification.",
                    _exact_retry_command(
                        DoctorRequest("verify", request.executable, request.install_alias),
                        _status_snapshot(cli),
                    ),
                    "The bounded Tiled runtime probe failed.",
                    action="rerun_verify",
                )
            ],
        )
    if not status.get("ready"):
        reason = str(status.get("reason") or "tiled_not_ready")
        if reason == "driver_missing":
            probe = _probe_core_cli()
            if probe.verified is None:
                failure_reason = (
                    "core_cli_unavailable"
                    if probe.failure_reason == "not_found"
                    else "core_cli_untrusted"
                )
                return _report(
                    request,
                    status,
                    10,
                    {"stage": "core_cli_preflight", "reason": failure_reason},
                    [],
                    blocker=_core_cli_blocker(probe),
                )
            core_cli = probe.verified
            if core_cli.version != core_version:
                return _report(
                    request,
                    status,
                    10,
                    {"stage": "core_cli_preflight", "reason": "core_cli_version_mismatch"},
                    [],
                    core_cli=core_cli,
                    blocker=_core_cli_blocker(CoreCliProbe(None, "version_mismatch")),
                )
            return _report(
                request,
                status,
                10,
                {"stage": "driver_preflight", "reason": reason},
                [
                    _catalog_plan_step(core_cli),
                    _command_step(
                        "rerun-doctor",
                        "Rerun the read-only Tiled doctor with the exact selected executable.",
                        _exact_retry_command(
                            DoctorRequest("doctor", request.executable, request.install_alias),
                            status,
                        ),
                        "The runtime must be rechecked before it can be reported directly usable.",
                        action="rerun_verify",
                    ),
                ],
                core_cli=core_cli,
            )
        return _report(
            request,
            status,
            10,
            {"stage": "executable_discovery", "reason": reason},
            [],
            blocker=_tiled_blocker(),
        )
    if _version_tuple(status.get("version")) is None:
        return _report(
            request,
            status,
            40,
            {"stage": "version_verification", "reason": "tiled_version_invalid"},
            [],
            blocker=_tiled_blocker(),
        )
    if not _version_at_least(status.get("version"), MIN_TILED_VERSION):
        return _report(
            request,
            status,
            10,
            {"stage": "version_preflight", "reason": "tiled_version_below_floor"},
            [],
            blocker=_tiled_blocker(),
        )
    return _report(request, status, 0, None, [])


def _run_catalog_plan(request: DoctorRequest) -> int:
    probe = _probe_core_cli()
    if probe.verified is None:
        report = _report(
            request,
            {"ready": False},
            10,
            {"stage": "core_cli_preflight", "reason": "core_cli_untrusted"},
            [],
            blocker=_core_cli_blocker(probe),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 10
    core_cli = probe.verified
    if core_cli.version != core_version:
        report = _report(
            request,
            {"ready": False},
            10,
            {"stage": "core_cli_preflight", "reason": "core_cli_version_mismatch"},
            [],
            core_cli=core_cli,
            blocker=_core_cli_blocker(CoreCliProbe(None, "version_mismatch")),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 10
    if _probe_core_cli().verified != core_cli:
        report = _report(
            request,
            {"ready": False},
            10,
            {"stage": "core_cli_preflight", "reason": "core_cli_replaced"},
            [],
            blocker=_core_cli_blocker(CoreCliProbe(None, "replacement_detected")),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 10
    try:
        completed = subprocess.run(
            [
                core_cli.path,
                "install",
                "--dcc-type",
                "tiled",
                "--output",
                "json",
                "--non-interactive",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        plan = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        plan = None
    if not isinstance(plan, dict) or plan.get("dcc_type") != "tiled":
        report = _report(
            request,
            {"ready": False},
            10,
            {"stage": "catalog_resolution", "reason": "catalog_plan_invalid"},
            [],
            core_cli=core_cli,
            blocker={
                "reason": "catalog_plan_unavailable",
                "instructions_url": INSTALL_GUIDE_URL,
            },
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 10
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a read-only Tiled doctor/verify command and return its stable exit code."""
    args = _parser().parse_args(argv)
    request = DoctorRequest(args.operation, args.executable, _install_alias())
    if request.operation == "catalog-plan":
        return _run_catalog_plan(request)
    if request.install_alias:
        print(
            "dcc-mcp-tiled-install is deprecated and performs verification only; "
            "use dcc-mcp-tiled-doctor doctor --json.",
            file=sys.stderr,
        )
    try:
        cli = TiledCli(executable=request.executable) if request.executable else TiledCli.from_env()
    except (TypeError, ValueError):
        report = _configuration_failure(request)
    else:
        report = _evaluate(request, cli)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])
