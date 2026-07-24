# dcc-mcp-tiled

![DCC-MCP Tiled](docs/images/dcc-mcp-tiled.svg)

Tiled adapter for the DCC Model Context Protocol ecosystem.

![Tiled game-level workflow](docs/images/dcc-mcp-tiled-showcase.webp)

The adapter uses a process-isolated loopback JSON-lines bridge contract for map
and object automation. It does not expose arbitrary source evaluation.

## Install

```bash
pip install dcc-mcp-tiled
dcc-mcp-tiled-install
```

Configure the Tiled bridge endpoint, then start:

```bash
dcc-mcp-tiled
```

The MCP endpoint defaults to `http://127.0.0.1:8767/mcp`; the plug-in bridge uses
`127.0.0.1:3848`. Override the latter with `DCC_MCP_TILED_BRIDGE_PORT` before
starting both processes.

## Current tools

- Check TILED bridge status and version.
- List open images with dimensions.
- Inspect the active image.

The first release targets safe session discovery. Image mutation and export will
be added only through typed TILED procedures, not arbitrary source evaluation.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests tools
python tools/lint_skills.py
python -m build
python -m twine check dist/*
```

Tiled plug-in API reference: https://developer.tiled.org/api/3.0/
