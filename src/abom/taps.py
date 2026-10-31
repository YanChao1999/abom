"""Named catalog taps: public core trust root plus private overlays."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

TAPS_SCHEMA = "abom.taps/v1"
NAME_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class TapError(Exception):
    pass


def abom_home() -> Path:
    root = os.environ.get("ABOM_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".abom").resolve(strict=False)


def taps_path() -> Path:
    return abom_home() / "taps.json"


def load_tap_entries() -> list[dict]:
    path = taps_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TapError(f"could not read taps '{path}': {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != TAPS_SCHEMA:
        raise TapError(f"taps '{path}' must set schema to {TAPS_SCHEMA}")
    taps = data.get("taps")
    if not isinstance(taps, list):
        raise TapError(f"taps '{path}' must contain a taps list")
    out: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(taps):
        if not isinstance(entry, dict):
            raise TapError(f"tap entry {index} must be an object")
        name = entry.get("name")
        kind = entry.get("kind")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise TapError(f"tap entry {index} has an invalid name")
        if name in seen:
            raise TapError(f"duplicate tap '{name}'")
        seen.add(name)
        if kind != "path":
            raise TapError(f"tap '{name}' kind must be 'path' for now")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise TapError(f"tap '{name}' is missing path")
        out.append({"name": name, "kind": "path", "path": raw_path.strip()})
    return out


def write_tap_entries(entries: list[dict]) -> None:
    path = taps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": TAPS_SCHEMA, "taps": entries}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def add_path_tap(name: str, catalog: Path) -> str:
    if not NAME_RE.fullmatch(name):
        raise TapError(
            f"tap name '{name}' must be lowercase letters, numbers, and . _ - separators"
        )
    if name == "core":
        raise TapError("tap name 'core' is reserved for the bundled public catalog")
    resolved = catalog.expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise TapError(f"catalog '{resolved}' is not a file")
    entries = load_tap_entries()
    for entry in entries:
        if entry["name"] == name:
            raise TapError(f"tap '{name}' already exists")
    entries.append({"name": name, "kind": "path", "path": str(resolved)})
    write_tap_entries(entries)
    return f"added tap {name} -> {resolved}"


def remove_tap(name: str) -> str:
    if name == "core":
        raise TapError("cannot remove the bundled core tap")
    entries = load_tap_entries()
    kept = [entry for entry in entries if entry["name"] != name]
    if len(kept) == len(entries):
        raise TapError(f"no tap named '{name}'")
    write_tap_entries(kept)
    return f"removed tap {name}"


def registered_tap_sources() -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for entry in load_tap_entries():
        sources.append((entry["name"], Path(entry["path"]).expanduser()))
    return sources
