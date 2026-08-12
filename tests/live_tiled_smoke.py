"""Exercise the real Tiled CLI driver and one typed MCP call."""

from __future__ import annotations

import json
import os
import struct
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

from dcc_mcp_tiled.bridge import TiledCli
from dcc_mcp_tiled.server import TiledMcpServer


def write_tileset_png(path: Path) -> None:
    width, height = 64, 32
    rows = []
    for _y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend((42, 125, 76, 255) if x < 32 else (74, 96, 140, 255))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), level=9))
        + chunk(b"IEND", b"")
    )


def post(url: str, method: str, params=None):
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def call(url: str, name: str, arguments=None):
    response = post(url, "tools/call", {"name": name, "arguments": arguments or {}})
    result = response.get("result", {})
    if response.get("error") or result.get("isError"):
        raise RuntimeError(json.dumps(response))
    structured = result.get("structuredContent")
    if structured is not None:
        envelope = structured
    else:
        envelope = json.loads(result["content"][0]["text"])
    job_id = envelope.get("job_id") if isinstance(envelope, dict) else None
    if not job_id:
        return envelope
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        poll = post(
            url,
            "tools/call",
            {
                "name": "jobs_get_status",
                "arguments": {"job_id": job_id, "include_result": True},
            },
        )
        poll_result = poll.get("result", {})
        if poll.get("error") or poll_result.get("isError"):
            raise RuntimeError(json.dumps(poll))
        status = poll_result.get("structuredContent")
        if status is None:
            status = json.loads(poll_result["content"][0]["text"])
        if status.get("status") == "completed":
            return status["result"]
        if status.get("status") in {"failed", "cancelled", "interrupted"}:
            raise RuntimeError(json.dumps(status))
        time.sleep(1.0)
    raise TimeoutError("MCP job %s did not complete within 120 seconds" % job_id)


def list_tool_names(url: str) -> set[str]:
    names = set()
    cursor = None
    for _page in range(20):
        response = post(url, "tools/list", {"cursor": cursor} if cursor else None)
        if response.get("error"):
            raise RuntimeError(json.dumps(response))
        result = response.get("result", {})
        names.update(item["name"] for item in result.get("tools", []))
        cursor = result.get("nextCursor")
        if not cursor:
            return names
    raise RuntimeError("MCP tools/list exceeded the 20-page smoke-test budget")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dcc-mcp-tiled-live-") as temp_dir:
        root = Path(temp_dir).resolve()
        os.environ["DCC_MCP_TILED_ALLOWED_ROOTS"] = str(root)
        os.environ["DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS"] = "1"
        cli = TiledCli.from_env()
        status = cli.status()
        assert status["ready"]

        base = root / "level.tmj"
        tileset_image = root / "terrain.png"
        tileset = root / "terrain.tsj"
        with_tileset = root / "level-tileset.tmj"
        painted = root / "level-painted.tmj"
        authored = root / "level-authored.tmj"
        converted = root / "level-authored.tmx"
        write_tileset_png(tileset_image)
        created_tileset = cli.create_tileset(str(tileset_image), str(tileset), "Terrain", 32, 32)
        assert created_tileset["tileset"]["tile_count"] == 2
        cli.create_map(
            str(base),
            16,
            10,
            layers=[{"type": "tile", "name": "Ground"}, {"type": "object", "name": "Gameplay"}],
            properties={"pipeline": "dcc-mcp"},
        )
        cli.add_tileset(str(base), str(tileset), str(with_tileset))
        cli.paint_tiles(
            str(with_tileset),
            str(painted),
            "Ground",
            [
                {"x": 0, "y": 0, "tile_id": 0},
                {"x": 1, "y": 0, "tile_id": 1, "flip_horizontal": True},
            ],
        )
        cli.write_objects(
            str(painted),
            str(authored),
            "Gameplay",
            [
                {
                    "name": "PlayerSpawn",
                    "class_name": "SpawnPoint",
                    "shape": "point",
                    "x": 160,
                    "y": 96,
                    "properties": {"team": "player"},
                },
                {
                    "name": "ExitZone",
                    "class_name": "Trigger",
                    "shape": "rectangle",
                    "x": 384,
                    "y": 224,
                    "width": 64,
                    "height": 64,
                },
            ],
        )
        inspected = cli.inspect_map(str(authored))
        assert inspected["width"] == 16
        assert inspected["height"] == 10
        gameplay = next(layer for layer in inspected["layers"] if layer["name"] == "Gameplay")
        ground = next(layer for layer in inspected["layers"] if layer["name"] == "Ground")
        assert gameplay["object_count"] == 2
        assert {item["name"] for item in gameplay["objects"]} == {"PlayerSpawn", "ExitZone"}
        assert inspected["tileset_count"] == 1
        assert inspected["tilesets"][0]["tile_count"] == 2
        assert ground["non_empty_tiles_scanned"] == 2
        assert cli.validate_map(str(authored))["valid"]
        cli.convert_map(str(authored), str(converted))
        assert cli.validate_map(str(converted))["valid"]

        server = TiledMcpServer(port=0, registry_dir=str(root / "registry"))
        try:
            server.register_builtin_actions()
            server.start(install_atexit_hook=False)
            call(server.mcp_url, "load_skill", {"skill_name": "tiled-maps"})
            names = list_tool_names(server.mcp_url)
            status_tool = next(
                name for name in names if name == "get_status" or name.endswith("__get_status")
            )
            typed_status = call(server.mcp_url, status_tool)
            assert typed_status["success"] is True
            assert typed_status["context"]["ready"] is True
        finally:
            server.stop()

        print(
            json.dumps(
                {
                    "tiled_version": status["version"],
                    "map": str(authored),
                    "converted": str(converted),
                    "objects": gameplay["object_count"],
                    "painted_tiles": ground["non_empty_tiles_scanned"],
                    "tilesets": inspected["tileset_count"],
                }
            )
        )


if __name__ == "__main__":
    main()
