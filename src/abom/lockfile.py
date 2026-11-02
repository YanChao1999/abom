"""Project lock file: pin installed recipe commits and material digests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCK_SCHEMA = "abom.lock/v1"
DEFAULT_LOCK_NAME = "abom.lock.json"


class LockError(Exception):
    pass


def default_lock_path(root: Path | None = None) -> Path:
    return (root or Path.cwd()) / DEFAULT_LOCK_NAME


def material_digest(root: Path) -> str:
    """Stable sha256 over file paths and contents under *root*, skipping .git."""
    digest = hashlib.sha256()
    if not root.exists():
        raise LockError(f"material path '{root}' does not exist")
    files: list[Path] = []
    if root.is_file():
        files = [root]
        base = root.parent
    else:
        base = root
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
            if parts and parts[0] == ".git":
                continue
            files.append(path)
    for path in files:
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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
        digest = entry.get("digest")
        out[name] = {
            "commit": commit.strip(),
            "git": entry.get("git"),
            "ref": entry.get("ref"),
            "path": entry.get("path"),
            "kind": entry.get("kind"),
            "digest": digest.strip() if isinstance(digest, str) and digest.strip() else None,
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
                    "digest": entry.get("digest"),
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
