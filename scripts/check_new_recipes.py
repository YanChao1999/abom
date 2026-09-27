#!/usr/bin/env python3
"""Fail a pull request when src/abom/recipes.json adds or edits an invalid recipe."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from abom.recipe import RecipeError, recipe_changes

CATALOG = Path("src/abom/recipes.json")


def base_catalog_text(base_ref: str) -> str | None:
    if not base_ref:
        return None
    completed = subprocess.run(
        ["git", "show", f"origin/{base_ref}:src/abom/recipes.json"],
        cwd=_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def main() -> int:
    head_text = (_ROOT / CATALOG).read_text(encoding="utf-8")
    base_text = base_catalog_text(os.environ.get("BASE_REF", ""))
    try:
        changes = recipe_changes(base_text, head_text)
    except RecipeError as exc:
        print(f"recipe check failed: {exc}", file=sys.stderr)
        return 1
    if not changes:
        print("no recipe added or updated")
        return 0
    for action, recipe in changes:
        print(f"{action} {recipe.name}  {recipe.kind}  {recipe.license}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
