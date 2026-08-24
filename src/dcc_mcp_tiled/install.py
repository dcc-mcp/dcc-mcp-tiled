"""Thin standalone verification compatibility layer pending Core #2252/#2320."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from dcc_mcp_core import __version__ as core_version

from .__version__ import __version__
from .bridge import TiledCli, TiledError, TiledLaunchError, TiledTimeoutError

MIN_CORE_VERSION = "0.19.38"
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the official Tiled CLI runtime; no host plug-in is installed."
    )
    parser.add_argument("operation", nargs="?", choices=("doctor", "verify"), default="doctor")
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


def _select_tiled_step(reason: str) -> dict[str, Any]:
    return _command_step(
        "select-tiled-executable",
        "Install Tiled 1.10.0 or newer and verify its exact executable.",
        [
            "dcc-mcp-tiled-doctor",
            "verify",
            "--json",
            "--executable",
            "<absolute-tiled-executable>",
        ],
        reason,
        action="install_tiled",
        minimum_version=MIN_TILED_VERSION,
        instructions_url=INSTALL_GUIDE_URL,
    )


def _report(
    request: DoctorRequest,
    status: dict[str, Any],
    exit_code: int,
    failure: Optional[dict[str, str]],
    next_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    status = dict(status)
    if "version" in status:
        status["version"] = _bounded_version_text(status["version"])
    reported_core_version = _bounded_version_text(core_version)
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
        "schema_version": 1,
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
            "contract": "adapter_compatibility_pending_core_2252_2320",
        },
        "compatibility": {
            "deprecated_install_alias": request.install_alias,
            "mode": "verify_only",
            "writes_performed": False,
            "replacement_command": ["dcc-mcp-tiled-doctor", "doctor", "--json"],
        },
        "requirements": {
            "min_core_version": MIN_CORE_VERSION,
            "min_tiled_version": MIN_TILED_VERSION,
            "core_version": reported_core_version,
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
                    ["dcc-mcp-tiled-doctor", "verify", "--json"],
                    "The bounded Tiled runtime probe failed.",
                    action="rerun_verify",
                )
            ],
        )
    if not status.get("ready"):
        reason = str(status.get("reason") or "tiled_not_ready")
        stage = "driver_preflight" if reason == "driver_missing" else "executable_discovery"
        next_step = (
            _command_step(
                "resolve-adapter-artifact",
                "Resolve the current Tiled adapter install plan without executing it.",
                [
                    "dcc-mcp-cli",
                    "install",
                    "--dcc-type",
                    "tiled",
                    "--output",
                    "json",
                    "--non-interactive",
                ],
                "The bundled fixed Tiled driver is missing, and no immutable wheel is available "
                "from this report.",
                action="resolve_adapter_artifact",
                executes_install=False,
                instructions_url=INSTALL_GUIDE_URL,
                requires_verified_wheel=True,
            )
            if reason == "driver_missing"
            else _select_tiled_step("Tiled was not discovered at a supported executable path.")
        )
        return _report(
            request,
            status,
            10,
            {"stage": stage, "reason": reason},
            [
                next_step,
                _command_step(
                    "rerun-doctor",
                    "Rerun the read-only Tiled doctor after applying the remediation.",
                    ["dcc-mcp-tiled-doctor", "doctor", "--json"],
                    "The runtime must be rechecked before it can be reported directly usable.",
                    action="rerun_verify",
                ),
            ],
        )
    if _version_tuple(status.get("version")) is None:
        return _report(
            request,
            status,
            40,
            {"stage": "version_verification", "reason": "tiled_version_invalid"},
            [
                _select_tiled_step(
                    "The Tiled runtime did not report a bounded canonical final release."
                )
            ],
        )
    if not _version_at_least(status.get("version"), MIN_TILED_VERSION):
        return _report(
            request,
            status,
            10,
            {"stage": "version_preflight", "reason": "tiled_version_below_floor"},
            [_select_tiled_step("The discovered Tiled release is below the supported floor.")],
        )
    return _report(request, status, 0, None, [])


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a read-only Tiled doctor/verify command and return its stable exit code."""
    args = _parser().parse_args(argv)
    request = DoctorRequest(args.operation, args.executable, _install_alias())
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
