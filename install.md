# Install dcc-mcp-tiled

## Requirements

- Python 3.9 or newer.
- DCC-MCP Core 0.20.14 or newer. Catalog planning additionally requires the
  exact official Core 0.20.14 CLI asset selected by
  `DCC_MCP_TILED_CORE_CLI` or `PATH`; the doctor checks its full SHA-256
  against the platform update manifest that Core publishes with a Sigstore
  bundle. Matching `--version` output alone is never trusted.
- Tiled 1.10.0 or newer with the official `--evaluate` scripting interface.
- A trusted `dcc-mcp-tiled` wheel. The project is not currently published on
  PyPI, and the Core catalog does not yet provide a pinned install URL and
  SHA-256. Do not treat `pip install dcc-mcp-tiled` as a working install path.

The adapter is a standalone service. It does not install a plug-in into Tiled,
does not need a running editor window, and does not require authentication to a
remote Tiled endpoint.

## Supported versions and platforms

The supported Tiled floor is 1.10.0. CI verifies Tiled 1.12.2 on Linux using
the official AppImage pinned by version and SHA-256.

| Platform | Tiled executable |
| --- | --- |
| Windows | `tiled.exe` from the official installer; common Program Files and per-user locations are discovered |
| macOS | `Tiled.app/Contents/MacOS/Tiled` from the official DMG or an operator-managed package manager |
| Linux | `tiled` from the distribution package manager, or an exact official AppImage release |

For an AppImage or any downloaded binary, select an exact release and verify
its publisher-provided SHA-256 before execution. Never download or scrape a
mutable `latest` URL.

## Agent quick path

Until a catalog install block is published, obtain the wheel from a trusted
release or build it from a reviewed checkout. Verify the wheel SHA-256 supplied
by that source, then install the wheel file itself:

```text
python -m pip install <verified-dcc-mcp-tiled-wheel>
dcc-mcp-tiled-doctor doctor --json
```

The wheel path must replace the placeholder above. Agents must not infer a
future release URL or checksum. The intended catalog instructions URL is:

```text
https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-tiled/main/install.md
```

Exit codes are stable:

| Code | Meaning |
| --- | --- |
| 0 | Tiled, Core, configuration, and the fixed CLI driver are directly usable |
| 10 | Installation or configuration preflight is incomplete |
| 40 | Tiled was found but runtime verification failed |

## Manual path

1. Install Tiled 1.10.0 or newer through the operating system or from an exact
   official release.
2. Install a checksum-verified adapter wheel as shown above. Do not use an
   editable source install as the production path.
3. If discovery does not find Tiled, set its exact executable:

   ```powershell
   $env:DCC_MCP_TILED_EXECUTABLE = "C:\Program Files\Tiled\tiled.exe"
   ```

   ```bash
   export DCC_MCP_TILED_EXECUTABLE="/Applications/Tiled.app/Contents/MacOS/Tiled" # macOS
   export DCC_MCP_TILED_EXECUTABLE="/opt/tiled/Tiled-1.12.2.AppImage"             # Linux
   ```

   A missing or unverifiable executable is reported as
   `verified_tiled_acquisition_required` with no synthetic command or path
   placeholder. Install and verify the operator-selected Tiled release before
   retrying.

4. Set the map and art roots that the adapter may access. Windows separates
   roots with `;`; macOS and Linux use `:`:

   ```powershell
   $env:DCC_MCP_TILED_ALLOWED_ROOTS = "D:\game\maps;D:\game\art"
   ```

   ```bash
   export DCC_MCP_TILED_ALLOWED_ROOTS="$HOME/game/maps:$HOME/game/art"
   ```

5. Run verification, then start the foreground standalone service:

   ```text
   dcc-mcp-tiled-doctor verify --json
   dcc-mcp-tiled
   ```

`dcc-mcp-tiled-install` remains only as a backward-compatible, deprecated
doctor alias. It performs no writes or installation. Its JSON output explicitly
reports `mode: verify_only` and `writes_performed: false`.

## Verify

Run either standard verb through the doctor entry point:

```text
dcc-mcp-tiled-doctor doctor --json
dcc-mcp-tiled-doctor verify --json
```

The report includes executable discovery, Tiled and Qt versions, Core and Tiled
floors, allowed roots, runtime limits, direct usability, a stable failure stage
and reason, and structured `next_steps`. It conforms to Install SOP schema v1:
integer `schema_version: 1`, `dcc_type`, adapter/Core versions, `steps`,
`receipt_path`, and the nested `verify` result are always present. Every
remediation has `id`, `description`, and `why`, plus exactly one executable
`command` or `file_edit`. When no bounded executable continuation exists, the
report instead returns an explicit `blocker` and an empty `next_steps` array.
A successful report has `directly_usable: true`,
`status: ok`, and `exit_code: 0`; every unsuccessful report uses `status:
failed` with exit 10 or 40.

After starting `dcc-mcp-tiled`, use the DCC-MCP CLI to discover its registered
loopback endpoint. Tiled itself has no network endpoint or credentials in this
adapter architecture.

## Upgrade

1. Stop the foreground `dcc-mcp-tiled` process.
2. Obtain and checksum-verify an exact newer wheel.
3. Upgrade that wheel file:

   ```text
   python -m pip install --upgrade <verified-dcc-mcp-tiled-wheel>
   ```

4. Upgrade Tiled through the same operator-managed source used for installation.
   For AppImages, download an exact newer filename, verify its SHA-256, update
   `DCC_MCP_TILED_EXECUTABLE`, and remove the old AppImage only after verification.
5. Run `dcc-mcp-tiled-doctor verify --json` again before restarting the service.

The adapter does not auto-provision Tiled and has no persistent binary or
download cache. Its request and response files use temporary directories that
are removed after each operation. The pinned CI AppImage lives only in the
ephemeral GitHub Actions workspace. Therefore adapter cache cleanup is `none`;
operator-managed Tiled packages and downloads remain under operator control.

## Uninstall

Stop the foreground service, then remove the Python distribution:

```text
python -m pip uninstall dcc-mcp-tiled
```

This does not remove Tiled, maps, art, Tiled preferences, or operator-managed
AppImages. Remove those separately only when intended. There is no adapter
binary cache or host plug-in directory to clean.

## Troubleshooting

### `tiled_not_found` / exit 10

Install a supported Tiled release, verify the selected artifact, and set
`DCC_MCP_TILED_EXECUTABLE` to its exact executable. On macOS, point inside
`Tiled.app`; on Linux AppImage installs, make the exact verified file
executable. The doctor deliberately returns an honest blocker rather than a
command containing an unknown path.

### `tiled_version_below_floor` / exit 10

Upgrade Tiled to 1.10.0 or newer, then rerun verification. CI's pinned 1.12.2
version is the strongest automated compatibility evidence.

### `driver_missing` / exit 10

Run the returned `dcc-mcp-tiled-doctor catalog-plan --json` command to resolve
the current adapter catalog plan. The wrapper resolves one exact CLI path,
checks its size and SHA-256 against Core 0.20.14's platform update manifest,
binds the manifest and Sigstore-bundle digests, repeats those checks immediately
before execution, and invokes the CLI without a shell. A PATH or file
replacement fails closed. Planning is read-only and does not install or repair
the adapter. Accept a wheel only when the plan or another trusted source
provides an immutable artifact and SHA-256; the current PyPI and pinned catalog
publication work is still pending. The following retry retains the exact Tiled
executable selected by the original doctor request.

### `core_version_below_floor` / exit 10

Upgrade DCC-MCP Core to at least 0.20.14 in the same Python environment as the
adapter.

### `core_cli_unavailable` / `core_cli_untrusted` / `core_cli_version_mismatch` / exit 10

Install the official `dcc-mcp-cli` asset for the same final release reported by
the imported Python Core package. Verify the release's signed platform update
manifest and CLI checksum, then set `DCC_MCP_TILED_CORE_CLI` to that absolute
asset path or place only that asset on `PATH`. The report is an honest blocker;
it does not open a browser or claim that version output proves provenance. A
missing, mismatched, replaced, or digest-invalid CLI is never used to resolve a
catalog plan.

### `invalid_environment` / exit 10

Correct numeric `DCC_MCP_TILED_MAX_*` values and path-list separators. The
doctor never substitutes unsafe defaults for malformed values.

### `tiled_timeout` or `tiled_runtime_error` / exit 40

Run Tiled from the same shell to expose missing Qt/display dependencies. Linux
headless environments need a working display backend such as the CI Xvfb setup.
Keep `DCC_MCP_TILED_EXECUTABLE` on the same checksum-verified binary and rerun
`dcc-mcp-tiled-doctor verify --json`.

### Service not discovered

Doctor verifies the local Tiled CLI, not a remote endpoint. Start
`dcc-mcp-tiled` in the same Python environment, then inspect DCC-MCP Core's
registered instances. There is no Tiled authentication token to configure.
