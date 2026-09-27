from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCHEMA = "abom.catalog/v1"
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


def bundled_catalog_path() -> Path:
    return Path(__file__).resolve().parent / "recipes.json"


def catalog_path() -> Path:
    override = os.environ.get("ABOM_CATALOG")
    if override:
        return Path(override).expanduser()
    return bundled_catalog_path()


def load_catalog(path: Path | None = None) -> dict[str, Recipe]:
    catalog_file = path or catalog_path()
    try:
        text = catalog_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeError(f"could not read recipe catalog '{catalog_file}': {exc}") from exc
    recipes, _entries = parse_catalog(text, str(catalog_file))
    return recipes


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
