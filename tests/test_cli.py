from __future__ import annotations

import os
import subprocess
from pathlib import Path

from abom.cli import cmd_install, cmd_link, cmd_remove, cmd_search, tools_dir


def _create_local_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return path


def test_install_search_remove_link(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "abom-home"))

    source_repo = _create_local_git_repo(tmp_path / "source-repo")
    target_repo = tmp_path / "target"

    install_message = cmd_install("skill-demo", str(source_repo))
    assert "installed skill-demo" in install_message

    assert cmd_search(None) == ["skill-demo"]
    assert cmd_search("demo") == ["skill-demo"]

    link_message = cmd_link("skill-demo", str(target_repo), None)
    link_path = target_repo / ".abom" / "skill-demo"
    assert "linked skill-demo" in link_message
    assert link_path.is_symlink()

    remove_message = cmd_remove("skill-demo")
    assert remove_message == "removed skill-demo"
    assert cmd_search(None) == []


def test_search_and_remove_symlinked_tool_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "abom-home"))
    missing_target = tmp_path / "missing-target"
    entry = tools_dir() / "prompt-demo"
    entry.symlink_to(missing_target)

    assert cmd_search(None) == ["prompt-demo"]
    assert cmd_remove("prompt-demo") == "removed prompt-demo"
    assert not entry.exists()


def test_link_accepts_symlinked_tool_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "abom-home"))
    source_target = tmp_path / "missing-target"
    tool_entry = tools_dir() / "mcp-demo"
    tool_entry.symlink_to(source_target)
    target_repo = tmp_path / "target"

    cmd_link("mcp-demo", str(target_repo), None)
    assert (target_repo / ".abom" / "mcp-demo").is_symlink()
