from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from abom.cli import (
    AbomError,
    cmd_add,
    cmd_init,
    cmd_install_recipe,
    cmd_lock,
    cmd_lock_check,
    cmd_tap_add,
    cmd_tap_list,
    cmd_tap_update,
    cmd_unuse,
    tools_dir,
)
from abom.lockfile import material_digest, read_lock
from abom.taps import resolve_tap_catalog


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


def test_init_add_unuse_and_lock_digest(tmp_path: Path, monkeypatch) -> None:
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
    (project / ".git").mkdir()
    monkeypatch.chdir(project)

    assert "initialized project" in cmd_init()
    lines = cmd_add("local-skill", agent="cursor")
    assert any("installed local-skill" in line or "linked skill" in line for line in lines)
    link = project / ".cursor" / "skills" / "local-skill"
    assert link.is_symlink()
    lock = read_lock(project / "abom.lock.json")
    assert "local-skill" in lock
    assert lock["local-skill"]["digest"]
    assert "lock check passed" in cmd_lock_check(project / "abom.lock.json")

    material = tools_dir() / "local-skill" / "skills" / "demo" / "SKILL.md"
    material.write_text(material.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
    with pytest.raises(AbomError, match="digest mismatch"):
        cmd_lock_check(project / "abom.lock.json")

    message = cmd_unuse("local-skill")
    assert "unlinked skill" in message
    assert not link.exists()
    project_cfg = json.loads((project / ".abom" / "project.json").read_text(encoding="utf-8"))
    assert "local-skill" not in project_cfg["tools"]


def test_git_tap_and_update(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.delenv("ABOM_EXTRA_CATALOGS", raising=False)
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n"},
    )
    catalog_repo = _create_repo(
        tmp_path / "tap-repo",
        {
            "recipes.json": json.dumps(
                {
                    "schema": "abom.catalog/v1",
                    "recipes": [
                        {
                            "name": "tap-skill",
                            "kind": "skill",
                            "description": "From a git tap.",
                            "homepage": "https://example.com/tap",
                            "license": "MIT",
                            "source": {
                                "git": str(source),
                                "ref": "main",
                                "path": "skills/demo",
                            },
                        }
                    ],
                }
            )
            + "\n"
        },
    )
    assert "added tap team" in cmd_tap_add("team", str(catalog_repo), path="recipes.json")
    listed = "\n".join(cmd_tap_list())
    assert "team  git" in listed
    assert any("updated team" in line for line in cmd_tap_update("team"))
    from abom.cli import cmd_info

    assert "From a git tap." in cmd_info("tap-skill")


def test_url_tap_fetches_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    remote = tmp_path / "remote.json"
    _write_catalog(
        remote,
        [
            {
                "name": "url-skill",
                "kind": "skill",
                "description": "From a url tap.",
                "homepage": "https://example.com/url",
                "license": "MIT",
                "source": {
                    "git": "https://example.com/demo.git",
                    "ref": "main",
                    "path": "skills/demo",
                },
            }
        ],
    )
    body = remote.read_bytes()

    class _Resp:
        def read(self) -> bytes:
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("abom.taps.urllib.request.urlopen", lambda request, timeout=20: _Resp())
    assert "added tap remote" in cmd_tap_add("remote", "https://example.com/team.json")
    cached = resolve_tap_catalog(
        {"name": "remote", "kind": "url", "url": "https://example.com/team.json"}
    )
    assert cached.is_file()
    assert "url-skill" in cached.read_text(encoding="utf-8")


def test_material_digest_stable(tmp_path: Path) -> None:
    root = tmp_path / "mat"
    root.mkdir()
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    first = material_digest(root)
    second = material_digest(root)
    assert first == second
    (root / "a.txt").write_text("b\n", encoding="utf-8")
    assert material_digest(root) != first


def test_lock_rewrites_digest_after_reinstall(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\nv1\n"},
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(catalog, [_skill_entry("local-skill", str(source))])
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    cmd_install_recipe("local-skill")
    lock_path = tmp_path / "abom.lock.json"
    cmd_lock(lock_path)
    assert "lock check passed" in cmd_lock_check(lock_path)
