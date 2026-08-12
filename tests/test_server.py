from pathlib import Path

import dcc_mcp_tiled
from dcc_mcp_tiled.server import TiledMcpServer


def test_server_is_standalone_and_bundles_typed_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    server = TiledMcpServer(port=0, registry_dir=str(tmp_path / "registry"))
    assert server._options.instance_type == "standalone"
    skill_file = Path(dcc_mcp_tiled.__file__).parent / "skills" / "tiled-maps" / "SKILL.md"
    assert skill_file.is_file()
