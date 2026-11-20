from __future__ import annotations

import json
from pathlib import Path

import pytest

from abom.cli import cmd_catalogs, cmd_tap_list, cmd_update
from abom.recipe import (
    RecipeError,
    bundled_catalog_path,
    catalog_sources,
    compatible_catalog_text,
    load_catalog,
    refresh_core_catalog,
)

REMOTE = "https://example.com/recipes.json"


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _entry(
    name: str, *, kind: str = "skill", description: str = "Remote skill.", **extra: object
) -> dict:
    entry = {
        "name": name,
        "kind": kind,
        "description": description,
        "homepage": "https://example.com/remote",
        "license": "MIT",
        "source": {
            "git": "https://example.com/remote.git",
            "ref": "main",
            "path": "skills/demo",
        },
    }
    entry.update(extra)
    return entry


def _document(entries: list[dict], schema: str = "abom.catalog/v1") -> str:
    return json.dumps({"schema": schema, "recipes": entries})


def _use_remote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str, *, ttl: str = "0"):
    calls = {"n": 0}

    def urlopen(request, timeout=0):
        calls["n"] += 1
        assert getattr(request, "full_url", None) == REMOTE
        assert timeout == 5
        return _Response(body.encode("utf-8"))

    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", REMOTE)
    monkeypatch.setenv("ABOM_CATALOG_TTL", ttl)
    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    return calls


def test_empty_catalog_url_stays_on_packaged_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", "")

    def urlopen(*_args, **_kwargs):
        raise AssertionError("catalog refresh should stay offline")

    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    assert catalog_sources()[0] == ("core", bundled_catalog_path())
    assert cmd_tap_list()[0] == "core  bundled  public trust root"


def test_remote_catalog_replaces_packaged_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = _document([_entry("remote-skill", description="From the remote catalog.")])
    calls = _use_remote(monkeypatch, tmp_path, body)

    catalog = load_catalog()
    assert list(catalog) == ["remote-skill"]
    assert catalog["remote-skill"].description == "From the remote catalog."
    assert calls["n"] == 1
    assert cmd_tap_list()[0] == f"core  remote  {REMOTE}"
    assert any("trust root: core (remote public catalog)" in line for line in cmd_catalogs())


def test_incompatible_entry_keeps_packaged_recipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packaged = load_catalog()["abom-skill"].description
    body = _document(
        [
            _entry("remote-skill", description="From the remote catalog.", min_abom="9"),
            _entry("abom-skill", kind="widget", description="too new"),
            _entry("future-thing", kind="widget"),
        ]
    )
    _use_remote(monkeypatch, tmp_path, body)

    catalog = load_catalog()
    assert set(catalog) == {"remote-skill", "abom-skill"}
    assert catalog["remote-skill"].description == "From the remote catalog."
    assert catalog["abom-skill"].description == packaged
    cache = tmp_path / "home" / "catalog-cache" / "core.json"
    assert "min_abom" in cache.read_text(encoding="utf-8")


def test_unknown_schema_keeps_packaged_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_remote(monkeypatch, tmp_path, _document([], schema="abom.catalog/v2"))

    assert "anthropics-skill-creator" in load_catalog()
    assert not (tmp_path / "home" / "catalog-cache" / "core.json").exists()
    assert any("trust root: core (bundled public catalog)" in line for line in cmd_catalogs())


def test_fetch_failure_keeps_packaged_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", REMOTE)
    monkeypatch.setenv("ABOM_CATALOG_TTL", "0")

    def urlopen(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    assert "anthropics-skill-creator" in load_catalog()


def test_fetch_failure_reuses_cache_for_the_same_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "home" / "catalog-cache" / "core.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(_document([_entry("cached-skill")]), encoding="utf-8")
    cache.with_suffix(".url").write_text(REMOTE + "\n", encoding="utf-8")
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", REMOTE)
    monkeypatch.setenv("ABOM_CATALOG_TTL", "0")

    def urlopen(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    assert list(load_catalog()) == ["cached-skill"]


def test_fresh_cache_skips_the_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "home" / "catalog-cache" / "core.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(_document([_entry("cached-skill")]), encoding="utf-8")
    cache.with_suffix(".url").write_text(REMOTE + "\n", encoding="utf-8")
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", REMOTE)
    monkeypatch.setenv("ABOM_CATALOG_TTL", "3600")

    def urlopen(*_args, **_kwargs):
        raise AssertionError("fresh cache should skip the network")

    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    assert list(load_catalog()) == ["cached-skill"]


def test_http_catalog_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABOM_CATALOG_URL", "http://example.com/recipes.json")
    with pytest.raises(RecipeError, match="https"):
        catalog_sources()


def test_update_refreshes_core_even_when_the_cache_is_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "home" / "catalog-cache" / "core.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(_document([_entry("cached-skill")]), encoding="utf-8")
    cache.with_suffix(".url").write_text(REMOTE + "\n", encoding="utf-8")
    body = _document([_entry("remote-skill", description="From the remote catalog.")])
    calls = _use_remote(monkeypatch, tmp_path, body, ttl="3600")

    lines = cmd_update()
    assert lines == [f"updated core  {REMOTE}  1 recipes"]
    assert calls["n"] == 1
    assert list(load_catalog()) == ["remote-skill"]


def test_update_reports_the_packaged_catalog_when_remote_refresh_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", "")

    def urlopen(*_args, **_kwargs):
        raise AssertionError("packaged catalog should stay offline")

    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    line = refresh_core_catalog()
    assert line.startswith("core  packaged  ")
    assert line.endswith(" recipes")


def test_update_reports_a_fetch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ABOM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ABOM_CATALOG", raising=False)
    monkeypatch.setenv("ABOM_CATALOG_URL", REMOTE)

    def urlopen(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr("abom.recipe.urllib.request.urlopen", urlopen)
    with pytest.raises(RecipeError, match="could not fetch"):
        refresh_core_catalog()


def test_update_rejects_an_incompatible_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_remote(monkeypatch, tmp_path, _document([], schema="abom.catalog/v2"))
    with pytest.raises(RecipeError, match="abom.catalog/v1"):
        refresh_core_catalog()
    assert not (tmp_path / "home" / "catalog-cache" / "core.json").exists()


def test_compatible_catalog_text_refuses_other_schemas() -> None:
    bundled = bundled_catalog_path().read_text(encoding="utf-8")
    assert compatible_catalog_text(_document([], schema="abom.catalog/v2"), bundled) is None
    assert compatible_catalog_text("{", bundled) is None
