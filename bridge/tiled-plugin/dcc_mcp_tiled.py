#!/usr/bin/env python3
"""Loopback bridge contract for Tiled integrations."""

import json
import os
import socketserver

PORT = int(os.environ.get("DCC_MCP_TILED_BRIDGE_PORT", "3848"))


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        method = request.get("method")
        if method in {"tiled.get_status", "tiled.ping"}:
            result = {"ready": True, "bridge_port": PORT}
        elif method in {"tiled.list_maps", "tiled.list_objects", "tiled.list_images"}:
            result = []
        else:
            result = None if method == "tiled.get_active_image" else {"error": f"Unsupported Tiled bridge method: {method}"}
        self.wfile.write((json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}) + "\n").encode())


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as server:
        server.serve_forever()
