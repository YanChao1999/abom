"""Discover the nearest project workspace from the current path (uv-style)."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_SCHEMA = "abom.project/v1"
PROJECT_DIR = ".abom"
PROJECT_FILE = "project.json"
AGENT_NAMES = ("cursor", "claude", "copilot", "agents", "codex", "all")

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


def parse_agents(value: object, *, label: str = "project") -> list[str]:
    """Return agent names. A single string and a list are both accepted."""
    if value is None:
        return []
    if isinstance(value, str):
        items: list[object] = [value]
    elif isinstance(value, list):
        items = list(value)
    else:
        raise ProjectError(f"{label} agent must be a string or a list of strings")
    cleaned: list[str] = []
    allowed = ", ".join(AGENT_NAMES)
    for item in items:
        if not isinstance(item, str) or item.strip() not in AGENT_NAMES:
            raise ProjectError(f"{label} agent '{item}' must be one of: {allowed}")
        name = item.strip()
        if name not in cleaned:
            cleaned.append(name)
    if "all" in cleaned:
        return ["all"]
    return cleaned


def store_agent(agents: list[str]) -> str | list[str] | None:
    if not agents:
        return None
    if len(agents) == 1:
        return agents[0]
    return list(agents)


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
    return {
        "schema": PROJECT_SCHEMA,
        "agent": store_agent(parse_agents(data.get("agent"), label=str(path))),
        "tools": tools,
    }


def tool_wire(workspace: Path, name: str) -> dict:
    data = load_project(workspace)
    entry = data["tools"].get(name)
    if not isinstance(entry, dict):
        return {}
    out: dict = {}
    if entry.get("agent") is not None:
        agents = parse_agents(entry.get("agent"), label=name)
        stored = store_agent(agents)
        if stored is not None:
            out["agent"] = stored
    link_name = entry.get("link_name")
    if isinstance(link_name, str) and link_name.strip():
        out["link_name"] = link_name.strip()
    if entry.get("global") is True:
        out["global"] = True
    commit = entry.get("commit")
    if isinstance(commit, str) and commit.strip():
        out["commit"] = commit.strip()
    return out


def write_project(workspace: Path, data: dict) -> None:
    path = project_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PROJECT_SCHEMA,
        "agent": data.get("agent"),
        "tools": data.get("tools") or {},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def init_project(workspace: Path | None = None, *, agents: list[str] | None = None) -> str:
    root = find_workspace(workspace) if workspace else find_workspace()
    path = project_config_path(root)
    selected = parse_agents(agents if agents is not None else ["cursor"], label="init")
    shown = ", ".join(selected)
    if path.is_file():
        if agents is None:
            return f"project already initialized -> {path}"
        data = load_project(root)
        write_project(root, {"agent": store_agent(selected), "tools": data["tools"]})
        return f"updated project agents -> {path} ({shown})"
    write_project(root, {"agent": store_agent(selected), "tools": {}})
    return f"initialized project -> {path} ({shown})"


def record_tool_wire(
    workspace: Path,
    name: str,
    *,
    agent: str | list[str],
    link_name: str | None = None,
    global_scope: bool = False,
    default_agent: str | None = None,
    commit: str | None = None,
) -> None:
    data = load_project(workspace)
    tools = dict(data["tools"])
    stored_agent = store_agent(parse_agents(agent, label=name))
    entry: dict = {"agent": stored_agent}
    if link_name:
        entry["link_name"] = link_name
    if global_scope:
        entry["global"] = True
    if commit:
        entry["commit"] = commit
    tools[name] = entry
    write_project(
        workspace,
        {
            "agent": default_agent or data.get("agent") or stored_agent,
            "tools": tools,
        },
    )


def remove_tool_wire(workspace: Path, name: str) -> bool:
    data = load_project(workspace)
    tools = dict(data["tools"])
    if name not in tools:
        return False
    del tools[name]
    write_project(workspace, {"agent": data.get("agent"), "tools": tools})
    return True
