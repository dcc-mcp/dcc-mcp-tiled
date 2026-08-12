/* Fixed typed driver for Tiled's official --evaluate scripting API. */
(function () {
    "use strict";

    var responsePath = null;

    function readJson(path) {
        var file = new TextFile(path, TextFile.ReadOnly);
        try {
            return JSON.parse(file.readAll());
        } finally {
            file.close();
        }
    }

    function writeJson(path, value) {
        var file = new TextFile(path, TextFile.WriteOnly);
        file.write(JSON.stringify(value));
        file.commit();
    }

    function fail(message) {
        throw new Error(String(message));
    }

    function setProperties(target, properties) {
        Object.keys(properties || {}).forEach(function (name) {
            var value = properties[name];
            if (value !== null)
                target.setProperty(name, value);
        });
    }

    function safeValue(value, depth) {
        if (depth > 4)
            return "[depth-limited]";
        if (value === null || value === undefined)
            return null;
        if (typeof value === "string" || typeof value === "boolean" || typeof value === "number")
            return value;
        if (Array.isArray(value))
            return value.slice(0, 1024).map(function (item) { return safeValue(item, depth + 1); });
        if (typeof value === "object") {
            var result = {};
            Object.keys(value).slice(0, 256).forEach(function (key) {
                result[key] = safeValue(value[key], depth + 1);
            });
            return result;
        }
        return String(value);
    }

    function propertiesOf(target) {
        return safeValue(target.properties(), 0) || {};
    }

    function loadMap(path) {
        var format = tiled.mapFormatForFile(path);
        if (!format)
            fail("No Tiled map reader is available for " + path);
        var map = format.read(path);
        if (!map || !map.isTileMap)
            fail("Tiled could not read the map");
        return map;
    }

    function writeMap(map, path) {
        var format = tiled.mapFormatForFile(path);
        if (!format)
            fail("No Tiled map writer is available for " + path);
        var error = format.write(map, path);
        if (error)
            fail(error);
    }

    function writeTileset(tileset, path) {
        var format = tiled.tilesetFormatForFile(path);
        if (!format)
            fail("No Tiled tileset writer is available for " + path);
        var error = format.write(tileset, path);
        if (error)
            fail(error);
    }

    function loadTileset(path) {
        var format = tiled.tilesetFormatForFile(path);
        if (!format)
            fail("No Tiled tileset reader is available for " + path);
        var tileset = format.read(path);
        if (!tileset || !tileset.isTileset)
            fail("Tiled could not read the tileset");
        return tileset;
    }

    function orientationName(value) {
        if (value === TileMap.Orthogonal) return "orthogonal";
        if (value === TileMap.Isometric) return "isometric";
        if (value === TileMap.Staggered) return "staggered";
        if (value === TileMap.Hexagonal) return "hexagonal";
        return "unknown";
    }

    function orientationValue(value) {
        if (value === "orthogonal") return TileMap.Orthogonal;
        if (value === "isometric") return TileMap.Isometric;
        if (value === "staggered") return TileMap.Staggered;
        if (value === "hexagonal") return TileMap.Hexagonal;
        fail("Unsupported map orientation: " + value);
    }

    function objectShapeName(object) {
        if (object.shape === MapObject.Ellipse) return "ellipse";
        if (object.shape === MapObject.Point) return "point";
        if (object.shape === MapObject.Polygon) return "polygon";
        if (object.shape === MapObject.Polyline) return "polyline";
        if (object.shape === MapObject.Text) return "text";
        return "rectangle";
    }

    function objectShapeValue(value) {
        if (value === "ellipse") return MapObject.Ellipse;
        if (value === "point") return MapObject.Point;
        if (value === "polygon") return MapObject.Polygon;
        if (value === "polyline") return MapObject.Polyline;
        return MapObject.Rectangle;
    }

    function pointValue(point) {
        return {x: Number(point.x), y: Number(point.y)};
    }

    function summarizeObject(object) {
        var result = {
            id: object.id,
            name: object.name,
            class_name: object.className || "",
            shape: objectShapeName(object),
            x: object.x,
            y: object.y,
            width: object.width,
            height: object.height,
            rotation: object.rotation,
            visible: object.visible,
            properties: propertiesOf(object)
        };
        if (result.shape === "polygon" || result.shape === "polyline")
            result.points = (object.polygon || []).map(pointValue);
        return result;
    }

    function layerType(layer) {
        if (layer.isTileLayer) return "tile";
        if (layer.isObjectLayer) return "object";
        if (layer.isGroupLayer) return "group";
        if (layer.isImageLayer) return "image";
        return "unknown";
    }

    function summarizeLayer(layer, options, counters) {
        var type = layerType(layer);
        var result = {
            id: layer.id,
            name: layer.name,
            type: type,
            class_name: layer.className || "",
            visible: layer.visible,
            locked: layer.locked,
            opacity: layer.opacity,
            offset: pointValue(layer.offset),
            parallax_factor: pointValue(layer.parallaxFactor),
            properties: propertiesOf(layer)
        };
        if (type === "tile") {
            result.width = layer.width;
            result.height = layer.height;
            var total = layer.width * layer.height;
            var scanLimit = Math.min(total, options.max_tiles_to_scan);
            var nonEmpty = 0;
            for (var index = 0; index < scanLimit; ++index) {
                var x = index % layer.width;
                var y = Math.floor(index / layer.width);
                if (layer.tileAt(x, y))
                    nonEmpty += 1;
            }
            result.non_empty_tiles_scanned = nonEmpty;
            result.tiles_scanned = scanLimit;
            result.tile_scan_truncated = scanLimit < total;
        } else if (type === "object") {
            result.object_count = layer.objectCount;
            result.objects = [];
            if (options.include_objects) {
                for (var objectIndex = 0; objectIndex < layer.objectCount; ++objectIndex) {
                    if (counters.objects >= options.max_objects)
                        break;
                    result.objects.push(summarizeObject(layer.objectAt(objectIndex)));
                    counters.objects += 1;
                }
            }
            result.objects_truncated = options.include_objects && result.objects.length < layer.objectCount;
        } else if (type === "group") {
            result.layers = [];
            for (var childIndex = 0; childIndex < layer.layerCount; ++childIndex)
                result.layers.push(summarizeLayer(layer.layerAt(childIndex), options, counters));
        } else if (type === "image") {
            result.image_file_name = layer.imageFileName || "";
            result.repeat_x = layer.repeatX;
            result.repeat_y = layer.repeatY;
        }
        return result;
    }

    function summarizeTileset(tileset) {
        return {
            name: tileset.name,
            file_name: tileset.fileName || "",
            class_name: tileset.className || "",
            tile_count: tileset.tileCount,
            tile_width: tileset.tileWidth,
            tile_height: tileset.tileHeight,
            image_file_name: tileset.imageFileName || "",
            image_width: tileset.imageWidth || 0,
            image_height: tileset.imageHeight || 0,
            columns: tileset.columnCount || 0,
            properties: propertiesOf(tileset)
        };
    }

    function summarizeMap(map, options) {
        options = options || {};
        options.include_objects = options.include_objects !== false;
        options.max_objects = Math.max(0, Number(options.max_objects || 0));
        options.max_tiles_to_scan = Math.max(0, Number(options.max_tiles_to_scan || 0));
        var counters = {objects: 0};
        var layers = [];
        for (var index = 0; index < map.layerCount; ++index)
            layers.push(summarizeLayer(map.layerAt(index), options, counters));
        return {
            file_name: map.fileName || "",
            class_name: map.className || "",
            width: map.width,
            height: map.height,
            tile_width: map.tileWidth,
            tile_height: map.tileHeight,
            infinite: map.infinite,
            orientation: orientationName(map.orientation),
            layer_count: map.layerCount,
            layers: layers,
            tileset_count: map.tilesets.length,
            tilesets: map.tilesets.map(summarizeTileset),
            properties: propertiesOf(map),
            objects_returned: counters.objects
        };
    }

    function findLayerByName(container, name, matches) {
        for (var index = 0; index < container.layerCount; ++index) {
            var layer = container.layerAt(index);
            if (layer.name === name)
                matches.push(layer);
            if (layer.isGroupLayer)
                findLayerByName(layer, name, matches);
        }
    }

    function requireUniqueLayer(map, name, expectedType) {
        var matches = [];
        findLayerByName(map, name, matches);
        if (matches.length === 0)
            fail("Layer not found: " + name);
        if (matches.length > 1)
            fail("Layer name is ambiguous: " + name);
        if (expectedType && layerType(matches[0]) !== expectedType)
            fail("Layer " + name + " is not a " + expectedType + " layer");
        return matches[0];
    }

    function makeLayer(spec) {
        var layer;
        if (spec.type === "tile")
            layer = new TileLayer(spec.name);
        else if (spec.type === "object")
            layer = new ObjectGroup(spec.name);
        else if (spec.type === "group")
            layer = new GroupLayer(spec.name);
        else
            fail("Unsupported layer type: " + spec.type);
        setProperties(layer, spec.properties);
        return layer;
    }

    function makeObject(spec) {
        var object = new MapObject(objectShapeValue(spec.shape), spec.name || "");
        object.className = spec.class_name || "";
        object.x = Number(spec.x);
        object.y = Number(spec.y);
        object.width = Number(spec.width);
        object.height = Number(spec.height);
        object.rotation = Number(spec.rotation);
        object.visible = spec.visible !== false;
        if (spec.shape === "polygon" || spec.shape === "polyline")
            object.polygon = spec.points.map(pointValue);
        setProperties(object, spec.properties);
        return object;
    }

    function operationStatus() {
        return {
            version: tiled.version,
            qt_version: tiled.qtVersion,
            platform: tiled.platform,
            arch: tiled.arch,
            map_formats: tiled.mapFormats.slice().sort(),
            tileset_formats: tiled.tilesetFormats.slice().sort(),
            cli_evaluate: true,
            arbitrary_script_input: false
        };
    }

    function execute(request) {
        var payload = request.payload || {};
        if (request.protocol_version !== 1)
            fail("Unsupported driver protocol version");
        if (request.operation === "status")
            return operationStatus();
        if (request.operation === "inspect_map") {
            var inspected = loadMap(payload.path);
            return summarizeMap(inspected, payload);
        }
        if (request.operation === "validate_map") {
            var validated = loadMap(payload.path);
            var validationSummary = summarizeMap(validated, {
                include_objects: false,
                max_objects: 0,
                max_tiles_to_scan: 0
            });
            return {valid: true, summary: validationSummary};
        }
        if (request.operation === "create_map") {
            var created = new TileMap();
            created.setSize(payload.width, payload.height);
            created.setTileSize(payload.tile_width, payload.tile_height);
            created.orientation = orientationValue(payload.orientation);
            setProperties(created, payload.properties);
            payload.layers.forEach(function (spec) { created.addLayer(makeLayer(spec)); });
            writeMap(created, payload.output_path);
            return {created: true, summary: summarizeMap(created, {include_objects: false, max_objects: 0, max_tiles_to_scan: 0})};
        }
        if (request.operation === "add_object_layer") {
            var layerMap = loadMap(payload.source_path);
            var existing = [];
            findLayerByName(layerMap, payload.layer_name, existing);
            if (existing.length)
                fail("Layer already exists: " + payload.layer_name);
            var objectLayer = new ObjectGroup(payload.layer_name);
            setProperties(objectLayer, payload.properties);
            layerMap.addLayer(objectLayer);
            writeMap(layerMap, payload.output_path);
            return {layer_name: payload.layer_name, layer_count: layerMap.layerCount};
        }
        if (request.operation === "write_objects") {
            var objectMap = loadMap(payload.source_path);
            var targetLayer = requireUniqueLayer(objectMap, payload.layer_name, "object");
            if (payload.mode === "replace_layer") {
                for (var removeIndex = targetLayer.objectCount - 1; removeIndex >= 0; --removeIndex)
                    targetLayer.removeObjectAt(removeIndex);
            }
            payload.objects.forEach(function (spec) { targetLayer.addObject(makeObject(spec)); });
            writeMap(objectMap, payload.output_path);
            return {layer_name: payload.layer_name, object_count: targetLayer.objectCount, written: payload.objects.length, mode: payload.mode};
        }
        if (request.operation === "add_tileset") {
            var tilesetMap = loadMap(payload.source_path);
            var tileset = loadTileset(payload.tileset_path);
            if (!tilesetMap.addTileset(tileset))
                fail("Tileset is already referenced by the map");
            writeMap(tilesetMap, payload.output_path);
            return {tileset_count: tilesetMap.tilesets.length, tileset: summarizeTileset(tileset)};
        }
        if (request.operation === "create_tileset") {
            var createdTileset = new Tileset(payload.name);
            createdTileset.tileSpacing = payload.spacing;
            createdTileset.margin = payload.margin;
            createdTileset.setTileSize(payload.tile_width, payload.tile_height);
            setProperties(createdTileset, payload.properties);
            createdTileset.imageFileName = payload.image_path;
            if (createdTileset.tileCount < 1)
                fail("Tileset image did not produce any tiles");
            writeTileset(createdTileset, payload.output_path);
            return {created: true, tileset: summarizeTileset(createdTileset)};
        }
        if (request.operation === "paint_tiles") {
            var tileMap = loadMap(payload.source_path);
            var tileLayer = requireUniqueLayer(tileMap, payload.layer_name, "tile");
            var edit = tileLayer.edit();
            payload.cells.forEach(function (cell) {
                if (cell.x >= tileLayer.width || cell.y >= tileLayer.height)
                    fail("Cell is outside tile layer bounds: " + cell.x + "," + cell.y);
                if (cell.tile_id === null) {
                    edit.setTile(cell.x, cell.y, null);
                    return;
                }
                var tileset = tileMap.tilesets[cell.tileset_index];
                if (!tileset)
                    fail("Tileset index is out of range: " + cell.tileset_index);
                var tile = tileset.findTile(cell.tile_id);
                if (!tile)
                    fail("Tile ID " + cell.tile_id + " was not found in tileset " + cell.tileset_index);
                var flags = 0;
                if (cell.flip_horizontal) flags |= Tile.FlippedHorizontally;
                if (cell.flip_vertical) flags |= Tile.FlippedVertically;
                if (cell.flip_diagonal) flags |= Tile.FlippedAntiDiagonally;
                edit.setTile(cell.x, cell.y, tile, flags);
            });
            edit.apply();
            writeMap(tileMap, payload.output_path);
            return {layer_name: payload.layer_name, painted: payload.cells.length};
        }
        if (request.operation === "convert_map") {
            var converted = loadMap(payload.source_path);
            writeMap(converted, payload.output_path);
            return {converted: true, summary: summarizeMap(converted, {include_objects: false, max_objects: 0, max_tiles_to_scan: 0})};
        }
        fail("Unsupported typed operation: " + request.operation);
    }

    try {
        var args = tiled.scriptArguments;
        if (!args || args.length < 2)
            fail("Expected request and response paths");
        responsePath = args[1];
        var request = readJson(args[0]);
        writeJson(responsePath, {ok: true, result: execute(request)});
    } catch (error) {
        if (responsePath)
            writeJson(responsePath, {ok: false, error: String(error && error.message ? error.message : error)});
        else
            tiled.error(String(error));
    }
}());
