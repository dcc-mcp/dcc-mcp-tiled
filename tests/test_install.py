"""Public CLI contract tests for the standalone Tiled runtime verifier."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dcc_mcp_tiled import install
from dcc_mcp_tiled.bridge import TiledCli, TiledTimeoutError


def install_status_runner(monkeypatch, version: str, qt_version: str) -> None:
    """Emulate only the external Tiled response while exercising the real driver envelope."""

    def fake_run(self, args, _timeout_secs):
        Path(args[-1]).write_text(
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "version": version,
                        "qt_version": qt_version,
                        "platform": "windows",
                        "arch": "x86_64",
                        "map_formats": ["tmj", "tmx"],
                        "tileset_formats": ["tsj", "tsx"],
                        "cli_evaluate": True,
                        "arbitrary_script_input": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "returncode": 0,
            "duration_secs": 0.01,
            "stdout": "",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    monkeypatch.setattr(TiledCli, "_run", fake_run)


def test_doctor_reports_missing_tiled_as_preflight_json(tmp_path, capsys) -> None:
    missing = tmp_path / "missing-tiled"

    exit_code = install.main(["doctor", "--json", "--executable", str(missing)])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 10
    assert report["schema_version"] == "1.0"
    assert report["operation"] == "doctor"
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "executable_discovery",
        "reason": "tiled_not_found",
    }
    assert report["next_steps"]


def test_verify_reports_runtime_versions_configuration_and_floors(
    tmp_path, monkeypatch, capsys
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    install_status_runner(monkeypatch, "1.12.2", "6.8.3")

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["exit_code"] == 0
    assert report["operation"] == "verify"
    assert report["directly_usable"] is True
    assert report["failure"] is None
    assert report["requirements"]["min_core_version"] == "0.19.38"
    assert report["requirements"]["min_tiled_version"] == "1.10.0"
    assert report["runtime"]["version"] == "1.12.2"
    assert report["runtime"]["qt_version"] == "6.8.3"
    assert report["configuration"]["allowed_roots"]
    assert report["discovery"]["executable"] == str(executable.resolve())
    assert report["provisioning"] == {
        "auto_provision": False,
        "binary_source": "operating_system",
        "persistent_adapter_cache": None,
    }
    assert report["next_steps"] == []


def test_verify_rejects_tiled_below_the_supported_floor(tmp_path, monkeypatch, capsys) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    install_status_runner(monkeypatch, "1.9.2", "5.15.2")

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 10
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "version_preflight",
        "reason": "tiled_version_below_floor",
    }
    assert report["runtime"]["version"] == "1.9.2"
    assert report["next_steps"][0]["action"] == "install_tiled"


def test_doctor_rejects_core_below_the_supported_floor(tmp_path, monkeypatch, capsys) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(install, "core_version", "0.19.1")

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 10
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "core_preflight",
        "reason": "core_version_below_floor",
    }
    assert report["next_steps"][0]["action"] == "upgrade_core"


def test_verify_maps_runtime_timeout_to_stable_exit_40(tmp_path, monkeypatch, capsys) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")

    def fake_run(self, _args, _timeout_secs):
        raise TiledTimeoutError("Tiled exceeded the configured timeout")

    monkeypatch.setattr(TiledCli, "_run", fake_run)

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 40
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "runtime_verification",
        "reason": "tiled_timeout",
    }
    assert report["next_steps"][0]["action"] == "rerun_verify"


def test_install_alias_is_explicitly_deprecated_and_never_reports_a_write(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(sys, "argv", ["dcc-mcp-tiled-install"])
    missing = tmp_path / "missing-tiled"

    exit_code = install.main(["--json", "--executable", str(missing)])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 10
    assert report["compatibility"] == {
        "deprecated_install_alias": True,
        "mode": "verify_only",
        "writes_performed": False,
        "replacement_command": ["dcc-mcp-tiled-doctor", "doctor", "--json"],
    }
    assert "deprecated" in captured.err.lower()


def test_doctor_reports_invalid_environment_as_configuration_preflight(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DCC_MCP_TILED_MAX_TIMEOUT_SECS", "not-a-number")

    exit_code = install.main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 10
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "configuration_preflight",
        "reason": "invalid_environment",
    }
    assert report["next_steps"][0]["action"] == "fix_environment"
