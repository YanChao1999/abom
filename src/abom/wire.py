"""Wire installed materials into agent skill and MCP locations."""

from __future__ import annotations

import json
import os
from pathlib import Path

SKILL_TARGETS = {
    "cursor": (".cursor/skills",),
    "claude": (".claude/skills",),
    "copilot": (".github/skills",),
    "agents": (".agents/skills",),
}

MCP_FILES = {
    "cursor": ".cursor/mcp.json",
    "claude": ".mcp.json",
}


class WireError(Exception):
    pass


def skill_dirs(agent: str, project: Path) -> list[Path]:
    if agent == "all":
        names = ("cursor", "claude", "copilot", "agents")
    elif agent in SKILL_TARGETS:
        names = (agent,)
    else:
        raise WireError(f"unknown agent '{agent}'")
    return [project.joinpath(*rel.split("/")) for name in names for rel in SKILL_TARGETS[name]]


def mcp_config_path(agent: str, project: Path, *, global_scope: bool) -> Path:
    if agent not in MCP_FILES and agent != "all":
        raise WireError(f"unknown agent '{agent}'")
    if agent == "all":
        agent = "cursor"
    relative = MCP_FILES[agent]
    if global_scope:
        home = Path.home()
        if agent == "cursor":
            return home / ".cursor" / "mcp.json"
        return home / ".mcp.json"
    return project.joinpath(*relative.split("/"))


def link_skill(material: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() and destination.resolve() == material.resolve():
            return f"skill already linked -> {destination}"
        raise WireError(f"skill link '{destination}' already exists")
    destination.symlink_to(
        os.path.relpath(material, destination.parent),
        target_is_directory=material.is_dir(),
    )
    return f"linked skill -> {destination}"


def mcp_server_entry(name: str, material: Path) -> dict:
    """Build a Cursor/Claude mcpServers entry for an installed checkout."""
    package_json = material / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WireError(f"could not read package.json: {exc}") from exc
        package_name = data.get("name") if isinstance(data, dict) else None
        if isinstance(package_name, str) and package_name.strip():
            return {"command": "npx", "args": ["-y", package_name.strip()]}
        index_js = material / "index.js"
        if index_js.is_file():
            return {"command": "node", "args": [str(index_js.resolve())]}
        raise WireError(f"mcp '{name}' has package.json but no package name or index.js to launch")

    for entry in ("server.py", "main.py"):
        script = material / entry
        if script.is_file():
            return {"command": "python", "args": [str(script.resolve())]}

    if (material / "pyproject.toml").is_file():
        raise WireError(
            f"mcp '{name}' looks like a Python package; install it and set the module command by hand"
        )
    raise WireError(f"mcp '{name}' has no package.json, server.py, or main.py to launch")


def merge_mcp_server(config_path: Path, name: str, entry: dict) -> str:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WireError(f"could not read '{config_path}': {exc}") from exc
        if not isinstance(loaded, dict):
            raise WireError(f"'{config_path}' must be a JSON object")
        data = loaded
    else:
        data = {}
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise WireError(f"'{config_path}' mcpServers must be an object")
    previous = servers.get(name)
    servers[name] = entry
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if previous == entry:
        return f"mcp already configured -> {config_path}"
    return f"configured mcp -> {config_path}"
