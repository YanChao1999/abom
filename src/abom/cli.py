from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from abom.check import format_report, inspect_checkout, inspect_material
from abom.recipe import (
    Recipe,
    RecipeError,
    format_recipe,
    format_recipe_line,
    iter_recipes,
    load_recipe,
    validate_git_url,
)


class AbomError(Exception):
    pass


def abom_home() -> Path:
    root = os.environ.get("ABOM_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".abom").resolve(strict=False)


def tools_dir() -> Path:
    path = abom_home() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipts_dir() -> Path:
    path = abom_home() / "receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipt_path(name: str) -> Path:
    return abom_home() / "receipts" / f"{name}.json"


def run_git(args: list[str], cwd: Path | None = None) -> None:
    try:
        subprocess.run(
            ["git", *_git_safety_args(), *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise AbomError(stderr or f"git {' '.join(args)} failed") from exc


def git_stdout(args: list[str], cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *_git_safety_args(), *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise AbomError(stderr or f"git {' '.join(args)} failed") from exc
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _git_safety_args() -> list[str]:
    hooks = abom_home() / "disabled-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    return [
        "-c",
        f"core.hooksPath={hooks}",
        "-c",
        "core.fsmonitor=",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "protocol.fd.allow=never",
    ]


def cmd_install(name: str, git_url: str) -> str:
    try:
        validate_git_url(git_url)
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc
    destination = tools_dir() / name
    if destination.exists() or destination.is_symlink():
        raise AbomError(f"tool '{name}' already installed")
    try:
        run_git(["clone", "--depth", "1", git_url, str(destination)])
        report = inspect_checkout(destination)
        if not report.ok:
            raise AbomError(format_report(report))
    except Exception:
        _discard_checkout(destination)
        raise
    return f"installed {name} -> {destination}"


def cmd_install_recipe(name: str) -> str:
    recipe = _load_recipe(name)
    destination = tools_dir() / recipe.name
    if destination.exists() or destination.is_symlink():
        raise AbomError(f"tool '{recipe.name}' already installed")

    try:
        _clone_recipe(recipe, destination)
        report = inspect_material(recipe, destination)
        if not report.ok:
            raise AbomError(format_report(report))
        commit = git_stdout(["rev-parse", "HEAD"], cwd=destination)
        _write_receipt(recipe, commit)
    except Exception:
        _discard_checkout(destination)
        raise
    return f"installed {recipe.name} -> {destination}"


def cmd_recipes(query: str | None) -> list[str]:
    try:
        recipes = iter_recipes(query)
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc
    return [format_recipe_line(recipe) for recipe in recipes]


def cmd_info(name: str) -> str:
    try:
        recipe = load_recipe(name)
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc
    return format_recipe(recipe)


def cmd_show(name: str) -> str:
    recipe = _load_recipe(name)
    lines = [format_recipe(recipe), ""]
    checkout = tools_dir() / recipe.name
    if not checkout.exists() and not checkout.is_symlink():
        lines.append("installed: no")
        lines.append("check: not run")
        return "\n".join(lines)
    lines.append("installed: yes")
    receipt = _read_receipt(recipe.name)
    if receipt and receipt.get("commit"):
        lines.append(f"commit: {receipt['commit']}")
    lines.append(format_report(inspect_material(recipe, checkout)))
    return "\n".join(lines)


def cmd_check(name: str) -> str:
    recipe = _load_recipe(name)
    checkout = tools_dir() / recipe.name
    if checkout.exists() or checkout.is_symlink():
        report = inspect_material(recipe, checkout)
    else:
        with tempfile.TemporaryDirectory(prefix="abom-check-") as temporary:
            destination = Path(temporary) / recipe.name
            _clone_recipe(recipe, destination)
            report = inspect_material(recipe, destination)
    text = format_report(report)
    if not report.ok:
        raise AbomError(text)
    return text


def cmd_search(query: str | None) -> list[str]:
    installed = sorted(p.name for p in tools_dir().iterdir() if p.is_dir() or p.is_symlink())
    if query:
        installed = [name for name in installed if query.lower() in name.lower()]
    return installed


def cmd_remove(name: str) -> str:
    destination = tools_dir() / name
    if not destination.exists() and not destination.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    if destination.is_symlink() or not destination.is_dir():
        destination.unlink()
    else:
        shutil.rmtree(destination)
    receipt = receipt_path(name)
    if receipt.exists():
        receipt.unlink()
    return f"removed {name}"


def cmd_link(name: str, target_repo: str, link_name: str | None) -> str:
    source_entry = tools_dir() / name
    if not source_entry.exists() and not source_entry.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    source_entry = _material_root(name, source_entry)

    target_root = Path(target_repo).expanduser()
    if not target_root.exists():
        raise AbomError(f"target repository '{target_root}' does not exist")
    if not target_root.is_dir():
        raise AbomError(f"target repository '{target_root}' is not a directory")
    target_root = target_root.resolve(strict=False)
    links_dir = target_root / ".abom"
    links_dir.mkdir(exist_ok=True)

    final_name = link_name or name
    destination = links_dir / final_name
    if destination.exists() or destination.is_symlink():
        raise AbomError(f"link '{destination}' already exists")

    link_target = os.path.relpath(source_entry, destination.parent)
    target_is_directory = (
        source_entry.resolve(strict=False).is_dir()
        if source_entry.is_symlink()
        else source_entry.is_dir()
    )
    destination.symlink_to(link_target, target_is_directory=target_is_directory)
    return f"linked {name} -> {destination}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abom", description="Manage agent skills, prompts, and MCP checkouts"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install", help="Install a named recipe from recipes.json")
    install_parser.add_argument("name")
    install_parser.add_argument("git_url", nargs="?")

    search_parser = sub.add_parser("search", help="Search installed tools")
    search_parser.add_argument("query", nargs="?")

    recipes_parser = sub.add_parser("recipes", help="List installable recipes")
    recipes_parser.add_argument("query", nargs="?")

    info_parser = sub.add_parser("info", help="Show a recipe from the catalog")
    info_parser.add_argument("name")

    show_parser = sub.add_parser(
        "show", help="Show a recipe, whether it is installed, and the check result"
    )
    show_parser.add_argument("name")

    check_parser = sub.add_parser("check", help="Verify a recipe's kind and install safety")
    check_parser.add_argument("name")

    remove_parser = sub.add_parser("remove", help="Remove an installed tool")
    remove_parser.add_argument("name")

    link_parser = sub.add_parser("link", help="Link an installed tool into a target repository")
    link_parser.add_argument("name")
    link_parser.add_argument("target_repo")
    link_parser.add_argument("--link-name")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            if args.git_url:
                print(cmd_install(args.name, args.git_url))
            else:
                print(cmd_install_recipe(args.name))
        elif args.command == "search":
            for name in cmd_search(args.query):
                print(name)
        elif args.command == "recipes":
            for line in cmd_recipes(args.query):
                print(line)
        elif args.command == "info":
            print(cmd_info(args.name))
        elif args.command == "show":
            print(cmd_show(args.name))
        elif args.command == "check":
            print(cmd_check(args.name))
        elif args.command == "remove":
            print(cmd_remove(args.name))
        elif args.command == "link":
            print(cmd_link(args.name, args.target_repo, args.link_name))
        else:
            parser.error(f"unknown command: {args.command}")
    except AbomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def _load_recipe(name: str) -> Recipe:
    try:
        return load_recipe(name)
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc


def _clone_recipe(recipe: Recipe, destination: Path) -> None:
    run_git(["clone", "--depth", "1", "--branch", recipe.ref, recipe.git, str(destination)])
    if recipe.path is not None and not (destination / recipe.path).exists():
        raise AbomError(f"recipe '{recipe.name}' path '{recipe.path}' was not found after clone")


def _material_root(name: str, checkout: Path) -> Path:
    receipt = _read_receipt(name)
    subpath = receipt.get("path") if receipt else None
    if not subpath:
        return checkout
    material = checkout / subpath
    if not material.exists() and not material.is_symlink():
        raise AbomError(f"installed recipe '{name}' is missing path '{subpath}'")
    return material


def _write_receipt(recipe: Recipe, commit: str) -> None:
    payload = {
        "schema": "abom.receipt/v1",
        "name": recipe.name,
        "kind": recipe.kind,
        "description": recipe.description,
        "homepage": recipe.homepage,
        "license": recipe.license,
        "git": recipe.git,
        "ref": recipe.ref,
        "path": recipe.path,
        "commit": commit,
    }
    receipt = receipts_dir() / f"{recipe.name}.json"
    receipt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_receipt(name: str) -> dict | None:
    receipt = receipt_path(name)
    if not receipt.is_file():
        return None
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AbomError(f"could not read receipt for '{name}': {exc}") from exc
    if not isinstance(data, dict):
        raise AbomError(f"receipt for '{name}' is not an object")
    return data


def _discard_checkout(destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)


if __name__ == "__main__":
    raise SystemExit(main())
