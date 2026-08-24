# dcc-mcp-tiled

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/dcc-mcp-tiled-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/dcc-mcp-tiled.svg">
    <img src="docs/images/dcc-mcp-tiled.svg" alt="DCC-MCP · TILED" width="600">
  </picture>
</p>

Typed, workspace-bounded level authoring for [Tiled](https://www.mapeditor.org/)
through the DCC Model Context Protocol.

![Tiled game-level workflow](docs/images/tiled-showcase.webp)

_Illustrative workflow generated with OpenAI ImageGen from the retained source in `docs/images/sources`; it is not a Tiled screenshot or host-validation artifact._

The adapter invokes Tiled's official JavaScript scripting API through
`tiled --evaluate`. It ships one fixed driver and accepts typed JSON data only:
clients cannot submit JavaScript or other arbitrary source code. The service is
standalone, so Tiled does not need an in-process Python plug-in or an open editor
window.

## Capabilities

| Typed tool | Purpose |
| --- | --- |
| `get_status` | Verify the executable, Tiled/Qt versions, formats, roots, and runtime limits. |
| `inspect_map` | Summarize maps, layers, objects, tilesets, properties, and bounded tile occupancy. |
| `validate_map` | Parse a map with Tiled and report structural or reference errors. |
| `create_map` | Create orthogonal, isometric, staggered, or hexagonal maps with initial layers. |
| `add_object_layer` | Add a uniquely named object layer and scalar properties. |
| `write_objects` | Append or replace bounded point, rectangle, ellipse, polygon, or polyline objects. |
| `create_tileset` | Create an external TSJ/TSX tileset from an image. |
| `add_tileset` | Attach an external tileset to a map. |
| `paint_tiles` | Paint or clear bounded cells, including Tiled flip flags. |
| `convert_map` | Convert maps through Tiled's registered map formats, including TMJ and TMX. |

All mutating operations write to a same-directory temporary file and atomically
replace the requested output only after Tiled returns a valid, non-empty result.

## Install

Install [Tiled](https://www.mapeditor.org/download.html) 1.10 or newer, then
follow the [agent-first installation guide](install.md). The adapter is not
currently published on PyPI, so `pip install dcc-mcp-tiled` must not be treated
as a working install path. Install a checksum-verified wheel from a trusted
release or reviewed checkout, then run:

```bash
dcc-mcp-tiled-doctor doctor --json
dcc-mcp-tiled-doctor verify --json
dcc-mcp-tiled
```

The deprecated `dcc-mcp-tiled-install` compatibility alias performs
verification only and never writes an installation.

When the fixed driver is unavailable, catalog planning is exposed only through
`dcc-mcp-tiled-doctor catalog-plan --json`. That wrapper authenticates the
exact Core 0.20.14 CLI bytes against Core's signed platform update manifest and
rechecks the path and digest immediately before the read-only plan. Missing or
untrusted executables return an explicit blocker instead of a placeholder or
browser-only remediation.

The adapter discovers `tiled`/`tiled.exe` from `PATH` and common Windows install
locations. Set an explicit executable when needed:

```powershell
$env:DCC_MCP_TILED_EXECUTABLE = "C:\Program Files\Tiled\tiled.exe"
$env:DCC_MCP_TILED_ALLOWED_ROOTS = "D:\game\maps;D:\game\art"
dcc-mcp-tiled
```

On POSIX systems, separate multiple allowed roots with `:` instead of `;`.
Without `DCC_MCP_TILED_ALLOWED_ROOTS`, only the server's current working
directory is accessible.

The MCP endpoint is registered with DCC-MCP Core and is also available directly
from the adapter's loopback HTTP server. Use `dcc-mcp-cli instances` to discover
the active URL and typed capabilities.

## Safety contract

- Source and output paths must remain inside `DCC_MCP_TILED_ALLOWED_ROOTS`.
- Inputs, responses, maps, tile cells, objects, coordinates, properties, and
  execution time all have explicit upper bounds.
- Subprocesses run without a shell, support DCC-MCP cancellation, and are
  terminated on timeout.
- Existing outputs require `overwrite=true`; successful writes are atomic.
- The bundled driver is immutable package code. Tool arguments are JSON data,
  never evaluated source.
- This adapter does not claim interactive GUI control. Visible editor automation
  belongs to a separate, explicitly authorized UI-control workflow.

Runtime ceilings can be reduced with `DCC_MCP_TILED_MAX_MAP_BYTES`,
`DCC_MCP_TILED_MAX_REQUEST_BYTES`, `DCC_MCP_TILED_MAX_RESPONSE_BYTES`,
`DCC_MCP_TILED_MAX_TILE_CELLS`, `DCC_MCP_TILED_MAX_CELLS_PER_CALL`,
`DCC_MCP_TILED_MAX_OBJECTS_PER_CALL`, and
`DCC_MCP_TILED_MAX_TIMEOUT_SECS`.

## Native validation

The release gate runs the official Tiled 1.12.2 AppImage by a pinned SHA256 in a
virtual display. It creates a two-tile image tileset, authors and paints a TMJ
map, writes two gameplay objects, validates it, converts it to TMX, starts the
packaged MCP server, loads the bundled Skill, and completes a typed asynchronous
`get_status` job. The smoke test verifies 10 registered typed tools and never
uses a mock Tiled process.

The adapter does not download Tiled and has no persistent binary cache. The CI
AppImage exists only in the ephemeral runner workspace; operators own upgrades
and cleanup for OS packages or exact, checksum-verified AppImages.

TMJ/TMX outputs are suitable handoff artifacts for downstream Godot, Blender,
and other game-content pipelines. This adapter validates the Tiled artifact; a
target engine or DCC remains responsible for its own importer and scene-level
acceptance.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python tools/lint_skills.py
python -m build
python -m twine check dist/*
```

Architecture details are in [docs/architecture.md](docs/architecture.md).
Official API references:
[scripting](https://doc.mapeditor.org/en/stable/reference/scripting/),
[MapFormat](https://www.mapeditor.org/docs/scripting/classes/MapFormat.html), and
[TilesetFormat](https://www.mapeditor.org/docs/scripting/classes/TilesetFormat.html).
