from __future__ import annotations

from pathlib import Path

from abom.project import find_workspace, load_project, record_tool_wire, tool_wire


def test_find_workspace_prefers_nearest_marker(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    nested = root / "apps" / "api"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "apps" / ".cursor").mkdir()
    monkeypatch.chdir(nested)
    assert find_workspace() == (root / "apps").resolve()


def test_find_workspace_falls_back_to_cwd(tmp_path: Path, monkeypatch) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    assert find_workspace() == bare.resolve()


def test_record_and_load_project_tool_wire(tmp_path: Path) -> None:
    workspace = tmp_path / "proj"
    workspace.mkdir()
    record_tool_wire(workspace, "local-mcp", agent="claude", link_name="mcp")
    assert tool_wire(workspace, "local-mcp") == {"agent": "claude", "link_name": "mcp"}
    data = load_project(workspace)
    assert data["schema"] == "abom.project/v1"
    assert data["agent"] == "claude"
