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
from .bridge import TiledCli, TiledError, TiledTimeoutError

MIN_CORE_VERSION = "0.19.38"
MIN_TILED_VERSION = "1.10.0"
INSTALL_GUIDE_URL = "https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-tiled/main/install.md"


@dataclass(frozen=True)
class DoctorRequest:
    """Arguments needed to evaluate one read-only runtime verification."""

    operation: str
    executable: Optional[str]
    install_alias: bool


def _version_tuple(value: object) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", str(value or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _version_at_least(value: object, minimum: str) -> bool:
    current = _version_tuple(value)
    floor = _version_tuple(minimum)
    width = max(len(current), len(floor))
    return bool(current) and current + (0,) * (width - len(current)) >= floor + (0,) * (
        width - len(floor)
    )


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


def _report(
    request: DoctorRequest,
    status: dict[str, Any],
    exit_code: int,
    failure: Optional[dict[str, str]],
    next_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    if request.executable:
        discovery_source = "explicit_argument"
    elif os.environ.get("DCC_MCP_TILED_EXECUTABLE"):
        discovery_source = "environment"
    elif status.get("executable"):
        discovery_source = "path_or_common_location"
    else:
        discovery_source = "not_found"
    return {
        "schema_version": "1.0",
        "operation": request.operation,
        "status": "ok" if exit_code == 0 else "error" if exit_code == 40 else "not_ready",
        "exit_code": exit_code,
        "directly_usable": exit_code == 0,
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
            "core_version": core_version,
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
            {
                "action": "fix_environment",
                "description": "Correct DCC_MCP_TILED_* values and rerun doctor --json.",
                "command": ["dcc-mcp-tiled-doctor", "doctor", "--json"],
            }
        ],
    )


def _evaluate(request: DoctorRequest, cli: TiledCli) -> dict[str, Any]:
    if not _version_at_least(core_version, MIN_CORE_VERSION):
        return _report(
            request,
            _status_snapshot(cli),
            10,
            {"stage": "core_preflight", "reason": "core_version_below_floor"},
            [
                {
                    "action": "upgrade_core",
                    "command": [
                        "python",
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "dcc-mcp-core>=%s" % MIN_CORE_VERSION,
                    ],
                    "minimum_version": MIN_CORE_VERSION,
                }
            ],
        )
    try:
        status = cli.status()
    except TiledError as exc:
        reason = "tiled_timeout" if isinstance(exc, TiledTimeoutError) else "tiled_runtime_error"
        return _report(
            request,
            _status_snapshot(cli),
            40,
            {"stage": "runtime_verification", "reason": reason},
            [
                {
                    "action": "rerun_verify",
                    "command": ["dcc-mcp-tiled-doctor", "verify", "--json"],
                    "description": "Check the Tiled launch environment, then retry.",
                }
            ],
        )
    if not status.get("ready"):
        reason = str(status.get("reason") or "tiled_not_ready")
        stage = "driver_preflight" if reason == "driver_missing" else "executable_discovery"
        return _report(
            request,
            status,
            10,
            {"stage": stage, "reason": reason},
            [
                {
                    "action": "install_tiled",
                    "minimum_version": MIN_TILED_VERSION,
                    "instructions_url": INSTALL_GUIDE_URL,
                },
                {
                    "action": "rerun_verify",
                    "command": ["dcc-mcp-tiled-doctor", "doctor", "--json"],
                },
            ],
        )
    if not _version_at_least(status.get("version"), MIN_TILED_VERSION):
        return _report(
            request,
            status,
            10,
            {"stage": "version_preflight", "reason": "tiled_version_below_floor"},
            [
                {
                    "action": "install_tiled",
                    "minimum_version": MIN_TILED_VERSION,
                    "instructions_url": INSTALL_GUIDE_URL,
                }
            ],
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
