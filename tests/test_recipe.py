from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from abom.cli import (
    AbomError,
    cmd_info,
    cmd_install_recipe,
    cmd_link,
    cmd_recipes,
    cmd_remove,
    tools_dir,
)
from abom.recipe import RecipeError, load_catalog, load_recipe


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def _create_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    payload = {
        "LICENSE": "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy.\n",
    }
    payload.update(files)
    for relative, body in payload.items():
        destination = path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
        _git(path, "add", relative)
    _git(path, "commit", "-m", "init")
    return path


def _entry(name: str, git: str, *, kind: str = "skill", path: str | None = "skills/demo") -> dict:
    source: dict[str, str] = {"git": git, "ref": "main"}
    if path is not None:
        source["path"] = path
    return {
        "name": name,
        "kind": kind,
        "description": "Local fixture material.",
        "homepage": "https://example.com/fixture",
        "license": "MIT",
        "source": source,
    }


def _write_catalog(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema": "abom.catalog/v1", "recipes": entries}, indent=2) + "\n",
        encoding="utf-8",
    )


def test_bundled_catalog_has_one_of_each_kind(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)

    listed = cmd_recipes(None)
    assert [line.split("  ", 1)[0] for line in listed] == [
        "abom-mcp",
        "abom-package",
        "abom-skill",
        "anthropics-skill-creator",
        "f-prompts",
        "f-prompts-chat",
        "modelcontextprotocol-filesystem",
    ]
    assert [line.split("  ")[1] for line in listed] == [
        "mcp",
        "package",
        "skill",
        "skill",
        "prompt",
        "package",
        "mcp",
    ]

    info = cmd_info("anthropics-skill-creator")
    assert "kind: skill" in info
    assert "git: https://github.com/anthropics/skills.git" in info
    assert "path: skills/skill-creator" in info
    assert "Apache-2.0" in info

    prompt = cmd_info("f-prompts")
    assert "kind: prompt" in prompt
    assert "path: PROMPTS.md" in prompt

    mcp = cmd_info("modelcontextprotocol-filesystem")
    assert "kind: mcp" in mcp
    assert "path: src/filesystem" in mcp

    package = cmd_info("f-prompts-chat")
    assert "kind: package" in package
    assert "path: packages/prompts.chat" in package

    own_skill = cmd_info("abom-skill")
    assert "path: skills/abom" in own_skill
    assert "https://github.com/YanChao1999/abom.git" in own_skill

    own_mcp = cmd_info("abom-mcp")
    assert "kind: mcp" in own_mcp
    assert "path: mcp" in own_mcp

    own_package = cmd_info("abom-package")
    assert "kind: package" in own_package
    assert "license: MIT" in own_package
    assert "path:" not in own_package

    matched = cmd_recipes("creator")
    assert len(matched) == 1
    assert matched[0].startswith("anthropics-skill-creator")


def test_catalog_override_replaces_repo_list(tmp_path: Path, monkeypatch) -> None:
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_entry("skill-creator", "/tmp/local-skills.git")])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))

    recipe = load_recipe("skill-creator")
    assert recipe.git == "/tmp/local-skills.git"
    assert "Local fixture material." in cmd_info("skill-creator")


def test_install_recipe_links_subpath_and_removes_receipt(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("ABOM_HOME", str(home))
    source = _create_repo(
        tmp_path / "upstream",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n"},
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_entry("local-skill", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))

    message = cmd_install_recipe("local-skill")
    checkout = tools_dir() / "local-skill"
    assert f"installed local-skill -> {checkout}" == message
    assert (checkout / "skills" / "demo" / "SKILL.md").is_file()

    receipt = json.loads((home / "receipts" / "local-skill.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "skill"
    assert receipt["path"] == "skills/demo"
    assert receipt["commit"]

    project = tmp_path / "project"
    project.mkdir()
    cmd_link("local-skill", str(project), None)
    link = project / ".abom" / "local-skill"
    assert link.is_symlink()
    assert link.resolve() == (checkout / "skills" / "demo").resolve()
    assert (link / "SKILL.md").is_file()

    assert cmd_remove("local-skill") == "removed local-skill"
    assert not checkout.exists()
    assert not (home / "receipts" / "local-skill.json").exists()


def test_install_recipe_can_link_a_prompt_file(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("ABOM_HOME", str(home))
    source = _create_repo(tmp_path / "prompts", {"prompts/review.md": "Review the diff.\n"})
    catalog = tmp_path / "recipes.json"
    _write_catalog(
        catalog, [_entry("review-prompt", str(source), kind="prompt", path="prompts/review.md")]
    )
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))

    cmd_install_recipe("review-prompt")
    project = tmp_path / "project"
    project.mkdir()
    cmd_link("review-prompt", str(project), None)
    link = project / ".abom" / "review-prompt"
    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == "Review the diff.\n"


def test_install_recipe_rolls_back_when_path_is_missing(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("ABOM_HOME", str(home))
    source = _create_repo(tmp_path / "upstream", {"README.md": "no skill here\n"})
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_entry("broken-skill", str(source), path="skills/missing")])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))

    with pytest.raises(AbomError, match="was not found"):
        cmd_install_recipe("broken-skill")
    assert not (tools_dir() / "broken-skill").exists()
    assert not (home / "receipts" / "broken-skill.json").exists()


def test_missing_recipe_names_the_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    with pytest.raises(AbomError, match="recipes.json"):
        cmd_install_recipe("does-not-exist")


def test_catalog_rejects_duplicate_names_and_unsafe_paths(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    _write_catalog(
        duplicate, [_entry("local-skill", "/tmp/a.git"), _entry("local-skill", "/tmp/b.git")]
    )
    with pytest.raises(RecipeError, match="duplicate recipe"):
        load_catalog(duplicate)

    unsafe = tmp_path / "unsafe.json"
    _write_catalog(
        unsafe, [_entry("bad-path", "https://example.com/mcp.git", kind="mcp", path="../outside")]
    )
    with pytest.raises(RecipeError, match="relative path"):
        load_catalog(unsafe)
