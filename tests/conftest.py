from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def packaged_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite on the packaged catalog unless a test opts into refresh."""
    monkeypatch.setenv("ABOM_CATALOG_URL", "")
