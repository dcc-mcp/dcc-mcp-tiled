"""Public CLI contract tests for the standalone Tiled runtime verifier."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from dcc_mcp_core.deployment import INSTALL_SOP_SCHEMA_VERSION, load_install_sop_schema
from jsonschema import Draft202012Validator

from dcc_mcp_tiled import install
from dcc_mcp_tiled.bridge import TiledCli, TiledTimeoutError

ROOT = Path(__file__).parents[1]
INSTALL_SOP_SCHEMA = load_install_sop_schema()
INSTALL_SOP_VALIDATOR = Draft202012Validator(INSTALL_SOP_SCHEMA)
Draft202012Validator.check_schema(INSTALL_SOP_SCHEMA)


def test_install_reports_consume_the_published_core_schema_resource() -> None:
    schema_resource = files("dcc_mcp_core").joinpath("schemas/adapter-install-sop-v1.schema.json")
    schema_bytes = schema_resource.read_bytes()

    assert not (ROOT / "tests/fixtures/adapter-install-sop-v1.schema.json").exists()
    assert INSTALL_SOP_SCHEMA_VERSION == 1
    assert len(schema_bytes) == 4261
    assert hashlib.sha256(schema_bytes).hexdigest() == (
        "3ca25788439917b4d4c0617230a762f9797756b5b54f45c8c4149f975b90f904"
    )
    assert b"\r\n" not in schema_bytes
    assert load_install_sop_schema() == json.loads(schema_bytes)


@pytest.fixture(autouse=True)
def coherent_core_cli(monkeypatch) -> None:
    """Keep ordinary tests focused while the public mismatch cases override this probe."""

    monkeypatch.setattr(install, "core_version", install.MIN_CORE_VERSION)


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
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["schema_version"] == 1
    assert report["legacy_schema_version"] == "1.0"
    assert report["legacy_status"] == "not_ready"
    assert report["adapter"]["contract"] == "core_install_sop_v1_verify_only"
    assert report["operation"] == "doctor"
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "executable_discovery",
        "reason": "tiled_not_found",
    }
    assert report["next_steps"]


def test_doctor_driver_missing_uses_read_only_catalog_resolution(
    tmp_path, monkeypatch, capsys
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(
        TiledCli,
        "_default_driver_path",
        staticmethod(lambda: tmp_path / "missing-driver.js"),
    )
    monkeypatch.setattr(install, "_probe_core_cli_version", lambda: install.core_version)

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["failure"] == {
        "stage": "driver_preflight",
        "reason": "driver_missing",
    }
    remediation = report["next_steps"][0]
    assert remediation["command"] == [
        "dcc-mcp-cli",
        "install",
        "--dcc-type",
        "tiled",
        "--output",
        "json",
        "--non-interactive",
    ]
    assert "--execute" not in remediation["command"]
    assert "dcc-mcp-tiled==" not in json.dumps(remediation)


def test_driver_missing_fails_closed_when_path_cli_does_not_match_python_core(
    tmp_path, monkeypatch, capsys
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(install, "core_version", "0.20.14")
    monkeypatch.setattr(install, "_probe_core_cli_version", lambda: "0.20.15")
    monkeypatch.setattr(
        TiledCli,
        "_default_driver_path",
        staticmethod(lambda: tmp_path / "missing-driver.js"),
    )

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["failure"] == {
        "stage": "core_cli_preflight",
        "reason": "core_cli_version_mismatch",
    }
    assert report["requirements"]["core_version"] == "0.20.14"
    assert report["requirements"]["core_cli_version"] == "0.20.15"
    assert report["next_steps"][0]["id"] == "install-matching-core-cli"
    assert report["next_steps"][0]["command"] == [
        sys.executable,
        "-m",
        "webbrowser",
        "https://github.com/dcc-mcp/dcc-mcp-core/releases/tag/v0.20.14",
    ]
    assert "dcc-mcp-cli" not in report["next_steps"][0]["command"]


def test_driver_missing_fails_closed_when_path_cli_is_unavailable(
    tmp_path, monkeypatch, capsys
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(install, "_probe_core_cli_version", lambda: None)
    monkeypatch.setattr(
        TiledCli,
        "_default_driver_path",
        staticmethod(lambda: tmp_path / "missing-driver.js"),
    )

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["failure"] == {
        "stage": "core_cli_preflight",
        "reason": "core_cli_unavailable",
    }
    assert report["requirements"]["core_cli_version"] is None
    assert report["requirements"]["core_cli_matches_python"] is None
    assert report["next_steps"][0]["id"] == "install-matching-core-cli"


def test_declared_core_floor_cli_executes_the_emitted_catalog_plan() -> None:
    cli_text = os.environ.get("DCC_MCP_TILED_CORE_FLOOR_CLI")
    if not cli_text:
        pytest.skip("set DCC_MCP_TILED_CORE_FLOOR_CLI to the official floor binary")
    cli = Path(cli_text)

    version = subprocess.run(
        [str(cli), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert version.returncode == 0
    assert version.stdout.strip() == "dcc-mcp-cli %s" % install.MIN_CORE_VERSION

    completed = subprocess.run(
        [
            str(cli),
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
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["dcc_type"] == "tiled"
    assert plan["adapter"]["name"] == "dcc-mcp-tiled"
    assert [step["name"] for step in plan["steps"]] == [
        "resolve-adapter",
        "install-runtime",
        "install-dcc-adapter",
        "verify",
    ]
    assert plan["next_steps"][0]["name"] == "read-install-instructions"
    assert plan["next_steps"][0]["url"] == install.INSTALL_GUIDE_URL


def test_driver_missing_binds_the_official_floor_cli_to_python_core(
    tmp_path, monkeypatch, capsys
) -> None:
    cli_text = os.environ.get("DCC_MCP_TILED_CORE_FLOOR_CLI")
    if not cli_text:
        pytest.skip("set DCC_MCP_TILED_CORE_FLOOR_CLI to the official floor binary")
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(install.shutil, "which", lambda _name: cli_text)
    monkeypatch.setattr(
        TiledCli,
        "_default_driver_path",
        staticmethod(lambda: tmp_path / "missing-driver.js"),
    )

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["failure"] == {
        "stage": "driver_preflight",
        "reason": "driver_missing",
    }
    assert report["requirements"]["core_version"] == "0.20.14"
    assert report["requirements"]["core_cli_version"] == "0.20.14"
    assert report["requirements"]["core_cli_matches_python"] is True
    assert report["next_steps"][0]["command"] == [
        "dcc-mcp-cli",
        "install",
        "--dcc-type",
        "tiled",
        "--output",
        "json",
        "--non-interactive",
    ]


def test_core_cli_floor_is_projected_into_package_docs_and_ci() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    install_guide = (ROOT / "install.md").read_text(encoding="utf-8")
    skill = (ROOT / "src" / "dcc_mcp_tiled" / "skills" / "tiled-maps" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    floor = install.MIN_CORE_VERSION

    assert floor == "0.20.14"
    assert 'dependencies = ["dcc-mcp-core>=%s,<1.0.0"]' % floor in pyproject
    assert "DCC-MCP Core %s or newer" % floor in install_guide
    assert "dcc-mcp-core/CLI >=%s" % floor in skill
    assert 'CORE_CLI_VERSION: "%s"' % floor in workflow
    assert "dcc-mcp-core==${CORE_CLI_VERSION}" in workflow
    assert "test_declared_core_floor_cli_executes_the_emitted_catalog_plan" in workflow


def test_verify_reports_runtime_versions_configuration_and_floors(
    tmp_path, monkeypatch, capsys
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    install_status_runner(monkeypatch, "1.12.2", "6.8.3")

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["legacy_status"] == "ok"
    assert report["exit_code"] == 0
    assert report["operation"] == "verify"
    assert report["directly_usable"] is True
    assert report["failure"] is None
    assert report["requirements"]["min_core_version"] == "0.20.14"
    assert report["requirements"]["min_core_cli_version"] == "0.20.14"
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
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "version_preflight",
        "reason": "tiled_version_below_floor",
    }
    assert report["runtime"]["version"] == "1.9.2"
    assert report["next_steps"][0]["id"] == "select-tiled-executable"
    assert report["next_steps"][0]["action"] == "install_tiled"


@pytest.mark.parametrize(
    "runtime_version",
    [
        pytest.param("1.12.2-rc1", id="prerelease"),
        pytest.param("Tiled 1.12.2", id="prefix"),
        pytest.param("1.12.2 build", id="suffix"),
        pytest.param(" 1.12.2 ", id="whitespace"),
        pytest.param("1.012.2", id="zero-padded"),
        pytest.param("1234567.12.2", id="component-too-long"),
        pytest.param("9" * 10_000 + ".12.2", id="bounded-before-int"),
    ],
)
def test_verify_rejects_non_final_tiled_version_as_runtime_failure(
    tmp_path, monkeypatch, capsys, runtime_version
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    install_status_runner(monkeypatch, runtime_version, "6.8.3")

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 40
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "version_verification",
        "failure_reason": "tiled_version_invalid",
    }
    if len(runtime_version) > 32:
        assert report["runtime"]["version"] == "invalid"


def test_doctor_rejects_core_below_the_supported_floor(tmp_path, monkeypatch, capsys) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(install, "core_version", "0.19.1")

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "core_preflight",
        "reason": "core_version_below_floor",
    }
    assert report["next_steps"][0]["id"] == "upgrade-core"
    assert report["next_steps"][0]["action"] == "upgrade_core"


@pytest.mark.parametrize(
    "runtime_version",
    [
        pytest.param("0.20.12rc1", id="prerelease"),
        pytest.param("dcc-mcp-core 0.20.12", id="prefix"),
        pytest.param("0.20.12+local", id="suffix"),
        pytest.param(" 0.20.12 ", id="whitespace"),
        pytest.param("0.020.12", id="zero-padded"),
        pytest.param("1234567.20.12", id="component-too-long"),
        pytest.param("9" * 10_000 + ".20.12", id="bounded-before-int"),
    ],
)
def test_doctor_rejects_non_final_core_version_before_launch(
    tmp_path, monkeypatch, capsys, runtime_version
) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(install, "core_version", runtime_version)

    exit_code = install.main(["doctor", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["verify"]["failure_stage"] == "core_preflight"
    assert report["verify"]["failure_reason"] == "core_version_invalid"
    if len(runtime_version) > 32:
        assert report["core_version"] == "invalid"


def test_verify_maps_runtime_timeout_to_stable_exit_40(tmp_path, monkeypatch, capsys) -> None:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")

    def fake_run(self, _args, _timeout_secs):
        raise TiledTimeoutError("Tiled exceeded the configured timeout")

    monkeypatch.setattr(TiledCli, "_run", fake_run)

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    report = json.loads(capsys.readouterr().out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 40
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "runtime_verification",
        "reason": "tiled_timeout",
    }
    assert report["next_steps"][0]["id"] == "retry-runtime-verification"
    assert report["next_steps"][0]["action"] == "rerun_verify"


@pytest.mark.parametrize(
    "launch_error",
    [
        pytest.param(PermissionError("sensitive launch detail"), id="permission"),
        pytest.param(OSError("sensitive launch detail"), id="os-error"),
    ],
)
def test_verify_maps_unlaunchable_executable_to_stable_json(
    tmp_path, monkeypatch, capsys, launch_error
) -> None:
    executable = tmp_path / "tiled.exe"
    executable.write_text("not an executable", encoding="utf-8")

    def fail_launch(*_args, **_kwargs):
        raise launch_error

    monkeypatch.setattr("dcc_mcp_tiled.bridge.subprocess.Popen", fail_launch)

    exit_code = install.main(["verify", "--json", "--executable", str(executable)])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 40
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "runtime_verification",
        "failure_reason": "tiled_launch_failed",
    }
    assert "traceback" not in captured.err.lower()
    assert "sensitive launch detail" not in captured.out + captured.err


def test_public_cli_fails_closed_for_a_non_executable_tiled_file(tmp_path) -> None:
    executable = tmp_path / ("tiled.exe" if os.name == "nt" else "tiled")
    executable.write_text("not an executable", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from dcc_mcp_tiled import install; "
                "install.core_version = install.MIN_CORE_VERSION; "
                "raise SystemExit(install.main())"
            ),
            "verify",
            "--json",
            "--executable",
            str(executable),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads(completed.stdout)
    INSTALL_SOP_VALIDATOR.validate(report)
    assert completed.returncode == 40
    assert report["verify"]["failure_reason"] == "tiled_launch_failed"
    assert "traceback" not in completed.stderr.lower()


def test_install_alias_is_explicitly_deprecated_and_never_reports_a_write(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(sys, "argv", ["dcc-mcp-tiled-install"])
    missing = tmp_path / "missing-tiled"

    exit_code = install.main(["--json", "--executable", str(missing)])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    INSTALL_SOP_VALIDATOR.validate(report)
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
    INSTALL_SOP_VALIDATOR.validate(report)
    assert exit_code == 10
    assert report["directly_usable"] is False
    assert report["failure"] == {
        "stage": "configuration_preflight",
        "reason": "invalid_environment",
    }
    assert report["next_steps"][0]["id"] == "fix-environment"
    assert report["next_steps"][0]["action"] == "fix_environment"
