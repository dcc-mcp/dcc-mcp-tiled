"""Bounded wrapper around Tiled's official command-line scripting API."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from dcc_mcp_core.skills_helper import check_dcc_cancelled

_MAP_SUFFIXES = frozenset({".tmj", ".tmx", ".json"})
_TILESET_SUFFIXES = frozenset({".tsj", ".tsx", ".json"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif"})
_ORIENTATIONS = frozenset({"orthogonal", "isometric", "staggered", "hexagonal"})
_LAYER_TYPES = frozenset({"tile", "object", "group"})
_OBJECT_SHAPES = frozenset({"rectangle", "ellipse", "point", "polygon", "polyline"})


class TiledError(RuntimeError):
    """A bounded, user-actionable Tiled adapter failure."""


class TiledTimeoutError(TiledError):
    """Tiled did not complete before the configured deadline."""


class TiledLaunchError(TiledError):
    """The configured Tiled executable could not be launched."""


def _split_roots(value: str) -> list[Path]:
    roots = []
    for item in value.split(os.pathsep):
        item = item.strip()
        if item:
            roots.append(Path(item).expanduser().resolve())
    return roots


def _within(path: Path, roots: Sequence[Path]) -> bool:
    candidate = os.path.normcase(str(path))
    for root in roots:
        normalized_root = os.path.normcase(str(root))
        try:
            if os.path.commonpath((candidate, normalized_root)) == normalized_root:
                return True
        except ValueError:
            continue
    return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TiledError("%s must be a number" % name)
    number = float(value)
    if not math.isfinite(number):
        raise TiledError("%s must be finite" % name)
    if abs(number) > 1_000_000_000:
        raise TiledError("%s is outside the supported coordinate range" % name)
    return number


def _text(value: Any, name: str, maximum: int = 512, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TiledError("%s must be a string" % name)
    if not allow_empty and not value.strip():
        raise TiledError("%s must not be empty" % name)
    if len(value) > maximum:
        raise TiledError("%s is limited to %d characters" % (name, maximum))
    return value


def _properties(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TiledError("properties must be an object")
    if len(value) > 128:
        raise TiledError("properties are limited to 128 entries")
    result = {}
    for raw_name, raw_value in value.items():
        name = _text(raw_name, "property name", maximum=256)
        if raw_value is None or isinstance(raw_value, (bool, int, float, str)):
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                raise TiledError("property %s must be finite" % name)
            if isinstance(raw_value, str) and len(raw_value) > 8192:
                raise TiledError("property %s is limited to 8192 characters" % name)
            result[name] = raw_value
            continue
        raise TiledError("property %s must be null, boolean, number, or string" % name)
    return result


class TiledCli:
    """Typed, workspace-bounded wrapper around Tiled's official CLI API."""

    def __init__(
        self,
        executable: Optional[str] = None,
        allowed_roots: Optional[Iterable[Path]] = None,
        max_map_bytes: int = 128 * 1024 * 1024,
        max_request_bytes: int = 2 * 1024 * 1024,
        max_response_bytes: int = 4 * 1024 * 1024,
        max_tile_cells: int = 4_000_000,
        max_cells_per_call: int = 100_000,
        max_objects_per_call: int = 2_000,
        max_timeout_secs: float = 600,
        driver_path: Optional[Path] = None,
    ) -> None:
        self.executable = self._resolve_executable(executable)
        roots = list(allowed_roots or (Path.cwd(),))
        self.allowed_roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self.max_map_bytes = max(1, int(max_map_bytes))
        self.max_request_bytes = max(1, int(max_request_bytes))
        self.max_response_bytes = max(1, int(max_response_bytes))
        self.max_tile_cells = max(1, int(max_tile_cells))
        self.max_cells_per_call = max(1, int(max_cells_per_call))
        self.max_objects_per_call = max(1, int(max_objects_per_call))
        self.max_timeout_secs = max(1.0, float(max_timeout_secs))
        self.driver_path = (driver_path or self._default_driver_path()).resolve()

    @classmethod
    def from_env(cls) -> "TiledCli":
        roots_value = os.environ.get("DCC_MCP_TILED_ALLOWED_ROOTS", "")
        roots = _split_roots(roots_value) if roots_value else [Path.cwd().resolve()]
        return cls(
            os.environ.get("DCC_MCP_TILED_EXECUTABLE") or None,
            allowed_roots=roots,
            max_map_bytes=int(
                os.environ.get("DCC_MCP_TILED_MAX_MAP_BYTES", str(128 * 1024 * 1024))
            ),
            max_request_bytes=int(
                os.environ.get("DCC_MCP_TILED_MAX_REQUEST_BYTES", str(2 * 1024 * 1024))
            ),
            max_response_bytes=int(
                os.environ.get("DCC_MCP_TILED_MAX_RESPONSE_BYTES", str(4 * 1024 * 1024))
            ),
            max_tile_cells=int(os.environ.get("DCC_MCP_TILED_MAX_TILE_CELLS", "4000000")),
            max_cells_per_call=int(os.environ.get("DCC_MCP_TILED_MAX_CELLS_PER_CALL", "100000")),
            max_objects_per_call=int(os.environ.get("DCC_MCP_TILED_MAX_OBJECTS_PER_CALL", "2000")),
            max_timeout_secs=float(os.environ.get("DCC_MCP_TILED_MAX_TIMEOUT_SECS", "600")),
        )

    @staticmethod
    def _default_driver_path() -> Path:
        installed = Path(__file__).resolve().parent / "tiled_cli" / "driver.js"
        if installed.is_file():
            return installed
        return (
            Path(__file__).resolve().parents[2] / "bridge" / "tiled-cli" / "dcc_mcp_tiled_driver.js"
        )

    @staticmethod
    def _resolve_executable(explicit: Optional[str]) -> Optional[str]:
        candidates = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        else:
            found = shutil.which("tiled") or shutil.which("tiled.exe")
            if found:
                candidates.append(Path(found))
            if os.name == "nt":
                program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
                candidates.extend(
                    (
                        program_files / "Tiled" / "tiled.exe",
                        local_app_data / "Programs" / "Tiled" / "tiled.exe",
                    )
                )
        for candidate in candidates:
            try:
                candidate = candidate.resolve()
            except OSError:
                continue
            if candidate.is_file():
                return str(candidate)
        return None

    def _timeout(self, value: float) -> float:
        timeout = float(value)
        if timeout <= 0 or timeout > self.max_timeout_secs:
            raise TiledError(
                "timeout_secs must be greater than 0 and no more than %s"
                % int(self.max_timeout_secs)
            )
        return timeout

    def _run(self, args: Sequence[str], timeout_secs: float) -> dict[str, Any]:
        if not self.executable:
            raise TiledError("Tiled was not found; set DCC_MCP_TILED_EXECUTABLE")
        timeout = self._timeout(timeout_secs)
        command = [self.executable] + [str(item) for item in args]
        started = time.monotonic()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        environment = os.environ.copy()
        if os.name != "nt" and not environment.get("DISPLAY"):
            environment.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file:
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        text=True,
                        env=environment,
                        creationflags=creationflags,
                    )
                except OSError as exc:
                    raise TiledLaunchError("Tiled executable could not be launched") from exc
                try:
                    deadline = started + timeout
                    while process.poll() is None:
                        check_dcc_cancelled()
                        if time.monotonic() >= deadline:
                            raise TiledTimeoutError(
                                "Tiled exceeded the %.1f second timeout" % timeout
                            )
                        time.sleep(0.05)
                except BaseException:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    raise
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read(65_537)
                stderr = stderr_file.read(65_537)
        return {
            "returncode": int(process.returncode or 0),
            "duration_secs": round(time.monotonic() - started, 3),
            "stdout": stdout[:65_536],
            "stderr": stderr[:65_536],
            "stdout_truncated": len(stdout) > 65_536,
            "stderr_truncated": len(stderr) > 65_536,
        }

    def _invoke(
        self, operation: str, payload: Mapping[str, Any], timeout_secs: float
    ) -> dict[str, Any]:
        if not self.driver_path.is_file():
            raise TiledError("Bundled Tiled CLI driver is missing")
        request = {"protocol_version": 1, "operation": operation, "payload": dict(payload)}
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.max_request_bytes:
            raise TiledError("Tiled request exceeds the configured size limit")
        with tempfile.TemporaryDirectory(prefix="dcc-mcp-tiled-") as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            response_path = Path(temp_dir) / "response.json"
            request_path.write_bytes(encoded)
            run = self._run(
                ("--evaluate", str(self.driver_path), str(request_path), str(response_path)),
                timeout_secs,
            )
            if not response_path.is_file():
                detail = (run["stderr"] or run["stdout"]).strip().splitlines()
                raise TiledError(
                    "Tiled CLI driver produced no response%s"
                    % (": %s" % detail[-1] if detail else "")
                )
            response_size = response_path.stat().st_size
            if response_size > self.max_response_bytes:
                raise TiledError("Tiled response exceeds the configured size limit")
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise TiledError("Tiled CLI driver returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise TiledError("Tiled CLI driver returned an invalid envelope")
        if not response.get("ok"):
            raise TiledError(str(response.get("error") or "Tiled operation failed"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise TiledError("Tiled CLI driver returned an invalid result")
        result["engine"] = {
            "returncode": run["returncode"],
            "duration_secs": run["duration_secs"],
            "stdout_truncated": run["stdout_truncated"],
            "stderr_truncated": run["stderr_truncated"],
        }
        return result

    def _input_path(self, value: str, suffixes: frozenset[str], label: str) -> Path:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() not in suffixes:
            raise TiledError("%s has an unsupported extension" % label)
        if not path.is_file():
            raise TiledError("%s does not exist: %s" % (label, path))
        if not _within(path, self.allowed_roots):
            raise TiledError("%s is outside DCC_MCP_TILED_ALLOWED_ROOTS" % label)
        if path.stat().st_size > self.max_map_bytes:
            raise TiledError("%s exceeds the configured size limit" % label)
        return path

    def _output_path(self, value: str, suffixes: frozenset[str], label: str) -> Path:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() not in suffixes:
            raise TiledError("%s has an unsupported extension" % label)
        if not path.parent.is_dir():
            raise TiledError("Output directory does not exist: %s" % path.parent)
        if not _within(path, self.allowed_roots):
            raise TiledError("%s is outside DCC_MCP_TILED_ALLOWED_ROOTS" % label)
        return path

    def _write_operation(
        self,
        operation: str,
        payload: Mapping[str, Any],
        destination: Path,
        overwrite: bool,
        timeout_secs: float,
    ) -> dict[str, Any]:
        replaced_existing = destination.exists()
        if replaced_existing and not overwrite:
            raise TiledError("Output already exists; set overwrite=true to replace it")
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".%s." % destination.stem,
            suffix=destination.suffix,
            dir=str(destination.parent),
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        temp_path.unlink()
        request_payload = dict(payload)
        request_payload["output_path"] = str(temp_path)
        try:
            result = self._invoke(operation, request_payload, timeout_secs)
            if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                raise TiledError("Tiled operation produced no output map")
            if temp_path.stat().st_size > self.max_map_bytes:
                raise TiledError("Tiled output exceeds the configured size limit")
            os.replace(str(temp_path), str(destination))
        finally:
            if temp_path.exists():
                temp_path.unlink()
        result.update(
            {
                "output_path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": _sha256_file(destination),
                "overwritten": replaced_existing,
            }
        )
        return result

    def status(self) -> dict[str, Any]:
        base = {
            "ready": False,
            "executable": self.executable,
            "instance_type": "standalone",
            "driver": "official_tiled_cli_script_api",
            "allowed_roots": [str(root) for root in self.allowed_roots],
            "limits": {
                "max_map_bytes": self.max_map_bytes,
                "max_request_bytes": self.max_request_bytes,
                "max_response_bytes": self.max_response_bytes,
                "max_tile_cells": self.max_tile_cells,
                "max_cells_per_call": self.max_cells_per_call,
                "max_objects_per_call": self.max_objects_per_call,
                "max_timeout_secs": self.max_timeout_secs,
            },
        }
        if not self.executable:
            base["reason"] = "tiled_not_found"
            return base
        if not self.driver_path.is_file():
            base["reason"] = "driver_missing"
            return base
        result = self._invoke("status", {}, 30)
        base.update(result)
        base["ready"] = True
        return base

    def inspect_map(
        self,
        path: str,
        include_objects: bool = True,
        max_objects: int = 1_000,
        max_tiles_to_scan: int = 250_000,
        timeout_secs: float = 120,
    ) -> dict[str, Any]:
        source = self._input_path(path, _MAP_SUFFIXES, "Map")
        max_objects = int(max_objects)
        max_tiles_to_scan = int(max_tiles_to_scan)
        if not 0 <= max_objects <= self.max_objects_per_call:
            raise TiledError("max_objects exceeds the configured per-call limit")
        if not 0 <= max_tiles_to_scan <= self.max_tile_cells:
            raise TiledError("max_tiles_to_scan exceeds the configured limit")
        return self._invoke(
            "inspect_map",
            {
                "path": str(source),
                "include_objects": bool(include_objects),
                "max_objects": max_objects,
                "max_tiles_to_scan": max_tiles_to_scan,
            },
            timeout_secs,
        )

    def validate_map(self, path: str, timeout_secs: float = 120) -> dict[str, Any]:
        source = self._input_path(path, _MAP_SUFFIXES, "Map")
        return self._invoke("validate_map", {"path": str(source)}, timeout_secs)

    def create_map(
        self,
        output_path: str,
        width: int,
        height: int,
        tile_width: int = 32,
        tile_height: int = 32,
        orientation: str = "orthogonal",
        layers: Optional[Sequence[Mapping[str, Any]]] = None,
        properties: Optional[Mapping[str, Any]] = None,
        overwrite: bool = False,
        timeout_secs: float = 120,
    ) -> dict[str, Any]:
        destination = self._output_path(output_path, _MAP_SUFFIXES, "Output map")
        width = int(width)
        height = int(height)
        tile_width = int(tile_width)
        tile_height = int(tile_height)
        if width <= 0 or height <= 0 or width * height > self.max_tile_cells:
            raise TiledError("Map dimensions exceed the configured tile-cell limit")
        if not 1 <= tile_width <= 8192 or not 1 <= tile_height <= 8192:
            raise TiledError("Tile width and height must be between 1 and 8192")
        if orientation not in _ORIENTATIONS:
            raise TiledError("Unsupported map orientation")
        layer_payload = []
        for index, layer in enumerate(layers or ({"type": "object", "name": "Objects"},)):
            if not isinstance(layer, Mapping):
                raise TiledError("layers[%d] must be an object" % index)
            layer_type = layer.get("type")
            if layer_type not in _LAYER_TYPES:
                raise TiledError("layers[%d].type is unsupported" % index)
            layer_payload.append(
                {
                    "type": layer_type,
                    "name": _text(layer.get("name", ""), "layers[%d].name" % index),
                    "properties": _properties(layer.get("properties")),
                }
            )
        if len(layer_payload) > 256:
            raise TiledError("A map is limited to 256 initial layers")
        return self._write_operation(
            "create_map",
            {
                "width": width,
                "height": height,
                "tile_width": tile_width,
                "tile_height": tile_height,
                "orientation": orientation,
                "layers": layer_payload,
                "properties": _properties(properties),
            },
            destination,
            bool(overwrite),
            timeout_secs,
        )

    def add_object_layer(
        self,
        source_path: str,
        output_path: str,
        layer_name: str,
        properties: Optional[Mapping[str, Any]] = None,
        overwrite: bool = False,
        timeout_secs: float = 120,
    ) -> dict[str, Any]:
        source = self._input_path(source_path, _MAP_SUFFIXES, "Source map")
        destination = self._output_path(output_path, _MAP_SUFFIXES, "Output map")
        return self._write_operation(
            "add_object_layer",
            {
                "source_path": str(source),
                "layer_name": _text(layer_name, "layer_name"),
                "properties": _properties(properties),
            },
            destination,
            bool(overwrite),
            timeout_secs,
        )

    def write_objects(
        self,
        source_path: str,
        output_path: str,
        layer_name: str,
        objects: Sequence[Mapping[str, Any]],
        mode: str = "append",
        overwrite: bool = False,
        timeout_secs: float = 120,
    ) -> dict[str, Any]:
        source = self._input_path(source_path, _MAP_SUFFIXES, "Source map")
        destination = self._output_path(output_path, _MAP_SUFFIXES, "Output map")
        if mode not in ("append", "replace_layer"):
            raise TiledError("mode must be append or replace_layer")
        if not isinstance(objects, Sequence) or isinstance(objects, (str, bytes)):
            raise TiledError("objects must be an array")
        if not 1 <= len(objects) <= self.max_objects_per_call:
            raise TiledError("objects exceed the configured per-call limit")
        object_payload = []
        for index, item in enumerate(objects):
            if not isinstance(item, Mapping):
                raise TiledError("objects[%d] must be an object" % index)
            shape = item.get("shape", "rectangle")
            if shape not in _OBJECT_SHAPES:
                raise TiledError("objects[%d].shape is unsupported" % index)
            points = item.get("points", [])
            if shape in ("polygon", "polyline"):
                if not isinstance(points, Sequence) or not 2 <= len(points) <= 4096:
                    raise TiledError("Polygon and polyline objects require 2-4096 points")
                normalized_points = []
                for point_index, point in enumerate(points):
                    if not isinstance(point, Mapping):
                        raise TiledError("Object point must be an object")
                    normalized_points.append(
                        {
                            "x": _finite_number(point.get("x"), "point[%d].x" % point_index),
                            "y": _finite_number(point.get("y"), "point[%d].y" % point_index),
                        }
                    )
            else:
                normalized_points = []
            object_payload.append(
                {
                    "name": _text(
                        item.get("name", ""),
                        "objects[%d].name" % index,
                        allow_empty=True,
                    ),
                    "class_name": _text(
                        item.get("class_name", ""),
                        "objects[%d].class_name" % index,
                        allow_empty=True,
                    ),
                    "shape": shape,
                    "x": _finite_number(item.get("x", 0), "objects[%d].x" % index),
                    "y": _finite_number(item.get("y", 0), "objects[%d].y" % index),
                    "width": _finite_number(item.get("width", 0), "objects[%d].width" % index),
                    "height": _finite_number(item.get("height", 0), "objects[%d].height" % index),
                    "rotation": _finite_number(
                        item.get("rotation", 0), "objects[%d].rotation" % index
                    ),
                    "visible": bool(item.get("visible", True)),
                    "points": normalized_points,
                    "properties": _properties(item.get("properties")),
                }
            )
        return self._write_operation(
            "write_objects",
            {
                "source_path": str(source),
                "layer_name": _text(layer_name, "layer_name"),
                "objects": object_payload,
                "mode": mode,
            },
            destination,
            bool(overwrite),
            timeout_secs,
        )

    def add_tileset(
        self,
        source_path: str,
        tileset_path: str,
        output_path: str,
        overwrite: bool = False,
        timeout_secs: float = 120,
    ) -> dict[str, Any]:
        source = self._input_path(source_path, _MAP_SUFFIXES, "Source map")
        tileset = self._input_path(tileset_path, _TILESET_SUFFIXES, "Tileset")
        destination = self._output_path(output_path, _MAP_SUFFIXES, "Output map")
        return self._write_operation(
            "add_tileset",
            {"source_path": str(source), "tileset_path": str(tileset)},
            destination,
            bool(overwrite),
            timeout_secs,
        )

    def create_tileset(
        self,
        image_path: str,
        output_path: str,
        name: str,
        tile_width: int,
        tile_height: int,
        spacing: int = 0,
        margin: int = 0,
        properties: Optional[Mapping[str, Any]] = None,
        overwrite: bool = False,
        timeout_secs: float = 120,
    ) -> dict[str, Any]:
        image = self._input_path(image_path, _IMAGE_SUFFIXES, "Tileset image")
        destination = self._output_path(output_path, _TILESET_SUFFIXES, "Output tileset")
        tile_width = int(tile_width)
        tile_height = int(tile_height)
        spacing = int(spacing)
        margin = int(margin)
        if not 1 <= tile_width <= 8192 or not 1 <= tile_height <= 8192:
            raise TiledError("Tile width and height must be between 1 and 8192")
        if not 0 <= spacing <= 8192 or not 0 <= margin <= 8192:
            raise TiledError("Tileset spacing and margin must be between 0 and 8192")
        return self._write_operation(
            "create_tileset",
            {
                "image_path": str(image),
                "name": _text(name, "name"),
                "tile_width": tile_width,
                "tile_height": tile_height,
                "spacing": spacing,
                "margin": margin,
                "properties": _properties(properties),
            },
            destination,
            bool(overwrite),
            timeout_secs,
        )

    def paint_tiles(
        self,
        source_path: str,
        output_path: str,
        layer_name: str,
        cells: Sequence[Mapping[str, Any]],
        default_tileset_index: int = 0,
        overwrite: bool = False,
        timeout_secs: float = 180,
    ) -> dict[str, Any]:
        source = self._input_path(source_path, _MAP_SUFFIXES, "Source map")
        destination = self._output_path(output_path, _MAP_SUFFIXES, "Output map")
        if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
            raise TiledError("cells must be an array")
        if not 1 <= len(cells) <= self.max_cells_per_call:
            raise TiledError("cells exceed the configured per-call limit")
        default_tileset_index = int(default_tileset_index)
        if default_tileset_index < 0:
            raise TiledError("default_tileset_index must be non-negative")
        cell_payload = []
        for index, cell in enumerate(cells):
            if not isinstance(cell, Mapping):
                raise TiledError("cells[%d] must be an object" % index)
            x = int(cell.get("x", -1))
            y = int(cell.get("y", -1))
            if x < 0 or y < 0:
                raise TiledError("Cell coordinates must be non-negative")
            tile_id = cell.get("tile_id")
            if tile_id is not None:
                tile_id = int(tile_id)
                if tile_id < 0:
                    raise TiledError("tile_id must be null or non-negative")
            tileset_index = int(cell.get("tileset_index", default_tileset_index))
            if tileset_index < 0:
                raise TiledError("tileset_index must be non-negative")
            cell_payload.append(
                {
                    "x": x,
                    "y": y,
                    "tile_id": tile_id,
                    "tileset_index": tileset_index,
                    "flip_horizontal": bool(cell.get("flip_horizontal", False)),
                    "flip_vertical": bool(cell.get("flip_vertical", False)),
                    "flip_diagonal": bool(cell.get("flip_diagonal", False)),
                }
            )
        return self._write_operation(
            "paint_tiles",
            {
                "source_path": str(source),
                "layer_name": _text(layer_name, "layer_name"),
                "cells": cell_payload,
            },
            destination,
            bool(overwrite),
            timeout_secs,
        )

    def convert_map(
        self,
        source_path: str,
        output_path: str,
        overwrite: bool = False,
        timeout_secs: float = 180,
    ) -> dict[str, Any]:
        source = self._input_path(source_path, _MAP_SUFFIXES, "Source map")
        destination = self._output_path(output_path, _MAP_SUFFIXES, "Output map")
        return self._write_operation(
            "convert_map",
            {"source_path": str(source)},
            destination,
            bool(overwrite),
            timeout_secs,
        )


def get_bridge() -> TiledCli:
    return TiledCli.from_env()
