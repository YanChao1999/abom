from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from abom.recipe import Recipe, RecipeError, license_terms

# Commands that turn a clone or npm lifecycle script into a remote shell.
_REMOTE_SHELL = re.compile(
    r"(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b"
    r"|base64\s+(?:-d|--decode)\b[^\n|]*\|\s*(?:ba)?sh\b"
    r"|\b(?:bash|sh)\s+-c\b"
    r"|/dev/tcp/",
    re.IGNORECASE,
)
# Instruction-override lines. The material is data for an agent, so these are treated as hostile.
_PROMPT_INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions"
    r"|disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    re.IGNORECASE,
)
_EXCERPT_BYTES = 6_000
_EXCERPT_FILES = 3
_LIFECYCLE_SCRIPTS = ("preinstall", "install", "postinstall", "prepare", "preprepare", "prepublish")
_PROMPT_SUFFIXES = {".md", ".markdown", ".txt", ".prompt"}
_TEXT_SCAN_BYTES = 256_000


@dataclass(frozen=True)
class CheckReport:
    ok: bool
    findings: tuple[str, ...]


def inspect_material(recipe: Recipe, checkout: Path) -> CheckReport:
    material = checkout if recipe.path is None else checkout / recipe.path
    findings: list[str] = []
    if not material.exists() and not material.is_symlink():
        findings.append(f"{recipe.kind} path '{recipe.path or '.'}' is missing")
        return CheckReport(False, tuple(findings))

    findings.extend(_symlink_findings(checkout, material))
    findings.extend(_kind_findings(recipe.kind, material))
    findings.extend(_license_findings(recipe.license, checkout, material))
    return CheckReport(not findings, tuple(findings))


def inspect_checkout(checkout: Path) -> CheckReport:
    """Scan an ad-hoc clone that has no recipe kind."""
    findings = _symlink_findings(checkout, checkout)
    return CheckReport(not findings, tuple(findings))


def material_excerpt(recipe: Recipe, checkout: Path) -> str:
    """Bounded text Copilot can review without a tool that reads the checkout."""
    material = checkout if recipe.path is None else checkout / recipe.path
    lines = [
        f"name: {recipe.name}",
        f"kind: {recipe.kind}",
        f"declared license: {recipe.license}",
    ]
    for path in _excerpt_files(recipe.kind, material):
        text = _read_text(path) or ""
        lines.append(f"\n--- file: {path.name} ---\n{text[:_EXCERPT_BYTES]}")
    return "\n".join(lines)


def format_report(report: CheckReport) -> str:
    if report.ok:
        return "check: pass"
    lines = ["check: fail", *(f"- {finding}" for finding in report.findings)]
    return "\n".join(lines)


def _kind_findings(kind: str, material: Path) -> list[str]:
    if kind == "skill":
        return _skill_findings(material)
    if kind == "prompt":
        return _prompt_findings(material)
    if kind == "package":
        return _package_findings(material)
    if kind == "mcp":
        return _mcp_findings(material)
    return [f"unknown kind '{kind}'"]


def _skill_findings(material: Path) -> list[str]:
    if not material.is_dir():
        return ["skill must be a directory containing SKILL.md"]
    skill_file = material / "SKILL.md"
    if not skill_file.is_file():
        return ["skill is missing SKILL.md"]
    text = _read_text(skill_file)
    if text is None:
        return ["SKILL.md is not a text file"]
    frontmatter = _frontmatter(text)
    findings: list[str] = []
    if not frontmatter.get("name") or not frontmatter.get("description"):
        findings.append("SKILL.md frontmatter must include name and description")
    if _REMOTE_SHELL.search(text):
        findings.append("SKILL.md contains a remote shell command")
    if _PROMPT_INJECTION.search(text):
        findings.append("SKILL.md contains a prompt-injection instruction")
    return findings


def _prompt_findings(material: Path) -> list[str]:
    files = _prompt_files(material)
    if not files:
        return ["prompt must be a text file or a directory of prompt files"]
    findings: list[str] = []
    for path in files:
        text = _read_text(path)
        if text is None:
            findings.append(f"prompt file is not text: {path.name}")
            continue
        if not text.strip():
            findings.append(f"prompt file is empty: {path.name}")
        elif _REMOTE_SHELL.search(text):
            findings.append(f"prompt file contains a remote shell command: {path.name}")
        elif _PROMPT_INJECTION.search(text):
            findings.append(f"prompt file contains a prompt-injection instruction: {path.name}")
    return findings


def _package_findings(material: Path) -> list[str]:
    if not material.is_dir():
        return ["package must be a directory containing package.json or pyproject.toml"]
    if (material / "package.json").is_file():
        return _package_json_findings(material / "package.json", required=True)
    if (material / "pyproject.toml").is_file():
        return _pyproject_findings(material / "pyproject.toml")
    return ["package must contain package.json or pyproject.toml"]


def _pyproject_findings(path: Path) -> list[str]:
    text = _read_text(path)
    if text is None:
        return ["pyproject.toml is not a text file"]
    findings: list[str] = []
    if not re.search(r'(?m)^name\s*=\s*["\'][^"\']+["\']', text):
        findings.append("pyproject.toml is missing project name")
    if not re.search(r"(?m)^license\s*=", text):
        findings.append("pyproject.toml is missing license")
    return findings


def _mcp_findings(material: Path) -> list[str]:
    if not material.is_dir():
        return ["mcp must be a directory"]
    markers = ("package.json", "pyproject.toml", "index.ts", "index.js", "server.py", "main.py")
    if not any((material / name).is_file() for name in markers):
        return ["mcp directory has no server manifest or entry file"]
    package_json = material / "package.json"
    if package_json.is_file():
        return _package_json_findings(package_json, required=False)
    return []


def _package_json_findings(path: Path, *, required: bool) -> list[str]:
    if not path.is_file():
        return ["package.json is missing"] if required else []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["package.json is not valid JSON text"]
    if not isinstance(data, dict):
        return ["package.json must be an object"]
    findings: list[str] = []
    if required and (not isinstance(data.get("name"), str) or not str(data.get("name")).strip()):
        findings.append("package.json is missing name")
    scripts = data.get("scripts", {})
    if scripts is None:
        scripts = {}
    if not isinstance(scripts, dict):
        return [*findings, "package.json scripts must be an object"]
    for key in _LIFECYCLE_SCRIPTS:
        command = scripts.get(key)
        if not isinstance(command, str) or not command.strip():
            continue
        if _REMOTE_SHELL.search(command):
            findings.append(f"package.json {key} script looks like a remote shell: {command}")
    return findings


def _excerpt_files(kind: str, material: Path) -> list[Path]:
    if kind == "skill" and (material / "SKILL.md").is_file():
        return [material / "SKILL.md"]
    if kind == "prompt":
        return _prompt_files(material)[:_EXCERPT_FILES]
    if material.is_file():
        return [material]
    preferred = (
        "SKILL.md",
        "package.json",
        "pyproject.toml",
        "README.md",
        "index.ts",
        "index.js",
        "server.py",
        "main.py",
    )
    return [material / name for name in preferred if (material / name).is_file()][:_EXCERPT_FILES]


def _prompt_files(material: Path) -> list[Path]:
    if material.is_file():
        return [material] if _is_prompt_file(material) else []
    if not material.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(material, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _SKIP_DIRS and not (Path(dirpath) / name).is_symlink()
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            if _is_prompt_file(path):
                found.append(path)
    return found


def _is_prompt_file(path: Path) -> bool:
    return path.suffix.lower() in _PROMPT_SUFFIXES or path.name.lower() in {"prompt", "prompts.md"}


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip("\"'")
    return data


def _read_text(path: Path) -> str | None:
    try:
        blob = path.read_bytes()[:_TEXT_SCAN_BYTES]
    except OSError:
        return None
    if b"\x00" in blob:
        return None
    return blob.decode("utf-8", errors="replace")


_LICENSE_NAME = re.compile(r"^(?:LICENSE|LICENCE|COPYING)(?:[.-].*)?$", re.IGNORECASE)
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _license_findings(declared: str, checkout: Path, material: Path) -> list[str]:
    try:
        operator, required = license_terms(declared)
    except RecipeError as exc:
        return [str(exc)]
    files = _license_files(checkout, material)
    if not files:
        return ["checkout has no license file for the declared license"]
    detected: set[str] = set()
    for path in files:
        text = _read_text(path)
        if text:
            detected.update(_detect_licenses(text))
    if not detected:
        names = ", ".join(path.name for path in files)
        return [f"could not recognize a license in {names}"]
    satisfied = (
        all(item in detected for item in required)
        if operator == "AND"
        else any(item in detected for item in required)
    )
    if satisfied:
        return []
    found = ", ".join(sorted(detected))
    return [f"declared license {declared} does not match checkout license {found}"]


def _license_files(checkout: Path, material: Path) -> list[Path]:
    directories = _license_directories(checkout, material)
    files: list[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for child in directory.iterdir():
            if child.is_file() and not child.is_symlink() and _LICENSE_NAME.match(child.name):
                resolved = child.resolve(strict=False)
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(child)
    if material.is_dir() and not material.is_symlink():
        for dirpath, dirnames, filenames in os.walk(material, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in _SKIP_DIRS and not (Path(dirpath) / name).is_symlink()
            ]
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_symlink() or not _LICENSE_NAME.match(name):
                    continue
                resolved = path.resolve(strict=False)
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(path)
    return files


def _license_directories(checkout: Path, material: Path) -> list[Path]:
    root = checkout.resolve(strict=False)
    start = material.resolve(strict=False)
    if not start.is_dir():
        start = start.parent
    directories: list[Path] = []
    current = start
    while True:
        directories.append(current)
        if current == root:
            break
        if root not in current.parents:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return directories


def _detect_licenses(text: str) -> set[str]:
    lowered = text.lower()
    detected: set[str] = set()
    if "cc0 1.0" in lowered or "cc0-1.0" in lowered or "creative commons zero" in lowered:
        detected.add("CC0-1.0")
    if "apache license" in lowered and "version 2.0" in lowered:
        detected.add("Apache-2.0")
    if "mit license" in lowered or "permission is hereby granted, free of charge" in lowered:
        detected.add("MIT")
    if "mozilla public license, version 2.0" in lowered:
        detected.add("MPL-2.0")
    return detected


def _symlink_findings(checkout: Path, material: Path) -> list[str]:
    root = checkout.resolve(strict=False)
    findings: list[str] = []
    for path in _symlink_candidates(material):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=False)
        if not _is_inside(root, target):
            findings.append(f"symlink escapes checkout: {path.name} -> {os.readlink(path)}")
    return findings


def _symlink_candidates(material: Path) -> list[Path]:
    candidates = [material]
    if material.is_dir() and not material.is_symlink():
        for dirpath, dirnames, filenames in os.walk(material, followlinks=False):
            dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
            for name in [*dirnames, *filenames]:
                candidates.append(Path(dirpath) / name)
    return candidates


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
