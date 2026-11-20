from __future__ import annotations

from pathlib import Path

from abom.project import find_workspace, init_project, load_project, record_tool_wire, tool_wire


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


def test_init_records_several_agents(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    message = init_project(agents=["cursor", "claude", "cursor"])
    assert "initialized project" in message
    assert load_project(workspace)["agent"] == ["cursor", "claude"]

    record_tool_wire(workspace, "local-skill", agent="cursor")
    updated = init_project(agents=["claude", "codex"])
    assert "updated project agents" in updated
    data = load_project(workspace)
    assert data["agent"] == ["claude", "codex"]
    assert data["tools"]["local-skill"]["agent"] == "cursor"
