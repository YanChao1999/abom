#!/usr/bin/env python3
"""stdio MCP server for the abom catalog.

Launch with `python mcp/server.py`. Messages are JSON-RPC, either one JSON
object per line or a Content-Length frame. Logs go to stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from abom import __version__
from abom.cli import (
    AbomError,
    cmd_check,
    cmd_install_recipe,
    cmd_link,
    cmd_recipes,
    cmd_remove,
    cmd_rollback,
    cmd_search,
    cmd_show,
    cmd_sync,
    cmd_tap_add,
    cmd_tap_list,
    cmd_tap_remove,
    cmd_upgrade,
    cmd_use,
)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "abom"

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_recipes",
        "description": "List abom recipes. Optionally filter by name, kind, or description.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "show_recipe",
        "description": "Show one abom recipe, whether it is installed, and the check result when it is installed.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_recipe",
        "description": "Verify an abom recipe's kind and install safety. Clones temporarily when it is not installed.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "install_recipe",
        "description": "Install an abom recipe by name after the kind and security check passes. Optionally wire it with use_target.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "use_target": {
                    "type": "string",
                    "description": "Project root to wire into; use '.' for the nearest workspace from cwd",
                },
                "agent": {
                    "type": "string",
                    "enum": ["cursor", "claude", "copilot", "agents", "codex", "all"],
                },
                "workspace": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_installed",
        "description": "List installed abom tools, optionally filtered by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "link_tool",
        "description": "Symlink an installed abom tool into a target repository's .abom directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target_repo": {"type": "string"},
                "link_name": {"type": "string"},
            },
            "required": ["name", "target_repo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "use_tool",
        "description": "Wire an installed skill, prompt, or MCP into Cursor, Claude, Copilot, agents, or Codex paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target_repo": {
                    "type": "string",
                    "description": "Project root; omit to use the nearest workspace from cwd",
                },
                "agent": {
                    "type": "string",
                    "enum": ["cursor", "claude", "copilot", "agents", "codex", "all"],
                },
                "link_name": {"type": "string"},
                "workspace": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "upgrade_tool",
        "description": "Upgrade one installed recipe checkout, or all of them when name is omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "rollback_tool",
        "description": "Restore an installed recipe to its previous receipt commit.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_tools",
        "description": "Install or reinstall tools so they match abom.lock.json.",
        "inputSchema": {
            "type": "object",
            "properties": {"lock_file": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_taps",
        "description": "List the public core trust root and registered private catalog taps.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "add_tap",
        "description": "Register a local catalog JSON file as a named private tap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "catalog": {"type": "string"},
            },
            "required": ["name", "catalog"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_tap",
        "description": "Remove a registered private catalog tap.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_tool",
        "description": "Remove an installed abom tool and its receipt.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    if not isinstance(method, str):
        return _error(message.get("id"), -32600, "invalid request")
    if method.startswith("notifications/") or "id" not in message:
        return None
    msg_id = message.get("id")
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "invalid params")

    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": _TOOLS})
    if method == "tools/call":
        return _result(msg_id, _call_tool(params))
    return _error(msg_id, -32601, f"method not found: {method}")


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _tool_error("tools/call requires a tool name and arguments object")
    try:
        text = _dispatch(name, arguments)
    except AbomError as exc:
        return _tool_error(str(exc))
    except KeyError as exc:
        return _tool_error(f"missing argument {exc}")
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _dispatch(name: str, arguments: dict[str, Any]) -> str:
    if name == "list_recipes":
        return "\n".join(cmd_recipes(arguments.get("query")))
    if name == "show_recipe":
        return cmd_show(_required(arguments, "name"))
    if name == "check_recipe":
        return cmd_check(_required(arguments, "name"))
    if name == "install_recipe":
        use_target = arguments.get("use_target")
        workspace = arguments.get("workspace")
        agent = arguments.get("agent")
        return cmd_install_recipe(
            _required(arguments, "name"),
            use_target=use_target if isinstance(use_target, str) else None,
            agent=agent if isinstance(agent, str) else None,
            workspace=workspace if isinstance(workspace, str) else None,
        )
    if name == "search_installed":
        found = cmd_search(arguments.get("query"))
        return "\n".join(found)
    if name == "link_tool":
        return cmd_link(
            _required(arguments, "name"),
            _required(arguments, "target_repo"),
            arguments.get("link_name"),
        )
    if name == "use_tool":
        target = arguments.get("target_repo")
        agent = arguments.get("agent")
        workspace = arguments.get("workspace")
        return cmd_use(
            _required(arguments, "name"),
            target if isinstance(target, str) else None,
            agent=agent if isinstance(agent, str) else None,
            link_name=arguments.get("link_name"),
            workspace=workspace if isinstance(workspace, str) else None,
        )
    if name == "upgrade_tool":
        name_arg = arguments.get("name")
        lines = cmd_upgrade(name_arg if isinstance(name_arg, str) else None)
        return "\n".join(lines)
    if name == "rollback_tool":
        return cmd_rollback(_required(arguments, "name"))
    if name == "sync_tools":
        lock_file = arguments.get("lock_file")
        path = Path(lock_file).expanduser() if isinstance(lock_file, str) and lock_file else None
        return "\n".join(cmd_sync(path))
    if name == "list_taps":
        return "\n".join(cmd_tap_list())
    if name == "add_tap":
        return cmd_tap_add(_required(arguments, "name"), _required(arguments, "catalog"))
    if name == "remove_tap":
        return cmd_tap_remove(_required(arguments, "name"))
    if name == "remove_tool":
        return cmd_remove(_required(arguments, "name"))
    raise AbomError(f"unknown tool '{name}'")


def _required(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KeyError(key)
    return value


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def read_message(stream: Any) -> dict[str, Any] | None:
    line = stream.readline()
    if not line:
        return None
    if line.lower().startswith(b"content-length:"):
        length = int(line.split(b":", 1)[1].strip())
        while True:
            header = stream.readline()
            if header in (b"\n", b"\r\n", b""):
                break
        body = stream.read(length)
        return json.loads(body.decode("utf-8"))
    text = line.decode("utf-8").strip()
    if not text:
        return read_message(stream)
    return json.loads(text)


def write_message(stream: Any, message: dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    stream.write(body)
    stream.flush()


def main() -> int:
    while True:
        try:
            message = read_message(sys.stdin.buffer)
        except json.JSONDecodeError:
            write_message(sys.stdout.buffer, _error(None, -32700, "parse error"))
            continue
        if message is None:
            return 0
        response = handle_message(message)
        if response is not None:
            write_message(sys.stdout.buffer, response)


if __name__ == "__main__":
    raise SystemExit(main())
