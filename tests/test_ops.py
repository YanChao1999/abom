from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from abom.cli import (
    AbomError,
    cmd_catalogs,
    cmd_install_recipe,
    cmd_lock,
    cmd_lock_check,
    cmd_rollback,
    cmd_upgrade,
    cmd_use,
    receipt_path,
    tools_dir,
)
from abom.lockfile import read_lock
from abom.recipe import private_catalogs_dir


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
        "LICENSE": (
            "MIT License\n\nPermission is hereby granted, free of charge, to any person "
            "obtaining a copy.\n"
        ),
    }
    payload.update(files)
    for relative, body in payload.items():
        destination = path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
        _git(path, "add", relative)
    _git(path, "commit", "-m", "init")
    return path


def _write_catalog(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema": "abom.catalog/v1", "recipes": entries}, indent=2) + "\n",
        encoding="utf-8",
    )


def _skill_entry(name: str, git: str, path: str = "skills/demo") -> dict:
    return {
        "name": name,
        "kind": "skill",
        "description": "Local fixture skill.",
        "homepage": "https://example.com/fixture",
        "license": "MIT",
        "source": {"git": git, "ref": "main", "path": path},
    }


def _mcp_entry(name: str, git: str, path: str = "src/server") -> dict:
    return {
        "name": name,
        "kind": "mcp",
        "description": "Local fixture mcp.",
        "homepage": "https://example.com/mcp",
        "license": "MIT",
        "source": {"git": git, "ref": "main", "path": path},
    }


def test_use_links_a_skill_into_cursor_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n"},
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_skill_entry("local-skill", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    project = tmp_path / "project"
    project.mkdir()

    cmd_install_recipe("local-skill")
    message = cmd_use("local-skill", str(project), agent="cursor")
    link = project / ".cursor" / "skills" / "local-skill"
    assert "linked skill" in message
    assert link.is_symlink()
    assert (link / "SKILL.md").is_file()


def test_use_writes_mcp_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "mcp",
        {
            "src/server/package.json": json.dumps(
                {"name": "@example/server-demo", "version": "1.0.0"}
            ),
            "src/server/index.ts": "export {}\n",
        },
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_mcp_entry("local-mcp", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    project = tmp_path / "project"
    project.mkdir()

    cmd_install_recipe("local-mcp")
    message = cmd_use("local-mcp", str(project), agent="cursor")
    config = project / ".cursor" / "mcp.json"
    assert "configured mcp" in message
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["mcpServers"]["local-mcp"] == {
        "command": "npx",
        "args": ["-y", "@example/server-demo"],
    }


def test_private_catalog_overrides_bundled_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.delenv("ABOM_EXTRA_CATALOGS", raising=False)
    private = private_catalogs_dir()
    private.mkdir(parents=True)
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n"},
    )
    _write_catalog(
        private / "team.json",
        [
            {
                "name": "abom-skill",
                "kind": "skill",
                "description": "Private override of abom-skill.",
                "homepage": "https://example.com/private",
                "license": "MIT",
                "source": {"git": str(source), "ref": "main", "path": "skills/demo"},
            }
        ],
    )
    from abom.cli import cmd_info

    info = cmd_info("abom-skill")
    assert "Private override of abom-skill." in info
    assert str(source) in info
    listed = cmd_catalogs()
    assert any(str(private / "team.json") == line for line in listed)


def test_lock_upgrade_and_rollback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\nv1\n"},
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_skill_entry("local-skill", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))

    cmd_install_recipe("local-skill")
    first = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))["commit"]
    lock_path = tmp_path / "abom.lock.json"
    assert "wrote" in cmd_lock(lock_path)
    locked = read_lock(lock_path)
    assert locked["local-skill"]["commit"] == first
    assert "lock check passed" in cmd_lock_check(lock_path)

    (source / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nv2\n",
        encoding="utf-8",
    )
    _git(source, "add", "skills/demo/SKILL.md")
    _git(source, "commit", "-m", "v2")

    upgraded = cmd_upgrade("local-skill")
    assert upgraded[0].startswith("upgraded local-skill")
    second = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))
    assert second["commit"] != first
    assert second["previous_commit"] == first
    with pytest.raises(AbomError, match="lock check failed"):
        cmd_lock_check(lock_path)

    rolled = cmd_rollback("local-skill")
    assert "rolled back local-skill" in rolled
    again = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))
    assert again["commit"] == first
    assert (
        (tools_dir() / "local-skill" / "skills" / "demo" / "SKILL.md")
        .read_text(encoding="utf-8")
        .endswith("v1\n")
    )
    assert "lock check passed" in cmd_lock_check(lock_path)


def test_install_respects_lock_commit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\nv1\n"},
    )
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (source / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nv2\n",
        encoding="utf-8",
    )
    _git(source, "add", "skills/demo/SKILL.md")
    _git(source, "commit", "-m", "v2")

    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_skill_entry("local-skill", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    lock_path = tmp_path / "abom.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "schema": "abom.lock/v1",
                "tools": {"local-skill": {"commit": first, "git": str(source), "ref": "main"}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    cmd_install_recipe("local-skill", locked=True, lock_file=lock_path)
    receipt = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))
    assert receipt["commit"] == first
    body = (tools_dir() / "local-skill" / "skills" / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert body.endswith("v1\n")
