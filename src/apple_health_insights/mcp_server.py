"""Stdio MCP server. The tool list is the four aggregates-only health tools.

Launch with ``python3 -m apple_health_insights.mcp_server``. Logs stay off
stdout so they cannot mix with JSON-RPC.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

from apple_health_insights import __version__
from apple_health_insights.mcp_tools import (
    health_inbox_status,
    health_ingest_latest,
    health_insights,
    health_overview,
)

SERVER_NAME = "apple-health-insights"
TOOL_NAMES = (
    "health_inbox_status",
    "health_ingest_latest",
    "health_overview",
    "health_insights",
)
_HANDLERS = {
    "health_inbox_status": health_inbox_status,
    "health_ingest_latest": health_ingest_latest,
    "health_overview": health_overview,
    "health_insights": health_insights,
}
_PROTOCOL = "2024-11-05"


def tools() -> list[dict]:
    return [
        {
            "name": "health_inbox_status",
            "description": (
                "List Apple Health export zips under Health Data. "
                "Uses Drive when HEALTH_DRIVE_ACCESS_TOKEN or GOOGLE_ACCESS_TOKEN is set; "
                "otherwise a local inbox path, or an empty status. "
                "Returns id, modifiedTime, size, and a sanitized label. No file bodies."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "inbox_path": {
                        "type": "string",
                        "description": "Local directory of zips. Used only when Drive credentials are absent.",
                    },
                    "out": {
                        "type": "string",
                        "description": "Private directory that holds the ingest manifest. Default state/health.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "health_ingest_latest",
            "description": (
                "Ingest the newest unprocessed export by running "
                "python -m apple_health_insights ingest. "
                "A path argument reads a local zip, export.xml, or folder. "
                "With Drive credentials, downloads the newest unprocessed zip to private scratch, "
                "runs that command, and deletes the Drive zip only after the command exits 0. "
                "Returns status, counts, and the overview path. Never returns XML or zip bytes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Local zip, export.xml, or folder. CI passes the synthetic fixture only.",
                    },
                    "inbox_path": {
                        "type": "string",
                        "description": "Local inbox used when path and Drive credentials are both absent.",
                    },
                    "out": {
                        "type": "string",
                        "description": "Private overview directory. Default state/health or HEALTH_STATE_DIR.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Pass --force through to the ingest CLI.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "health_overview",
            "description": (
                "Read the latest private overview as structured aggregates and short markdown. "
                "Does not return per-sample series, record attributes, GPS, or notes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "out": {
                        "type": "string",
                        "description": "Private overview directory. Default state/health or HEALTH_STATE_DIR.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "health_insights",
            "description": (
                "Return ranked insights from the private overview. Version 1 returns at most one, "
                "and never more than three. Aggregates only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "out": {
                        "type": "string",
                        "description": "Private overview directory. Default state/health or HEALTH_STATE_DIR.",
                    }
                },
                "additionalProperties": False,
            },
        },
    ]


def handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if not isinstance(method, str):
        if msg_id is None:
            return None
        return _error(msg_id, -32600, "Invalid request")
    if method == "notifications/initialized":
        return None
    if msg_id is None:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and requested[:4] in {"2024", "2025"} else _PROTOCOL
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": tools()})
    if method == "tools/call":
        name = params.get("name")
        if name not in _HANDLERS:
            return _error(msg_id, -32602, "Unknown tool")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(msg_id, -32602, "Invalid arguments")
        arguments = {key: value for key, value in arguments.items() if key in _allowed(name)}
        try:
            payload = _HANDLERS[name](arguments)
        except Exception:
            payload = {"status": "error", "message": "Tool failed."}
        is_error = bool(payload.get("refused") or payload.get("status") == "error")
        return _result(msg_id, _tool_result(payload, is_error=is_error))
    return _error(msg_id, -32601, "Method not found")


def serve(stdin: TextIO, stdout: TextIO) -> None:
    for message in _messages(stdin):
        response = handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
        stdout.flush()


def main() -> None:
    serve(sys.stdin, sys.stdout)


def _messages(stdin: TextIO):
    while True:
        line = stdin.readline()
        if line == "":
            return
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                header = stdin.readline()
                if header == "":
                    return
                if header.strip() == "":
                    break
            body = stdin.read(length)
            if not body:
                return
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload
            continue
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _allowed(name: str) -> set[str]:
    for tool in tools():
        if tool["name"] == name:
            return set(tool["inputSchema"]["properties"])
    return set()


def _tool_result(payload: dict, *, is_error: bool) -> dict:
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _result(msg_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
