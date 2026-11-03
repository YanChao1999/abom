"""Project policy: allowed licenses, kinds, and required advisory sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from abom.advisory import URL_RE, AdvisoryError, bundled_advisories_path, read_advisories_text
from abom.recipe import ALLOWED_LICENSES, KINDS, RecipeError, license_terms

POLICY_SCHEMA = "abom.policy/v1"
POLICY_FILE = "policy.json"


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Policy:
    licenses: tuple[str, ...] | None
    kinds: tuple[str, ...] | None
    advisories: tuple[str, ...] | None
    workspace: Path


def policy_path(workspace: Path) -> Path:
    return workspace / ".abom" / POLICY_FILE


def load_policy(workspace: Path) -> Policy | None:
    path = policy_path(workspace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"could not read policy '{path}': {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != POLICY_SCHEMA:
        raise PolicyError(f"policy '{path}' must set schema to {POLICY_SCHEMA}")
    return Policy(
        licenses=_string_tuple(data, "licenses", path),
        kinds=_string_tuple(data, "kinds", path),
        advisories=_string_tuple(data, "advisories", path),
        workspace=workspace,
    )


def policy_advisory_sources(policy: Policy | None) -> list[str | Path]:
    """Resolve policy advisory subscriptions. Relative paths are inside the workspace."""
    if policy is None or policy.advisories is None:
        return []
    sources: list[str | Path] = []
    for item in policy.advisories:
        if item == "bundled":
            sources.append(bundled_advisories_path())
            continue
        if URL_RE.match(item):
            sources.append(item)
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = policy.workspace / path
        sources.append(path)
    return sources


def policy_violations(
    policy: Policy | None,
    *,
    kind: str | None = None,
    license_id: str | None = None,
) -> list[str]:
    if policy is None:
        return []
    findings: list[str] = []
    if policy.kinds is not None and kind is not None and kind not in policy.kinds:
        findings.append(f"kind '{kind}' is not allowed by project policy")
    if policy.licenses is not None and license_id:
        try:
            _operator, terms = license_terms(license_id)
        except RecipeError as exc:
            findings.append(str(exc))
        else:
            if any(term not in policy.licenses for term in terms):
                allowed = ", ".join(policy.licenses) or "(none)"
                findings.append(
                    f"license {license_id} is not allowed by project policy ({allowed})"
                )
    return findings


def ensure_policy_advisories(policy: Policy | None) -> None:
    """Fail if a required advisory source cannot be read."""
    if policy is None or policy.advisories is None:
        return
    for source in policy_advisory_sources(policy):
        try:
            read_advisories_text(source)
        except AdvisoryError as exc:
            raise PolicyError(str(exc)) from exc


def _string_tuple(data: dict, key: str, path: Path) -> tuple[str, ...] | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise PolicyError(f"policy '{path}' {key} must be a list of strings")
    items = tuple(item.strip() for item in value)
    if key == "licenses":
        unknown = [item for item in items if item not in ALLOWED_LICENSES]
        if unknown:
            allowed = ", ".join(sorted(ALLOWED_LICENSES))
            raise PolicyError(
                f"policy '{path}' license {unknown[0]} must use allowed SPDX ids: {allowed}"
            )
    elif key == "kinds":
        unknown = [item for item in items if item not in KINDS]
        if unknown:
            raise PolicyError(
                f"policy '{path}' kind '{unknown[0]}' must be one of: {', '.join(KINDS)}"
            )
    else:
        for item in items:
            if item == "bundled" or URL_RE.match(item):
                continue
            if any(char in item for char in "\n\r\x00"):
                raise PolicyError(f"policy '{path}' advisory source '{item}' is not a path or URL")
    return items
