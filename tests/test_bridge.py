from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcc_mcp_tiled.bridge import TiledCli, TiledError


def make_cli(tmp_path: Path) -> TiledCli:
    executable = tmp_path / "tiled"
    executable.write_text("placeholder", encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text("// placeholder", encoding="utf-8")
    return TiledCli(
        executable=str(executable),
        allowed_roots=[tmp_path],
        driver_path=driver,
        max_tile_cells=100,
        max_cells_per_call=3,
        max_objects_per_call=3,
    )


def install_fake_runner(monkeypatch: pytest.MonkeyPatch, cli: TiledCli, result=None):
    requests = []

    def fake_run(args, _timeout_secs):
        request_path = Path(args[-2])
        response_path = Path(args[-1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        requests.append(request)
        payload = request["payload"]
        if "output_path" in payload:
            Path(payload["output_path"]).write_text('{"type":"map"}', encoding="utf-8")
        response_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "result": result
                    or {
                        "version": "1.12.2",
                        "map_formats": ["tmx", "json"],
                        "tileset_formats": ["tsx", "json"],
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

    monkeypatch.setattr(cli, "_run", fake_run)
    return requests


def test_status_uses_fixed_driver_and_reports_limits(tmp_path, monkeypatch):
    cli = make_cli(tmp_path)
    requests = install_fake_runner(monkeypatch, cli)

    status = cli.status()

    assert status["ready"] is True
    assert status["version"] == "1.12.2"
    assert status["driver"] == "official_tiled_cli_script_api"
    assert status["limits"]["max_cells_per_call"] == 3
    assert requests == [{"protocol_version": 1, "operation": "status", "payload": {}}]


def test_status_without_tiled_is_explicit(tmp_path):
    cli = TiledCli(executable=str(tmp_path / "missing"), allowed_roots=[tmp_path])
    status = cli.status()
    assert status["ready"] is False
    assert status["reason"] == "tiled_not_found"


def test_create_map_writes_atomically_and_preserves_destination_name(tmp_path, monkeypatch):
    cli = make_cli(tmp_path)
    requests = install_fake_runner(monkeypatch, cli, {"created": True})
    output = tmp_path / "level.tmj"

    result = cli.create_map(
        str(output),
        8,
        6,
        layers=[{"type": "tile", "name": "Ground"}, {"type": "object", "name": "Gameplay"}],
    )

    assert output.read_text(encoding="utf-8") == '{"type":"map"}'
    assert result["output_path"] == str(output.resolve())
    assert result["bytes"] > 0
    assert len(result["sha256"]) == 64
    payload = requests[0]["payload"]
    assert payload["width"] == 8
    assert payload["height"] == 6
    assert payload["output_path"].endswith(".tmj")
    assert payload["output_path"] != str(output.resolve())


def test_create_map_refuses_overwrite_by_default(tmp_path, monkeypatch):
    cli = make_cli(tmp_path)
    install_fake_runner(monkeypatch, cli)
    output = tmp_path / "level.tmj"
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(TiledError, match="overwrite=true"):
        cli.create_map(str(output), 4, 4)

    assert output.read_text(encoding="utf-8") == "existing"


def test_create_map_enforces_total_tile_limit(tmp_path):
    cli = make_cli(tmp_path)
    with pytest.raises(TiledError, match="tile-cell limit"):
        cli.create_map(str(tmp_path / "large.tmj"), 11, 10)


def test_inspect_map_rejects_paths_outside_allowed_roots(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.tmj"
    outside.write_text("{}", encoding="utf-8")
    cli = TiledCli(allowed_roots=[root])

    with pytest.raises(TiledError, match="outside DCC_MCP_TILED_ALLOWED_ROOTS"):
        cli.inspect_map(str(outside))


def test_inspect_map_passes_only_bounded_data(tmp_path, monkeypatch):
    cli = make_cli(tmp_path)
    requests = install_fake_runner(monkeypatch, cli, {"layer_count": 2})
    source = tmp_path / "level.tmj"
    source.write_text("{}", encoding="utf-8")

    result = cli.inspect_map(str(source), max_objects=2, max_tiles_to_scan=80)

    assert result["layer_count"] == 2
    assert requests[0]["operation"] == "inspect_map"
    assert requests[0]["payload"]["max_objects"] == 2
    assert requests[0]["payload"]["max_tiles_to_scan"] == 80


def test_write_objects_normalizes_shapes_and_scalar_properties(tmp_path, monkeypatch):
    cli = make_cli(tmp_path)
    requests = install_fake_runner(monkeypatch, cli, {"written": 1})
    source = tmp_path / "source.tmj"
    source.write_text("{}", encoding="utf-8")

    cli.write_objects(
        str(source),
        str(tmp_path / "objects.tmj"),
        "Gameplay",
        [
            {
                "name": "Patrol",
                "shape": "polyline",
                "points": [{"x": 0, "y": 0}, {"x": 64, "y": 32}],
                "properties": {"speed": 2.5, "enabled": True},
            }
        ],
    )

    item = requests[0]["payload"]["objects"][0]
    assert item["shape"] == "polyline"
    assert item["points"][1] == {"x": 64.0, "y": 32.0}
    assert item["properties"] == {"speed": 2.5, "enabled": True}


def test_write_objects_rejects_nested_properties(tmp_path):
    cli = make_cli(tmp_path)
    source = tmp_path / "source.tmj"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(TiledError, match="must be null, boolean, number, or string"):
        cli.write_objects(
            str(source),
            str(tmp_path / "objects.tmj"),
            "Gameplay",
            [{"properties": {"unsafe": {"nested": True}}}],
        )


def test_paint_tiles_enforces_per_call_limit(tmp_path):
    cli = make_cli(tmp_path)
    source = tmp_path / "source.tmj"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(TiledError, match="per-call limit"):
        cli.paint_tiles(
            str(source),
            str(tmp_path / "painted.tmj"),
            "Ground",
            [{"x": index, "y": 0, "tile_id": 0} for index in range(4)],
        )


def test_driver_error_is_propagated_without_fallback(tmp_path, monkeypatch):
    cli = make_cli(tmp_path)

    def fake_run(args, _timeout_secs):
        Path(args[-1]).write_text(
            json.dumps({"ok": False, "error": "Layer not found: Ground"}),
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

    monkeypatch.setattr(cli, "_run", fake_run)
    source = tmp_path / "source.tmj"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(TiledError, match="Layer not found: Ground"):
        cli.inspect_map(str(source), max_objects=3, max_tiles_to_scan=100)


def test_bundled_driver_exists():
    cli = TiledCli()
    assert cli.driver_path.name in {"driver.js", "dcc_mcp_tiled_driver.js"}
    assert cli.driver_path.is_file()
