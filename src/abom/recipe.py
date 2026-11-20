from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

SCHEMA = "abom.catalog/v1"
DEFAULT_CATALOG_URL = "https://yanchao1999.github.io/abom/recipes.json"
_CATALOG_TIMEOUT = 5
_DEFAULT_CATALOG_TTL = 3600.0
KINDS = ("skill", "prompt", "mcp", "package")
NAME_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
ALLOWED_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "CC0-1.0",
        "MPL-2.0",
        "Unlicense",
        "0BSD",
    }
)


class RecipeError(Exception):
    pass


@dataclass(frozen=True)
class Recipe:
    name: str
    kind: str
    description: str
    homepage: str
    license: str
    git: str
    ref: str
    path: str | None
    catalog: str | None = None
    tap: str | None = None


def bundled_catalog_path() -> Path:
    return Path(__file__).resolve().parent / "recipes.json"


def _abom_home() -> Path:
    root = os.environ.get("ABOM_HOME")
    return Path(root).expanduser() if root else Path.home() / ".abom"


def private_catalogs_dir() -> Path:
    return _abom_home() / "catalogs"


def catalog_url() -> str | None:
    """HTTPS catalog to refresh, or None when remote refresh is off.

    Unset ``ABOM_CATALOG_URL`` uses the public Pages catalog. An empty value
    keeps the packaged copy.
    """
    if "ABOM_CATALOG_URL" in os.environ:
        raw = os.environ["ABOM_CATALOG_URL"].strip()
        if not raw:
            return None
        if not raw.lower().startswith("https://"):
            raise RecipeError("ABOM_CATALOG_URL must be an https URL")
        return raw
    return DEFAULT_CATALOG_URL


def catalog_path() -> Path:
    """Primary catalog path. Prefer load_catalog() for the merged view."""
    override = os.environ.get("ABOM_CATALOG")
    if override:
        return Path(override).expanduser()
    return core_catalog_file()


def core_catalog_file() -> Path:
    """Packaged catalog, or the cached remote catalog when that document is usable."""
    url = catalog_url()
    if url is None:
        return bundled_catalog_path()
    return _resolve_remote_core(url)


def catalog_sources() -> list[tuple[str, Path]]:
    """Named catalog sources in merge order. Later taps override earlier names."""
    override = os.environ.get("ABOM_CATALOG")
    if override:
        return [("override", Path(override).expanduser())]

    sources: list[tuple[str, Path]] = [("core", core_catalog_file())]
    try:
        from abom.taps import TapError, registered_tap_sources

        sources.extend(registered_tap_sources())
    except TapError as exc:
        raise RecipeError(str(exc)) from exc
    private = private_catalogs_dir()
    if private.is_dir():
        for path in sorted(private.glob("*.json")):
            sources.append((f"local/{path.stem}", path))
    extra = os.environ.get("ABOM_EXTRA_CATALOGS", "")
    for index, part in enumerate(extra.split(os.pathsep)):
        part = part.strip()
        if part:
            sources.append((f"extra/{index}", Path(part).expanduser()))
    return sources


def catalog_paths() -> list[Path]:
    """Catalog files in merge order. Later files override earlier names."""
    return [path for _name, path in catalog_sources()]


def load_catalog(path: Path | None = None) -> dict[str, Recipe]:
    if path is not None:
        return _load_one_catalog(path)
    merged: dict[str, Recipe] = {}
    for tap_name, catalog_file in catalog_sources():
        for name, recipe in _load_one_catalog(catalog_file).items():
            merged[name] = replace(recipe, catalog=str(catalog_file), tap=tap_name)
    return merged


def _load_one_catalog(catalog_file: Path) -> dict[str, Recipe]:
    try:
        text = catalog_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeError(f"could not read recipe catalog '{catalog_file}': {exc}") from exc
    recipes, _entries = parse_catalog(text, str(catalog_file))
    return recipes


def compatible_catalog_text(remote_text: str, bundled_text: str) -> str | None:
    """Return a v1 catalog using remote entries this client can parse.

    A document with another schema is refused. An entry this client cannot
    parse keeps the packaged recipe of the same name when that name exists.
    """
    try:
        remote = json.loads(remote_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(remote, dict) or remote.get("schema") != SCHEMA:
        return None
    recipes = remote.get("recipes")
    if not isinstance(recipes, list):
        return None
    _bundled, bundled_raw = parse_catalog(bundled_text, "bundled")
    selected: dict[str, dict] = {}
    order: list[str] = []
    for entry in recipes:
        if not isinstance(entry, dict):
            continue
        try:
            recipe = recipe_from_mapping(entry)
        except RecipeError:
            name = entry.get("name")
            if not isinstance(name, str) or name in selected:
                continue
            fallback = bundled_raw.get(name)
            if fallback is None:
                continue
            selected[name] = fallback
            order.append(name)
            continue
        if recipe.name not in order:
            order.append(recipe.name)
        selected[recipe.name] = entry
    document = {"schema": SCHEMA, "recipes": [selected[name] for name in order]}
    return json.dumps(document, indent=2) + "\n"


def _catalog_ttl() -> float:
    raw = os.environ.get("ABOM_CATALOG_TTL", str(int(_DEFAULT_CATALOG_TTL)))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_CATALOG_TTL


def _cache_paths() -> tuple[Path, Path]:
    cache = _abom_home() / "catalog-cache" / "core.json"
    return cache, cache.with_suffix(".url")


def _cache_is_catalog(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("schema") == SCHEMA
        and isinstance(data.get("recipes"), list)
    )


def _cache_is_fresh(cache: Path, marker: Path, url: str) -> bool:
    ttl = _catalog_ttl()
    if ttl == 0:
        return False
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
        age = time.time() - cache.stat().st_mtime
    except OSError:
        return False
    return recorded == url and age >= 0 and age < ttl and _cache_is_catalog(cache)


def _write_cache(cache: Path, marker: Path, url: str, text: str) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".json.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(cache)
    marker.write_text(url + "\n", encoding="utf-8")


def _fetch_catalog_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "abom-catalog/1"})
    try:
        with urllib.request.urlopen(request, timeout=_CATALOG_TIMEOUT) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as exc:
        raise RecipeError(f"could not fetch recipe catalog '{url}': {exc}") from exc


def refresh_core_catalog() -> str:
    """Fetch the remote core catalog immediately, ignoring the cache TTL."""
    url = catalog_url()
    if url is None:
        text = bundled_catalog_path().read_text(encoding="utf-8")
        count = len(parse_catalog(text, "bundled")[0])
        return f"core  packaged  {count} recipes"
    cache, marker = _cache_paths()
    remote_text = _fetch_catalog_text(url)
    bundled_text = bundled_catalog_path().read_text(encoding="utf-8")
    resolved = compatible_catalog_text(remote_text, bundled_text)
    if resolved is None:
        raise RecipeError(f"recipe catalog '{url}' is not an {SCHEMA} document this client can use")
    _write_cache(cache, marker, url, resolved)
    count = len(json.loads(resolved)["recipes"])
    return f"updated core  {url}  {count} recipes"


def _resolve_remote_core(url: str) -> Path:
    cache, marker = _cache_paths()
    if _cache_is_fresh(cache, marker, url):
        return cache
    try:
        remote_text = _fetch_catalog_text(url)
    except RecipeError:
        if _cache_is_catalog(cache) and _cache_url(marker) == url:
            return cache
        return bundled_catalog_path()
    try:
        bundled_text = bundled_catalog_path().read_text(encoding="utf-8")
        resolved = compatible_catalog_text(remote_text, bundled_text)
    except (OSError, RecipeError):
        resolved = None
    if resolved is None:
        if _cache_is_catalog(cache) and _cache_url(marker) == url:
            return cache
        return bundled_catalog_path()
    _write_cache(cache, marker, url, resolved)
    return cache


def _cache_url(marker: Path) -> str | None:
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def parse_catalog(text: str, label: str = "catalog") -> tuple[dict[str, Recipe], dict[str, dict]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecipeError(f"recipe catalog '{label}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeError(f"recipe catalog '{label}' must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise RecipeError(f"recipe catalog '{label}' must set schema to {SCHEMA}")

    entries = data.get("recipes")
    if not isinstance(entries, list):
        raise RecipeError(f"recipe catalog '{label}' is missing a recipes list")

    found: dict[str, Recipe] = {}
    raw: dict[str, dict] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RecipeError(f"recipe catalog entry {index} must be an object")
        recipe = recipe_from_mapping(entry)
        if recipe.name in found:
            raise RecipeError(f"duplicate recipe '{recipe.name}' in {label}")
        found[recipe.name] = recipe
        raw[recipe.name] = entry
    return found, raw


def recipe_changes(base_text: str | None, head_text: str) -> list[tuple[str, Recipe]]:
    """Return added or updated recipes. Both catalogs must validate when present."""
    head, head_entries = parse_catalog(head_text, "head")
    base_entries: dict[str, dict] = {}
    if base_text is not None and base_text.strip():
        _base, base_entries = parse_catalog(base_text, "base")
    changes: list[tuple[str, Recipe]] = []
    for name, recipe in sorted(head.items()):
        previous = base_entries.get(name)
        if previous is None:
            changes.append(("added", recipe))
        elif previous != head_entries[name]:
            changes.append(("updated", recipe))
    return changes


def load_recipe(name: str, path: Path | None = None) -> Recipe:
    catalog = load_catalog(path)
    recipe = catalog.get(name)
    if recipe is None:
        raise RecipeError(f"no recipe named '{name}' in {path or catalog_path()}")
    return recipe


def iter_recipes(query: str | None = None, path: Path | None = None) -> list[Recipe]:
    recipes = list(load_catalog(path).values())
    if query:
        needle = query.lower()
        recipes = [
            recipe
            for recipe in recipes
            if needle in f"{recipe.name} {recipe.kind} {recipe.description}".lower()
        ]
    return sorted(recipes, key=lambda recipe: recipe.name)


def recipe_from_mapping(data: dict) -> Recipe:
    name = _required_string(data, "name", "recipe")
    if not NAME_RE.fullmatch(name):
        raise RecipeError(
            f"recipe name '{name}' must be lowercase letters, numbers, and . _ - separators"
        )

    kind = _required_string(data, "kind", name)
    if kind not in KINDS:
        raise RecipeError(f"recipe '{name}' kind must be one of: {', '.join(KINDS)}")

    source = data.get("source")
    if not isinstance(source, dict):
        raise RecipeError(f"recipe '{name}' is missing a source object")

    git = _required_string(source, "git", name)
    validate_git_url(git)
    ref = source.get("ref", "main")
    if not isinstance(ref, str) or not ref.strip():
        raise RecipeError(f"recipe '{name}' source.ref must be a non-empty string")

    raw_path = source.get("path")
    subpath: str | None
    if raw_path is None:
        subpath = None
    else:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RecipeError(f"recipe '{name}' source.path must be a relative path")
        subpath = _clean_subpath(raw_path.strip(), name)

    return Recipe(
        name=name,
        kind=kind,
        description=_required_string(data, "description", name),
        homepage=_required_string(data, "homepage", name),
        license=validate_license(_required_string(data, "license", name)),
        git=git,
        ref=ref.strip(),
        path=subpath,
    )


def validate_license(expression: str) -> str:
    """Accept one SPDX id, or several joined by OR or AND."""
    parts = re.split(r"\s+(?:OR|AND)\s+", expression.strip())
    if not parts or any(part not in ALLOWED_LICENSES for part in parts):
        allowed = ", ".join(sorted(ALLOWED_LICENSES))
        raise RecipeError(f"license '{expression}' must use allowed SPDX ids: {allowed}")
    if " OR " in expression and " AND " in expression:
        raise RecipeError(f"license '{expression}' cannot mix OR and AND")
    return expression.strip()


def license_terms(expression: str) -> tuple[str, tuple[str, ...]]:
    expression = validate_license(expression)
    if " OR " in expression:
        return "OR", tuple(re.split(r"\s+OR\s+", expression))
    if " AND " in expression:
        return "AND", tuple(re.split(r"\s+AND\s+", expression))
    return "SINGLE", (expression,)


def validate_git_url(url: str) -> None:
    """Reject clone URLs that can execute a helper during fetch."""
    if url != url.strip() or any(char in url for char in "\n\r\x00"):
        raise RecipeError("git url contains whitespace or control characters")
    lowered = url.lower()
    if lowered.startswith(("ext::", "fd::", "-")):
        raise RecipeError(f"git url is not allowed: {url}")
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in {"https", "http", "ssh", "git", "file"}:
            raise RecipeError(f"git url scheme '{scheme}' is not allowed")


def format_recipe(recipe: Recipe) -> str:
    lines = [
        f"name: {recipe.name}",
        f"kind: {recipe.kind}",
        f"description: {recipe.description}",
        f"homepage: {recipe.homepage}",
        f"license: {recipe.license}",
        f"git: {recipe.git}",
        f"ref: {recipe.ref}",
    ]
    if recipe.path is not None:
        lines.append(f"path: {recipe.path}")
    if recipe.tap is not None:
        lines.append(f"tap: {recipe.tap}")
        if recipe.tap == "core":
            lines.append("trust: public core")
        else:
            lines.append(f"trust: tap/{recipe.tap}")
    if recipe.catalog is not None:
        lines.append(f"catalog: {recipe.catalog}")
    return "\n".join(lines)


def format_recipe_line(recipe: Recipe) -> str:
    return f"{recipe.name}  {recipe.kind}  {recipe.license}  {recipe.description}"


def _required_string(data: dict, key: str, recipe_name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecipeError(f"recipe '{recipe_name}' is missing {key}")
    return value.strip()


def _clean_subpath(path: str, recipe_name: str) -> str:
    if "\\" in path:
        raise RecipeError(f"recipe '{recipe_name}' source.path must use '/' separators")
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise RecipeError(
            f"recipe '{recipe_name}' source.path must be a relative path inside the repo"
        )
    return pure.as_posix()
