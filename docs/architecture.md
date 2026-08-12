# Architecture

`dcc-mcp-tiled` is a standalone adapter around Tiled's supported command-line
scripting surface. It deliberately does not install a Python plug-in into Tiled
or open a custom network listener inside the editor.

```text
MCP client
  -> DCC-MCP Core HTTP server and bounded job manager
  -> typed Skill script
  -> TiledCli validation and filesystem boundary
  -> tiled --evaluate <bundled driver> <request.json> <response.json>
  -> Tiled MapFormat, TilesetFormat, TileMap, and layer APIs
  -> atomic TMJ/TMX/TSJ/TSX output
```

## Ownership boundaries

- DCC-MCP Core owns discovery, MCP transport, Skill registration, jobs,
  cancellation, and lifecycle.
- The adapter owns typed validation, allowed-root enforcement, subprocess
  deadlines, temporary request/response files, and atomic output replacement.
- The bundled JavaScript driver owns only translation between the fixed protocol
  and Tiled's scripting objects.
- Tiled owns map and tileset parsing, serialization, registered formats, and
  compatibility semantics.

## Protocol

The private driver protocol has an integer `protocol_version`, one allowlisted
`operation`, and a JSON `payload`. The response is either `{ "ok": true,
"result": ... }` or `{ "ok": false, "error": ... }`. There is no field for
source text, module loading, shell commands, or a callback endpoint.

Each invocation gets a private temporary directory. Request and response sizes
are checked before parsing, stdout and stderr capture is bounded, and temporary
files are removed after the result has been validated.

## Mutation model

Mutating calls never ask Tiled to write directly over the destination. The
adapter reserves a same-directory temporary name, asks Tiled to serialize into
that path, checks the result and configured file-size ceiling, then uses an
atomic replace. A pre-existing destination is rejected unless the typed request
sets `overwrite=true`.

## Interactive editor boundary

Tiled's JavaScript extension API does not provide a supported Python plug-in or
a built-in scripting-side HTTP/TCP server. A persistent interactive editor
bridge would therefore require a separately maintained native Tiled plug-in.
This adapter makes no such claim. The current contract is deterministic file
authoring through the official CLI scripting API.
