"""Published abom advisories, similar in role to CVE records for recipes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ADVISORIES_SCHEMA = "abom.advisories/v1"
RULES_SCHEMA = "abom.rules/v1"
REPORT_SCHEMA = "abom.report/v1"
ID_RE = re.compile(r"^ABOM-\d{4}-\d{4,}$")
SEVERITIES = ("critical", "high", "medium", "low")
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
DEFAULT_FEED_URL = "https://yanchao1999.github.io/abom/advisories.json"


class AdvisoryError(Exception):
    pass


@dataclass(frozen=True)
class Affected:
    recipe: str | None
    git: str | None
    commits: tuple[str, ...]
    refs: tuple[str, ...]
    all_commits: bool


@dataclass(frozen=True)
class Advisory:
    id: str
    severity: str
    summary: str
    description: str
    published: str
    affected: tuple[Affected, ...]
    references: tuple[str, ...]
    withdrawn: str | None


@dataclass(frozen=True)
class AdvisoryHit:
    advisory: Advisory
    reason: str


@dataclass(frozen=True)
class Rule:
    id: str
    summary: str
    check: str | None = None
    pattern: str | None = None
    flags: str = ""
    scan: tuple[str, ...] = ()


RULE_CHECKS = frozenset({"kind-shape", "license-file", "symlink-escape", "unsafe-git", "advisory"})
RULE_SCANS = frozenset({"skill-tree", "prompt-text", "lifecycle-scripts", "mcp-command"})


def bundled_advisories_path() -> Path:
    return Path(__file__).resolve().parent / "advisories.json"


def bundled_rules_path() -> Path:
    return Path(__file__).resolve().parent / "rules.json"


def advisories_path() -> Path | str:
    """Primary advisory source: path, URL, or bundled file."""
    override = os.environ.get("ABOM_ADVISORIES")
    if override:
        cleaned = override.strip()
        if URL_RE.match(cleaned):
            return cleaned
        return Path(cleaned).expanduser()
    return bundled_advisories_path()


def advisory_extra_sources() -> list[str | Path]:
    raw = os.environ.get("ABOM_ADVISORIES_EXTRA", "")
    sources: list[str | Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        if URL_RE.match(part):
            sources.append(part)
        else:
            sources.append(Path(part).expanduser())
    return sources


def load_rules_version(path: Path | None = None) -> str:
    version, _rules = load_rules(path)
    return version


def load_rules(path: Path | None = None) -> tuple[str, tuple[Rule, ...]]:
    """Load the versioned rule file. Patterns are executed from this file."""
    rules_file = path or bundled_rules_path()
    try:
        data = json.loads(rules_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisoryError(f"could not read rules '{rules_file}': {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != RULES_SCHEMA:
        raise AdvisoryError(f"rules '{rules_file}' must set schema to {RULES_SCHEMA}")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise AdvisoryError(f"rules '{rules_file}' is missing version")
    entries = data.get("rules")
    if not isinstance(entries, list) or not entries:
        raise AdvisoryError(f"rules '{rules_file}' must contain a rules list")
    found: list[Rule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AdvisoryError(f"rule entry {index} must be an object")
        rule = _rule_from_mapping(entry)
        if rule.id in seen:
            raise AdvisoryError(f"duplicate rule '{rule.id}'")
        seen.add(rule.id)
        found.append(rule)
    return version.strip(), tuple(found)


def _rule_from_mapping(data: dict) -> Rule:
    rule_id = data.get("id")
    summary = data.get("summary")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise AdvisoryError("rule is missing id")
    if not isinstance(summary, str) or not summary.strip():
        raise AdvisoryError(f"rule '{rule_id}' is missing summary")
    check = data.get("check")
    pattern = data.get("pattern")
    if check is None and pattern is None:
        raise AdvisoryError(f"rule '{rule_id}' needs check or pattern")
    if check is not None and (not isinstance(check, str) or check not in RULE_CHECKS):
        allowed = ", ".join(sorted(RULE_CHECKS))
        raise AdvisoryError(f"rule '{rule_id}' check must be one of: {allowed}")
    flags = data.get("flags") or ""
    if not isinstance(flags, str):
        raise AdvisoryError(f"rule '{rule_id}' flags must be a string")
    scan_raw = data.get("scan") or []
    if not isinstance(scan_raw, list) or any(not isinstance(item, str) for item in scan_raw):
        raise AdvisoryError(f"rule '{rule_id}' scan must be a list of strings")
    scan = tuple(item.strip() for item in scan_raw if item.strip())
    unknown = [item for item in scan if item not in RULE_SCANS]
    if unknown:
        allowed = ", ".join(sorted(RULE_SCANS))
        raise AdvisoryError(f"rule '{rule_id}' scan '{unknown[0]}' must be one of: {allowed}")
    if isinstance(pattern, str):
        if not pattern.strip():
            raise AdvisoryError(f"rule '{rule_id}' pattern is empty")
        if not scan:
            raise AdvisoryError(f"rule '{rule_id}' pattern needs a scan list")
        try:
            re.compile(pattern, re.IGNORECASE if "i" in flags.lower() else 0)
        except re.error as exc:
            raise AdvisoryError(f"rule '{rule_id}' pattern is invalid: {exc}") from exc
    elif pattern is not None:
        raise AdvisoryError(f"rule '{rule_id}' pattern must be a string")
    return Rule(
        id=rule_id.strip(),
        summary=summary.strip(),
        check=check if isinstance(check, str) else None,
        pattern=pattern if isinstance(pattern, str) else None,
        flags=flags,
        scan=scan,
    )


def read_advisories_text(source: str | Path) -> tuple[str, str]:
    """Return (text, label) for a path or http(s) URL."""
    if isinstance(source, str) and URL_RE.match(source):
        request = urllib.request.Request(source, headers={"User-Agent": "abom-advisories/1"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AdvisoryError(f"could not fetch advisories '{source}': {exc}") from exc
        return body, source
    path = Path(source)
    try:
        return path.read_text(encoding="utf-8"), str(path)
    except OSError as exc:
        raise AdvisoryError(f"could not read advisories '{path}': {exc}") from exc


def advisory_sources() -> list[str | Path]:
    """Default feed order. ABOM_ADVISORIES replaces the bundled file; extras overlay it."""
    if os.environ.get("ABOM_ADVISORIES"):
        sources: list[str | Path] = [advisories_path()]
    else:
        sources = [bundled_advisories_path()]
    sources.extend(advisory_extra_sources())
    return sources


def advisory_feed_digest(extra: list[str | Path] | None = None) -> str:
    """Content hash of the advisory feed texts, ignoring install paths."""
    digest = hashlib.sha256()
    for source in [*advisory_sources(), *(extra or [])]:
        text, _label = read_advisories_text(source)
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def load_advisories(
    path: Path | str | None = None,
    *,
    extra: list[str | Path] | None = None,
) -> list[Advisory]:
    """Load advisories. Later sources override earlier ids (extras win over primary)."""
    if path is not None:
        sources: list[str | Path] = [path, *(extra or [])]
    else:
        sources = [*advisory_sources(), *(extra or [])]

    merged: dict[str, Advisory] = {}
    for source in sources:
        text, label = read_advisories_text(source)
        for item in parse_advisories(text, label):
            merged[item.id] = item
    return sorted(merged.values(), key=lambda item: item.id)


def parse_advisories(text: str, label: str = "advisories") -> list[Advisory]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdvisoryError(f"advisories '{label}' are not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != ADVISORIES_SCHEMA:
        raise AdvisoryError(f"advisories '{label}' must set schema to {ADVISORIES_SCHEMA}")
    entries = data.get("advisories")
    if not isinstance(entries, list):
        raise AdvisoryError(f"advisories '{label}' must contain an advisories list")
    found: list[Advisory] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AdvisoryError(f"advisory entry {index} must be an object")
        advisory = advisory_from_mapping(entry)
        if advisory.id in seen:
            raise AdvisoryError(f"duplicate advisory '{advisory.id}'")
        seen.add(advisory.id)
        found.append(advisory)
    return found


def advisory_from_mapping(data: dict) -> Advisory:
    advisory_id = _required_string(data, "id")
    if not ID_RE.fullmatch(advisory_id):
        raise AdvisoryError(f"advisory id '{advisory_id}' must look like ABOM-YYYY-NNNN")
    severity = _required_string(data, "severity")
    if severity not in SEVERITIES:
        raise AdvisoryError(
            f"advisory '{advisory_id}' severity must be one of: {', '.join(SEVERITIES)}"
        )
    affected_raw = data.get("affected")
    if not isinstance(affected_raw, list) or not affected_raw:
        raise AdvisoryError(f"advisory '{advisory_id}' needs at least one affected entry")
    affected = tuple(_affected_from_mapping(item, advisory_id) for item in affected_raw)
    withdrawn = data.get("withdrawn")
    if withdrawn is not None and (not isinstance(withdrawn, str) or not withdrawn.strip()):
        raise AdvisoryError(f"advisory '{advisory_id}' withdrawn must be a date string")
    references = data.get("references") or []
    if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
        raise AdvisoryError(f"advisory '{advisory_id}' references must be a list of strings")
    return Advisory(
        id=advisory_id,
        severity=severity,
        summary=_required_string(data, "summary"),
        description=_required_string(data, "description"),
        published=_required_string(data, "published"),
        affected=affected,
        references=tuple(item.strip() for item in references if item.strip()),
        withdrawn=withdrawn.strip() if isinstance(withdrawn, str) else None,
    )


def match_advisories(
    *,
    recipe_name: str,
    git: str,
    commit: str | None = None,
    ref: str | None = None,
    path: Path | None = None,
    extra: list[str | Path] | None = None,
) -> list[AdvisoryHit]:
    hits: list[AdvisoryHit] = []
    for advisory in load_advisories(path, extra=extra):
        if advisory.withdrawn:
            continue
        for item in advisory.affected:
            reason = _match_affected(item, recipe_name=recipe_name, git=git, commit=commit, ref=ref)
            if reason:
                hits.append(AdvisoryHit(advisory, reason))
                break
    return hits


def format_advisory(advisory: Advisory) -> str:
    lines = [
        f"id: {advisory.id}",
        f"severity: {advisory.severity}",
        f"published: {advisory.published}",
        f"summary: {advisory.summary}",
        f"description: {advisory.description}",
    ]
    if advisory.withdrawn:
        lines.append(f"withdrawn: {advisory.withdrawn}")
    for index, item in enumerate(advisory.affected, start=1):
        lines.append(f"affected[{index}].recipe: {item.recipe or '-'}")
        lines.append(f"affected[{index}].git: {item.git or '-'}")
        if item.commits:
            lines.append(f"affected[{index}].commits: {', '.join(item.commits)}")
        if item.refs:
            lines.append(f"affected[{index}].refs: {', '.join(item.refs)}")
        lines.append(f"affected[{index}].all_commits: {str(item.all_commits).lower()}")
    for reference in advisory.references:
        lines.append(f"reference: {reference}")
    return "\n".join(lines)


def format_advisory_line(advisory: Advisory) -> str:
    state = "withdrawn" if advisory.withdrawn else "active"
    return f"{advisory.id}  {advisory.severity}  {state}  {advisory.summary}"


def _affected_from_mapping(data: object, advisory_id: str) -> Affected:
    if not isinstance(data, dict):
        raise AdvisoryError(f"advisory '{advisory_id}' affected entry must be an object")
    recipe = data.get("recipe")
    git = data.get("git")
    if recipe is not None and (not isinstance(recipe, str) or not recipe.strip()):
        raise AdvisoryError(f"advisory '{advisory_id}' affected.recipe must be a string")
    if git is not None and (not isinstance(git, str) or not git.strip()):
        raise AdvisoryError(f"advisory '{advisory_id}' affected.git must be a string")
    if not recipe and not git:
        raise AdvisoryError(f"advisory '{advisory_id}' affected needs recipe or git")
    commits = data.get("commits") or []
    refs = data.get("refs") or []
    if not isinstance(commits, list) or any(not isinstance(item, str) for item in commits):
        raise AdvisoryError(f"advisory '{advisory_id}' affected.commits must be strings")
    if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
        raise AdvisoryError(f"advisory '{advisory_id}' affected.refs must be strings")
    all_commits = data.get("all_commits", not commits)
    if not isinstance(all_commits, bool):
        raise AdvisoryError(f"advisory '{advisory_id}' affected.all_commits must be a boolean")
    return Affected(
        recipe=recipe.strip() if isinstance(recipe, str) else None,
        git=git.strip() if isinstance(git, str) else None,
        commits=tuple(item.strip() for item in commits if item.strip()),
        refs=tuple(item.strip() for item in refs if item.strip()),
        all_commits=all_commits,
    )


def _match_affected(
    item: Affected,
    *,
    recipe_name: str,
    git: str,
    commit: str | None,
    ref: str | None,
) -> str | None:
    if item.recipe and item.recipe != recipe_name:
        return None
    if item.git and item.git.rstrip("/") != git.rstrip("/"):
        return None
    if item.refs and (ref is None or ref not in item.refs):
        return None
    if item.all_commits or not item.commits:
        target = item.recipe or item.git or recipe_name
        return f"matches {target}"
    if commit is None:
        return None
    for pinned in item.commits:
        if commit == pinned or commit.startswith(pinned) or pinned.startswith(commit):
            return f"commit {commit} is listed"
    return None


def _required_string(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AdvisoryError(f"advisory is missing {key}")
    return value.strip()
