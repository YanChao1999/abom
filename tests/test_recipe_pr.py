from __future__ import annotations

import json
from pathlib import Path

import pytest

from abom.recipe import RecipeError, recipe_changes


def _catalog(entries: list[dict]) -> str:
    return json.dumps({"schema": "abom.catalog/v1", "recipes": entries})


def _entry(name: str, *, license_id: str = "MIT") -> dict:
    return {
        "name": name,
        "kind": "skill",
        "description": "Example skill.",
        "homepage": "https://example.com",
        "license": license_id,
        "source": {"git": "https://example.com/skill.git", "ref": "main", "path": "skills/demo"},
    }


def test_unchanged_catalog_has_no_recipe_changes() -> None:
    text = (Path(__file__).resolve().parents[1] / "src/abom/recipes.json").read_text(
        encoding="utf-8"
    )
    assert recipe_changes(text, text) == []


def test_added_recipe_is_reported() -> None:
    base = _catalog([])
    head = _catalog([_entry("abom-skill")])
    changes = recipe_changes(base, head)
    assert [action for action, _recipe in changes] == ["added"]
    assert changes[0][1].name == "abom-skill"
    assert changes[0][1].license == "MIT"


def test_new_recipe_with_an_unknown_license_fails() -> None:
    head = _catalog([_entry("abom-skill", license_id="proprietary")])
    with pytest.raises(RecipeError, match="proprietary"):
        recipe_changes(None, head)
