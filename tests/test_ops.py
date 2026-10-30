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
    cmd_sync,
    cmd_tap_add,
    cmd_tap_list,
    cmd_tap_remove,
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
        "cwd": str(project.resolve()),
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
    assert any(str(private / "team.json") in line for line in listed)
    assert any(line.startswith("local/team ") for line in listed)
    assert any("trust root: core" in line for line in listed)


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


def test_use_defaults_to_nearest_workspace_and_reuses_project_agent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "mcp",
        {
            "src/server/package.json": json.dumps(
                {"name": "@modelcontextprotocol/server-filesystem", "version": "1.0.0"}
            ),
            "src/server/index.js": "export {}\n",
        },
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_mcp_entry("local-fs", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    project = tmp_path / "project"
    nested = project / "apps" / "web"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()

    cmd_install_recipe("local-fs")
    monkeypatch.chdir(nested)
    message = cmd_use("local-fs", agent="cursor")
    assert "configured mcp" in message
    assert f"workspace {project.resolve()}" in message
    config = project / ".cursor" / "mcp.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["mcpServers"]["local-fs"]["cwd"] == str(project.resolve())
    assert str(project.resolve()) in data["mcpServers"]["local-fs"]["args"]
    project_cfg = json.loads((project / ".abom" / "project.json").read_text(encoding="utf-8"))
    assert project_cfg["schema"] == "abom.project/v1"
    assert project_cfg["tools"]["local-fs"]["agent"] == "cursor"

    config.unlink()
    message = cmd_use("local-fs")
    assert "configured mcp" in message
    again = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert again["mcpServers"]["local-fs"]["cwd"] == str(project.resolve())


def test_install_use_without_target_uses_cwd_workspace(tmp_path: Path, monkeypatch) -> None:
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
    (project / ".cursor").mkdir()
    monkeypatch.chdir(project)

    message = cmd_install_recipe("local-skill", use_target=".")
    assert "linked skill" in message
    assert (project / ".cursor" / "skills" / "local-skill").is_symlink()
    assert (project / ".abom" / "project.json").is_file()


def test_install_use_wires_skill(tmp_path: Path, monkeypatch) -> None:
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

    message = cmd_install_recipe("local-skill", use_target=str(project), agent="codex")
    assert "installed local-skill" in message
    assert "linked skill" in message
    link = project / ".codex" / "skills" / "local-skill"
    assert link.is_symlink()
    assert (link / "SKILL.md").is_file()


def test_use_mcp_sets_workspace_cwd_and_filesystem_args(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "mcp",
        {
            "src/server/package.json": json.dumps(
                {"name": "@modelcontextprotocol/server-filesystem", "version": "1.0.0"}
            ),
            "src/server/index.js": "export {}\n",
        },
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_mcp_entry("local-fs", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    project = tmp_path / "project"
    project.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cmd_install_recipe("local-fs")
    cmd_use("local-fs", str(project), agent="cursor", workspace=str(workspace))
    data = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    entry = data["mcpServers"]["local-fs"]
    assert entry["cwd"] == str(workspace.resolve())
    assert str(workspace.resolve()) in entry["args"]


def test_sync_reinstalls_to_lock_commit(tmp_path: Path, monkeypatch) -> None:
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

    lines = cmd_sync(lock_path)
    assert any("synced local-skill" in line for line in lines)
    receipt = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))
    assert receipt["commit"] == first

    (source / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nv2\n",
        encoding="utf-8",
    )
    _git(source, "add", "skills/demo/SKILL.md")
    _git(source, "commit", "-m", "v2")
    cmd_upgrade("local-skill")
    assert json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))["commit"] != first

    lines = cmd_sync(lock_path)
    assert any("synced local-skill" in line for line in lines)
    again = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))
    assert again["commit"] == first
    assert "ok local-skill" in "\n".join(cmd_sync(lock_path))


def test_tap_overrides_core_and_lists_trust_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.delenv("ABOM_EXTRA_CATALOGS", raising=False)
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n"},
    )
    private_catalog = tmp_path / "team.json"
    _write_catalog(
        private_catalog,
        [
            {
                "name": "abom-skill",
                "kind": "skill",
                "description": "Tap override of abom-skill.",
                "homepage": "https://example.com/tap",
                "license": "MIT",
                "source": {"git": str(source), "ref": "main", "path": "skills/demo"},
            }
        ],
    )

    assert "added tap team" in cmd_tap_add("team", str(private_catalog))
    listed = cmd_tap_list()
    assert listed[0].startswith("core  bundled")
    assert any(line.startswith("team  path") for line in listed)

    from abom.cli import cmd_info

    info = cmd_info("abom-skill")
    assert "Tap override of abom-skill." in info
    assert "tap: team" in info
    assert "trust: tap/team" in info
    assert "removed tap team" in cmd_tap_remove("team")
    with pytest.raises(AbomError, match="no tap named"):
        cmd_tap_remove("team")


def test_upgrade_records_receipt_history(tmp_path: Path, monkeypatch) -> None:
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
    (source / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nv2\n",
        encoding="utf-8",
    )
    _git(source, "add", "skills/demo/SKILL.md")
    _git(source, "commit", "-m", "v2")
    cmd_upgrade("local-skill")
    receipt = json.loads(receipt_path("local-skill").read_text(encoding="utf-8"))
    assert receipt["previous_commit"] == first
    assert receipt["history"][0] == first


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
