"""Discover the nearest project workspace from the current path (uv-style)."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_SCHEMA = "abom.project/v1"
PROJECT_DIR = ".abom"
PROJECT_FILE = "project.json"

# Nearest ancestor wins. Prefer abom/agent markers before generic VCS roots.
WORKSPACE_MARKERS = (
    "abom.lock.json",
    f"{PROJECT_DIR}/{PROJECT_FILE}",
    PROJECT_DIR,
    ".cursor",
    ".claude",
    ".codex",
    ".agents",
    ".git",
)


class ProjectError(Exception):
    pass


def find_workspace(start: Path | None = None) -> Path:
    """Return the nearest workspace root containing *start* (cwd by default).

    Walks parents until a marker is found. If none match, returns *start* itself
    when it is an existing directory — the minimal workspace is the current path.
    """
    current = (start or Path.cwd()).expanduser().resolve(strict=False)
    if current.is_file():
        current = current.parent
    if not current.is_dir():
        raise ProjectError(f"path '{current}' is not a directory")

    for candidate in (current, *current.parents):
        for marker in WORKSPACE_MARKERS:
            if (candidate / marker).exists():
                return candidate
    return current


def project_config_path(workspace: Path) -> Path:
    return workspace / PROJECT_DIR / PROJECT_FILE


def load_project(workspace: Path) -> dict:
    path = project_config_path(workspace)
    if not path.is_file():
        return {"schema": PROJECT_SCHEMA, "agent": None, "tools": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"could not read project config '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError(f"project config '{path}' must be a JSON object")
    if data.get("schema") != PROJECT_SCHEMA:
        raise ProjectError(f"project config '{path}' must set schema to {PROJECT_SCHEMA}")
    tools = data.get("tools")
    if tools is None:
        tools = {}
    if not isinstance(tools, dict):
        raise ProjectError(f"project config '{path}' tools must be an object")
    agent = data.get("agent")
    if agent is not None and (not isinstance(agent, str) or not agent.strip()):
        raise ProjectError(f"project config '{path}' agent must be a string")
    return {
        "schema": PROJECT_SCHEMA,
        "agent": agent.strip() if isinstance(agent, str) else None,
        "tools": tools,
    }


def tool_wire(workspace: Path, name: str) -> dict:
    data = load_project(workspace)
    entry = data["tools"].get(name)
    if not isinstance(entry, dict):
        return {}
    out: dict = {}
    agent = entry.get("agent")
    if isinstance(agent, str) and agent.strip():
        out["agent"] = agent.strip()
    link_name = entry.get("link_name")
    if isinstance(link_name, str) and link_name.strip():
        out["link_name"] = link_name.strip()
    if entry.get("global") is True:
        out["global"] = True
    return out


def record_tool_wire(
    workspace: Path,
    name: str,
    *,
    agent: str,
    link_name: str | None = None,
    global_scope: bool = False,
    default_agent: str | None = None,
) -> None:
    data = load_project(workspace)
    tools = dict(data["tools"])
    entry: dict = {"agent": agent}
    if link_name:
        entry["link_name"] = link_name
    if global_scope:
        entry["global"] = True
    tools[name] = entry
    payload = {
        "schema": PROJECT_SCHEMA,
        "agent": default_agent or data.get("agent") or agent,
        "tools": tools,
    }
    path = project_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
