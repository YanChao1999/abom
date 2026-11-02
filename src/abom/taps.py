"""Named catalog taps: public core trust root plus private overlays."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

TAPS_SCHEMA = "abom.taps/v1"
NAME_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class TapError(Exception):
    pass


def abom_home() -> Path:
    root = os.environ.get("ABOM_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".abom").resolve(strict=False)


def taps_path() -> Path:
    return abom_home() / "taps.json"


def tap_cache_dir(name: str) -> Path:
    return abom_home() / "tap-cache" / name


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
        if kind == "path":
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise TapError(f"tap '{name}' is missing path")
            out.append({"name": name, "kind": "path", "path": raw_path.strip()})
        elif kind == "url":
            url = entry.get("url")
            if not isinstance(url, str) or not URL_RE.match(url.strip()):
                raise TapError(f"tap '{name}' needs an http(s) url")
            out.append({"name": name, "kind": "url", "url": url.strip()})
        elif kind == "git":
            git = entry.get("git")
            ref = entry.get("ref", "main")
            catalog_path = entry.get("path", "recipes.json")
            if not isinstance(git, str) or not git.strip():
                raise TapError(f"tap '{name}' is missing git")
            if not isinstance(ref, str) or not ref.strip():
                raise TapError(f"tap '{name}' ref must be a non-empty string")
            if not isinstance(catalog_path, str) or not catalog_path.strip():
                raise TapError(f"tap '{name}' path must be a non-empty string")
            out.append(
                {
                    "name": name,
                    "kind": "git",
                    "git": git.strip(),
                    "ref": ref.strip(),
                    "path": catalog_path.strip(),
                }
            )
        else:
            raise TapError(f"tap '{name}' kind must be path, url, or git")
    return out


def write_tap_entries(entries: list[dict]) -> None:
    path = taps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": TAPS_SCHEMA, "taps": entries}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise TapError(
            f"tap name '{name}' must be lowercase letters, numbers, and . _ - separators"
        )
    if name == "core":
        raise TapError("tap name 'core' is reserved for the bundled public catalog")


def add_path_tap(name: str, catalog: Path) -> str:
    _validate_name(name)
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


def add_url_tap(name: str, url: str) -> str:
    _validate_name(name)
    cleaned = url.strip()
    if not URL_RE.match(cleaned):
        raise TapError(f"tap url must be http(s): {url}")
    entries = load_tap_entries()
    for entry in entries:
        if entry["name"] == name:
            raise TapError(f"tap '{name}' already exists")
    entries.append({"name": name, "kind": "url", "url": cleaned})
    write_tap_entries(entries)
    resolve_tap_catalog(entries[-1], refresh=True)
    return f"added tap {name} -> {cleaned}"


def add_git_tap(name: str, git: str, *, ref: str = "main", path: str = "recipes.json") -> str:
    _validate_name(name)
    entries = load_tap_entries()
    for entry in entries:
        if entry["name"] == name:
            raise TapError(f"tap '{name}' already exists")
    entry = {
        "name": name,
        "kind": "git",
        "git": git.strip(),
        "ref": ref.strip() or "main",
        "path": path.strip() or "recipes.json",
    }
    entries.append(entry)
    write_tap_entries(entries)
    resolve_tap_catalog(entry, refresh=True)
    return f"added tap {name} -> {entry['git']}@{entry['ref']}:{entry['path']}"


def add_tap(name: str, source: str, *, ref: str = "main", path: str = "recipes.json") -> str:
    """Register a tap. Source may be a local file, http(s) URL, or git URL/repo."""
    candidate = Path(source).expanduser()
    if candidate.is_file():
        return add_path_tap(name, candidate)
    if candidate.is_dir() and (candidate / ".git").exists():
        return add_git_tap(name, str(candidate.resolve()), ref=ref, path=path)
    cleaned = source.strip()
    if URL_RE.match(cleaned) and cleaned.rstrip("/").endswith(".json"):
        return add_url_tap(name, cleaned)
    if URL_RE.match(cleaned) or cleaned.endswith(".git") or cleaned.startswith("git@"):
        return add_git_tap(name, cleaned, ref=ref, path=path)
    raise TapError(
        f"cannot determine tap kind for '{source}'; use a local JSON file, "
        "https://…/catalog.json, a local git checkout, or a git URL"
    )


def remove_tap(name: str) -> str:
    if name == "core":
        raise TapError("cannot remove the bundled core tap")
    entries = load_tap_entries()
    kept = [entry for entry in entries if entry["name"] != name]
    if len(kept) == len(entries):
        raise TapError(f"no tap named '{name}'")
    write_tap_entries(kept)
    cache = tap_cache_dir(name)
    if cache.exists():
        shutil.rmtree(cache)
    return f"removed tap {name}"


def resolve_tap_catalog(entry: dict, *, refresh: bool = False) -> Path:
    kind = entry["kind"]
    name = entry["name"]
    if kind == "path":
        path = Path(entry["path"]).expanduser()
        if not path.is_file():
            raise TapError(f"tap '{name}' catalog '{path}' is not a file")
        return path

    cache = tap_cache_dir(name)
    catalog_file = cache / "catalog.json"
    if kind == "url":
        if refresh or not catalog_file.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            _fetch_url(entry["url"], catalog_file)
        return catalog_file

    if kind == "git":
        repo = cache / "repo"
        relative = entry.get("path") or "recipes.json"
        if refresh or not repo.is_dir():
            if cache.exists():
                shutil.rmtree(cache)
            cache.mkdir(parents=True, exist_ok=True)
            _clone_git(entry["git"], entry.get("ref") or "main", repo)
        catalog = repo / relative
        if not catalog.is_file():
            raise TapError(f"tap '{name}' path '{relative}' was not found in the clone")
        return catalog

    raise TapError(f"tap '{name}' has unsupported kind '{kind}'")


def update_taps(name: str | None = None) -> list[str]:
    lines: list[str] = []
    for entry in load_tap_entries():
        if name is not None and entry["name"] != name:
            continue
        if entry["kind"] == "path":
            lines.append(f"unchanged {entry['name']} (path)")
            continue
        path = resolve_tap_catalog(entry, refresh=True)
        lines.append(f"updated {entry['name']} -> {path}")
    if name is not None and not any(line.startswith(("updated ", "unchanged ")) for line in lines):
        raise TapError(f"no tap named '{name}'")
    if not lines:
        lines.append("no remote taps to update")
    return lines


def registered_tap_sources() -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for entry in load_tap_entries():
        sources.append((entry["name"], resolve_tap_catalog(entry, refresh=False)))
    return sources


def _fetch_url(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "abom-tap/1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TapError(f"could not fetch tap url '{url}': {exc}") from exc
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TapError(f"tap url '{url}' did not return JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "abom.catalog/v1":
        raise TapError(f"tap url '{url}' must be an abom.catalog/v1 JSON document")
    destination.write_bytes(body if body.endswith(b"\n") else body + b"\n")


def _clone_git(git_url: str, ref: str, destination: Path) -> None:
    try:
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "protocol.file.allow=user",
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                git_url,
                str(destination),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise TapError(stderr or f"git clone failed for '{git_url}'") from exc
