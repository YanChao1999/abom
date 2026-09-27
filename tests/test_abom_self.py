from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from abom.check import inspect_material
from abom.recipe import Recipe


def _load_server():
    path = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("abom_mcp_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recipe(kind: str, path: str) -> Recipe:
    return Recipe(
        name=f"abom-{kind}",
        kind=kind,
        description="fixture",
        homepage="https://github.com/YanChao1999/abom",
        license="MIT",
        git="https://github.com/YanChao1999/abom.git",
        ref="main",
        path=path,
    )


def test_abom_skill_mcp_and_package_pass_checks() -> None:
    root = Path(__file__).resolve().parents[1]
    skill = inspect_material(_recipe("skill", "skills/abom"), root)
    mcp = inspect_material(_recipe("mcp", "mcp"), root)
    package = inspect_material(
        Recipe(
            name="abom-package",
            kind="package",
            description="fixture",
            homepage="https://github.com/YanChao1999/abom",
            license="MIT",
            git="https://github.com/YanChao1999/abom.git",
            ref="main",
            path=None,
        ),
        root,
    )
    assert skill.ok, skill.findings
    assert mcp.ok, mcp.findings
    assert package.ok, package.findings


def test_license_file_must_match_the_recipe(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n", encoding="utf-8"
    )
    (checkout / "LICENSE").write_text(
        "Apache License\n\nVersion 2.0, January 2004\n", encoding="utf-8"
    )
    recipe = Recipe(
        name="demo-skill",
        kind="skill",
        description="fixture",
        homepage="https://example.com",
        license="MIT",
        git="https://example.com/demo.git",
        ref="main",
        path=None,
    )
    report = inspect_material(recipe, checkout)
    assert not report.ok
    assert any("does not match" in finding for finding in report.findings)


def test_mcp_lists_and_shows_abom_recipes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    server = _load_server()

    listed = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert "list_recipes" in names
    assert "install_recipe" in names

    called = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_recipes", "arguments": {"query": "abom-"}},
        }
    )
    text = called["result"]["content"][0]["text"]
    assert "abom-skill" in text
    assert "abom-mcp" in text
    assert called["result"]["isError"] is False

    shown = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "show_recipe", "arguments": {"name": "abom-skill"}},
        }
    )
    body = shown["result"]["content"][0]["text"]
    assert "installed: no" in body
    assert "skills/abom" in body


def test_mcp_frames_round_trip() -> None:
    server = _load_server()
    buffer = io.BytesIO()
    server.write_message(buffer, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    buffer.seek(0)
    assert server.read_message(buffer) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    line = io.BytesIO(b'{"jsonrpc":"2.0","id":4,"method":"ping"}\n')
    message = server.read_message(line)
    assert server.handle_message(message)["result"] == {}
