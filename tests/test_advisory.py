from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from abom.advisory import match_advisories, parse_advisories
from abom.cli import (
    AbomError,
    cmd_advisories,
    cmd_advisory,
    cmd_check,
    cmd_install_recipe,
    cmd_report,
)


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


def _write_advisories(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema": "abom.advisories/v1", "advisories": entries}, indent=2) + "\n",
        encoding="utf-8",
    )


def test_advisory_id_and_match_by_recipe_name(tmp_path: Path) -> None:
    path = tmp_path / "advisories.json"
    _write_advisories(
        path,
        [
            {
                "id": "ABOM-2026-0001",
                "severity": "high",
                "summary": "Prompt injection in demo recipe.",
                "description": "The skill tells the agent to ignore earlier instructions.",
                "published": "2026-10-29",
                "affected": [{"recipe": "evil-skill", "all_commits": True}],
                "references": ["https://example.com/advisory"],
            }
        ],
    )
    advisories = parse_advisories(path.read_text(encoding="utf-8"))
    assert advisories[0].id == "ABOM-2026-0001"
    hits = match_advisories(
        recipe_name="evil-skill",
        git="https://example.com/evil.git",
        path=path,
    )
    assert [hit.advisory.id for hit in hits] == ["ABOM-2026-0001"]
    assert (
        match_advisories(
            recipe_name="safe-skill",
            git="https://example.com/evil.git",
            path=path,
        )
        == []
    )


def test_install_and_check_reject_an_advisory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    source = _create_repo(
        tmp_path / "skill",
        {"skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n"},
    )
    catalog = tmp_path / "recipes.json"
    _write_catalog(
        catalog,
        [
            {
                "name": "evil-skill",
                "kind": "skill",
                "description": "Fixture blocked by advisory.",
                "homepage": "https://example.com/evil",
                "license": "MIT",
                "source": {"git": str(source), "ref": "main", "path": "skills/demo"},
            }
        ],
    )
    advisories = tmp_path / "advisories.json"
    _write_advisories(
        advisories,
        [
            {
                "id": "ABOM-2026-0007",
                "severity": "critical",
                "summary": "Known malicious fixture recipe.",
                "description": "Used only in tests to prove advisory blocking.",
                "published": "2026-10-29",
                "affected": [{"recipe": "evil-skill", "all_commits": True}],
            }
        ],
    )
    monkeypatch.setenv("ABOM_CATALOG", str(catalog))
    monkeypatch.setenv("ABOM_ADVISORIES", str(advisories))

    with pytest.raises(AbomError, match="ABOM-2026-0007"):
        cmd_check("evil-skill")
    with pytest.raises(AbomError, match="ABOM-2026-0007"):
        cmd_install_recipe("evil-skill")

    report = json.loads(cmd_report("evil-skill"))
    assert report["schema"] == "abom.report/v1"
    assert report["ok"] is False
    assert report["rules_version"] == "1.0.0"
    assert report["advisories"][0]["id"] == "ABOM-2026-0007"
    assert cmd_advisories(None)[0].startswith("ABOM-2026-0007")
    assert "Known malicious fixture recipe." in cmd_advisory("ABOM-2026-0007")
