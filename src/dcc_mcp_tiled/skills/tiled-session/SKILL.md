---
name: tiled-session
description: >-
  Inspect the connected Tiled session through the DCC-MCP Python plug-in
  bridge. Use for session health, open images, and active image metadata.
license: MIT
compatibility: "Tiled.0+; dcc-mcp-core 0.19+"
allowed-tools: "python"
metadata:
  dcc-mcp:
    dcc: tiled
    layer: domain
    version: "0.1.0"
    search-hint: "TILED image editor session document active image layers"
    tags: "tiled,image-editing,session"
    tools: tools.yaml
    depends: "dcc-diagnostics"
---

# TILED Session

Install and run the bundled Tiled plug-in before using this skill. Calls use a
loopback JSON-lines bridge and never execute arbitrary TILED/Python source.
