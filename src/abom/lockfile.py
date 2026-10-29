"""Project lock file: pin installed recipe commits."""

from __future__ import annotations

import json
from pathlib import Path

LOCK_SCHEMA = "abom.lock/v1"
DEFAULT_LOCK_NAME = "abom.lock.json"


class LockError(Exception):
    pass


def default_lock_path(root: Path | None = None) -> Path:
    return (root or Path.cwd()) / DEFAULT_LOCK_NAME


def read_lock(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LockError(f"could not read lock file '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise LockError(f"lock file '{path}' must be a JSON object")
    if data.get("schema") != LOCK_SCHEMA:
        raise LockError(f"lock file '{path}' must set schema to {LOCK_SCHEMA}")
    tools = data.get("tools")
    if not isinstance(tools, dict):
        raise LockError(f"lock file '{path}' is missing a tools object")
    out: dict[str, dict] = {}
    for name, entry in tools.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise LockError(f"lock file '{path}' has an invalid tools entry")
        commit = entry.get("commit")
        if not isinstance(commit, str) or not commit.strip():
            raise LockError(f"lock entry '{name}' is missing commit")
        out[name] = {
            "commit": commit.strip(),
            "git": entry.get("git"),
            "ref": entry.get("ref"),
            "path": entry.get("path"),
            "kind": entry.get("kind"),
        }
    return out


def write_lock(path: Path, tools: dict[str, dict]) -> None:
    payload = {
        "schema": LOCK_SCHEMA,
        "tools": {
            name: {
                key: value
                for key, value in {
                    "commit": entry["commit"],
                    "git": entry.get("git"),
                    "ref": entry.get("ref"),
                    "path": entry.get("path"),
                    "kind": entry.get("kind"),
                }.items()
                if value is not None
            }
            for name, entry in sorted(tools.items())
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
