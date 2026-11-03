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
    "codex": (".codex/skills",),
}

PROMPT_TARGETS = {
    "cursor": (".cursor/prompts",),
    "claude": (".claude/prompts",),
    "copilot": (".github/prompts",),
    "agents": (".agents/prompts",),
    "codex": (".codex/prompts",),
}

MCP_FILES = {
    "cursor": ".cursor/mcp.json",
    "claude": ".mcp.json",
}


class WireError(Exception):
    pass


def skill_dirs(agent: str, project: Path) -> list[Path]:
    return _agent_dirs(agent, project, SKILL_TARGETS)


def prompt_dirs(agent: str, project: Path) -> list[Path]:
    return _agent_dirs(agent, project, PROMPT_TARGETS)


def _agent_dirs(agent: str, project: Path, mapping: dict[str, tuple[str, ...]]) -> list[Path]:
    if agent == "all":
        names = tuple(mapping)
    elif agent in mapping:
        names = (agent,)
    else:
        raise WireError(f"unknown agent '{agent}'")
    return [project.joinpath(*rel.split("/")) for name in names for rel in mapping[name]]


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
    return _link_path(material, destination, label="skill")


def link_prompt(material: Path, destination: Path) -> str:
    return _link_path(material, destination, label="prompt")


def _link_path(material: Path, destination: Path, *, label: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.resolve() == material.resolve():
            return f"{label} already linked -> {destination}"
        destination.unlink()
    elif destination.exists():
        raise WireError(f"{label} link '{destination}' already exists")
    destination.symlink_to(
        os.path.relpath(material, destination.parent),
        target_is_directory=material.is_dir(),
    )
    return f"linked {label} -> {destination}"


def mcp_server_entry(
    name: str,
    material: Path,
    *,
    workspace: Path | None = None,
) -> dict:
    """Build a Cursor/Claude mcpServers entry that launches this checkout."""
    package_json = material / "package.json"
    package_name = name
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WireError(f"could not read package.json: {exc}") from exc
        if not isinstance(data, dict):
            raise WireError("package.json must be an object")
        raw_name = data.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            package_name = raw_name.strip()
        launch = _node_script(material, data)
        if launch is not None:
            entry = {"command": "node", "args": [str(launch.resolve())]}
            return _with_workspace(entry, package_name, workspace)

    for entry_name in ("server.py", "main.py"):
        script = material / entry_name
        if script.is_file() and not script.is_symlink():
            entry = {"command": "python", "args": [str(script.resolve())]}
            if workspace is not None:
                entry["cwd"] = str(workspace.resolve(strict=False))
            return entry

    if (material / "pyproject.toml").is_file():
        raise WireError(
            f"mcp '{name}' looks like a Python package; install it and set the module command by hand"
        )
    raise WireError(f"mcp '{name}' has no file in the checkout to launch")


def _node_script(material: Path, package: dict) -> Path | None:
    """Pick a file inside the checkout. Never fall back to a registry fetch."""
    relatives: list[str] = []
    bin_field = package.get("bin")
    if isinstance(bin_field, str):
        relatives.append(bin_field)
    elif isinstance(bin_field, dict):
        relatives.extend(value for value in bin_field.values() if isinstance(value, str))
    main = package.get("main")
    if isinstance(main, str):
        relatives.append(main)
    relatives.extend(("index.js", "index.mjs", "index.cjs", "index.ts"))
    for relative in relatives:
        cleaned = relative.strip()
        if not cleaned or cleaned.startswith(("/", "\\")):
            continue
        parts = Path(cleaned).parts
        if ".." in parts:
            continue
        candidate = material / cleaned
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _with_workspace(entry: dict, package_name: str, workspace: Path | None) -> dict:
    if workspace is None:
        return entry
    root = str(workspace.resolve(strict=False))
    entry = {**entry, "cwd": root}
    lowered = package_name.lower()
    if "filesystem" in lowered or lowered.endswith("-fs") or lowered.endswith("/fs"):
        args = list(entry.get("args") or [])
        if root not in args:
            args.append(root)
        entry["args"] = args
    return entry


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


def unlink_path(destination: Path, *, label: str) -> str:
    if not destination.exists() and not destination.is_symlink():
        return f"{label} not linked -> {destination}"
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
        return f"unlinked {label} -> {destination}"
    raise WireError(f"{label} path '{destination}' is not a symlink or file")


def remove_mcp_server(config_path: Path, name: str) -> str:
    if not config_path.is_file():
        return f"mcp not configured -> {config_path}"
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WireError(f"could not read '{config_path}': {exc}") from exc
    if not isinstance(loaded, dict):
        raise WireError(f"'{config_path}' must be a JSON object")
    servers = loaded.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return f"mcp not configured -> {config_path}"
    del servers[name]
    config_path.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
    return f"removed mcp -> {config_path}"
