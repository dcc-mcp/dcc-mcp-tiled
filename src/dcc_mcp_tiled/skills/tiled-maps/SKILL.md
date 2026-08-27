---
name: tiled-maps
description: >-
  Create, inspect, validate, convert, and author Tiled TMX/TMJ maps through
  Tiled's official command-line scripting and format APIs. Use for bounded
  level-layout, object-layer, external tileset, and tile-painting workflows.
  Do not use for controlling the interactive Tiled GUI.
license: MIT
compatibility: "Python 3.9+; Tiled 1.10+; dcc-mcp-core/CLI >=0.20.14"
allowed-tools: "python"
metadata:
  dcc-mcp:
    dcc: tiled
    layer: domain
    version: "0.4.2"  # x-release-please-version
    search-hint: "Tiled TMX TMJ tilemap level design object layer tileset paint validate convert Godot map"
    tags: [tiled, tilemap, level-design, game-dev, pipeline]
    tools: tools.yaml
---

# Tiled Maps

Use this Skill for deterministic map-file workflows backed by Tiled's native
`--evaluate`, `MapFormat`, `TilesetFormat`, `TileMap`, and layer APIs. The
adapter runs as a standalone service and invokes a fixed bundled JavaScript
driver. Tool arguments are JSON data only; callers cannot provide source code.

Keep source and output files under `DCC_MCP_TILED_ALLOWED_ROOTS`. Prefer a new
output path for authoring operations, inspect and validate it, then replace a
production map explicitly when that is the intended result.

This Skill does not claim interactive GUI control. Use DCC UI Control only when
the user explicitly needs a visible Tiled window and no typed map operation can
satisfy the task.
