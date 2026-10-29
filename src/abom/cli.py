from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from abom.advisory import (
    AdvisoryError,
    format_advisory,
    format_advisory_line,
    load_advisories,
)
from abom.check import format_report, inspect_checkout, inspect_material, report_to_dict
from abom.lockfile import LockError, default_lock_path, read_lock, write_lock
from abom.recipe import (
    Recipe,
    RecipeError,
    catalog_paths,
    format_recipe,
    format_recipe_line,
    iter_recipes,
    load_recipe,
    private_catalogs_dir,
    validate_git_url,
)
from abom.wire import (
    WireError,
    link_skill,
    mcp_config_path,
    mcp_server_entry,
    merge_mcp_server,
    skill_dirs,
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


def cmd_install_recipe(name: str, *, locked: bool = False, lock_file: Path | None = None) -> str:
    recipe = _load_recipe(name)
    destination = tools_dir() / recipe.name
    if destination.exists() or destination.is_symlink():
        raise AbomError(f"tool '{recipe.name}' already installed")

    pin: str | None = None
    if locked:
        try:
            tools = read_lock(lock_file or default_lock_path())
        except LockError as exc:
            raise AbomError(str(exc)) from exc
        entry = tools.get(recipe.name)
        if entry is None:
            raise AbomError(f"lock file has no entry for '{recipe.name}'")
        pin = entry["commit"]

    try:
        if pin:
            _clone_at_commit(recipe.git, pin, destination)
        else:
            _clone_recipe(recipe, destination)
        commit = git_stdout(["rev-parse", "HEAD"], cwd=destination)
        if pin and commit != pin and not commit.startswith(pin) and not pin.startswith(commit):
            raise AbomError(f"checked out {commit}, expected lock commit {pin}")
        report = inspect_material(recipe, destination, commit=commit)
        if not report.ok:
            raise AbomError(format_report(report))
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
    if receipt and receipt.get("previous_commit"):
        lines.append(f"previous_commit: {receipt['previous_commit']}")
    commit = receipt.get("commit") if receipt else None
    if not isinstance(commit, str):
        commit = None
    lines.append(format_report(inspect_material(recipe, checkout, commit=commit)))
    return "\n".join(lines)


def cmd_check(name: str) -> str:
    report = _inspect_named(name)
    text = format_report(report)
    if not report.ok:
        raise AbomError(text)
    return text


def cmd_report(name: str) -> str:
    recipe = _load_recipe(name)
    report = _inspect_named(name)
    return json.dumps(report_to_dict(recipe, report), indent=2) + "\n"


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


def cmd_use(
    name: str,
    target_repo: str,
    *,
    agent: str = "cursor",
    link_name: str | None = None,
    global_scope: bool = False,
) -> str:
    checkout = tools_dir() / name
    if not checkout.exists() and not checkout.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    receipt = _read_receipt(name)
    kind = (receipt or {}).get("kind")
    if not kind:
        recipe = _load_recipe(name)
        kind = recipe.kind
    material = _material_root(name, checkout)
    target_root = Path(target_repo).expanduser()
    if not target_root.exists() or not target_root.is_dir():
        raise AbomError(f"target repository '{target_root}' must be an existing directory")
    target_root = target_root.resolve(strict=False)
    final_name = link_name or name
    messages: list[str] = []

    try:
        if kind == "skill":
            for directory in skill_dirs(agent, target_root):
                messages.append(link_skill(material, directory / final_name))
        elif kind == "mcp":
            agents = ("cursor", "claude") if agent == "all" else (agent,)
            entry = mcp_server_entry(name, material)
            for item in agents:
                if item not in {"cursor", "claude"}:
                    raise AbomError(f"mcp use supports cursor or claude, not '{item}'")
                path = mcp_config_path(item, target_root, global_scope=global_scope)
                messages.append(merge_mcp_server(path, final_name, entry))
        elif kind in {"prompt", "package"}:
            messages.append(cmd_link(name, str(target_root), final_name))
        else:
            raise AbomError(f"cannot use kind '{kind}'")
    except WireError as exc:
        raise AbomError(str(exc)) from exc
    return "\n".join(messages)


def cmd_upgrade(name: str | None = None) -> list[str]:
    names = [name] if name else cmd_search(None)
    if not names:
        raise AbomError("no installed tools to upgrade")
    lines: list[str] = []
    for item in names:
        lines.append(_upgrade_one(item))
    return lines


def cmd_rollback(name: str) -> str:
    receipt = _read_receipt(name)
    if receipt is None:
        raise AbomError(f"tool '{name}' has no receipt")
    previous = receipt.get("previous_commit")
    if not isinstance(previous, str) or not previous.strip():
        raise AbomError(f"tool '{name}' has no previous_commit to roll back to")
    git_url = receipt.get("git")
    if not isinstance(git_url, str) or not git_url.strip():
        raise AbomError(f"receipt for '{name}' is missing git")
    recipe = _recipe_from_receipt(receipt)
    destination = tools_dir() / name
    if not destination.exists() and not destination.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")

    with tempfile.TemporaryDirectory(prefix="abom-rollback-") as temporary:
        fresh = Path(temporary) / name
        _clone_at_commit(git_url, previous.strip(), fresh)
        commit = git_stdout(["rev-parse", "HEAD"], cwd=fresh)
        report = inspect_material(recipe, fresh, commit=commit)
        if not report.ok:
            raise AbomError(format_report(report))
        current = receipt.get("commit")
        _replace_checkout(destination, fresh)
        _write_receipt(
            recipe, commit, previous_commit=current if isinstance(current, str) else None
        )
    return f"rolled back {name} -> {commit}"


def cmd_lock(path: Path | None = None) -> str:
    lock_path = path or default_lock_path()
    tools: dict[str, dict] = {}
    for name in cmd_search(None):
        receipt = _read_receipt(name)
        if receipt is None or not receipt.get("commit"):
            raise AbomError(f"installed tool '{name}' has no receipt commit; reinstall it first")
        tools[name] = {
            "commit": receipt["commit"],
            "git": receipt.get("git"),
            "ref": receipt.get("ref"),
            "path": receipt.get("path"),
            "kind": receipt.get("kind"),
        }
    try:
        write_lock(lock_path, tools)
    except OSError as exc:
        raise AbomError(f"could not write lock file '{lock_path}': {exc}") from exc
    return f"wrote {lock_path} ({len(tools)} tools)"


def cmd_lock_check(path: Path | None = None) -> str:
    lock_path = path or default_lock_path()
    try:
        tools = read_lock(lock_path)
    except LockError as exc:
        raise AbomError(str(exc)) from exc
    if not tools:
        raise AbomError(f"lock file '{lock_path}' has no tools")
    problems: list[str] = []
    for name, entry in tools.items():
        receipt = _read_receipt(name)
        checkout = tools_dir() / name
        if not checkout.exists() and not checkout.is_symlink():
            problems.append(f"{name}: not installed")
            continue
        if receipt is None or not receipt.get("commit"):
            problems.append(f"{name}: missing receipt commit")
            continue
        if receipt["commit"] != entry["commit"]:
            problems.append(f"{name}: installed {receipt['commit']}, lock wants {entry['commit']}")
    if problems:
        raise AbomError("lock check failed:\n" + "\n".join(f"- {item}" for item in problems))
    return f"lock check passed ({len(tools)} tools)"


def cmd_catalogs() -> list[str]:
    try:
        paths = catalog_paths()
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc
    lines = [str(path) for path in paths]
    private = private_catalogs_dir()
    lines.append(f"private catalogs dir: {private}")
    return lines


def cmd_advisories(query: str | None = None) -> list[str]:
    try:
        advisories = load_advisories()
    except AdvisoryError as exc:
        raise AbomError(str(exc)) from exc
    if query:
        needle = query.lower()
        advisories = [
            item
            for item in advisories
            if needle in f"{item.id} {item.severity} {item.summary} {item.description}".lower()
        ]
    return [format_advisory_line(item) for item in advisories]


def cmd_advisory(advisory_id: str) -> str:
    try:
        advisories = load_advisories()
    except AdvisoryError as exc:
        raise AbomError(str(exc)) from exc
    for item in advisories:
        if item.id == advisory_id:
            return format_advisory(item)
    raise AbomError(f"no advisory named '{advisory_id}'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abom", description="Manage agent skills, prompts, and MCP checkouts"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install", help="Install a named recipe from recipes.json")
    install_parser.add_argument("name")
    install_parser.add_argument("git_url", nargs="?")
    install_parser.add_argument(
        "--locked",
        action="store_true",
        help="Install the commit pinned in abom.lock.json",
    )
    install_parser.add_argument("--lock-file", type=Path, help="Lock file path")

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

    report_parser = sub.add_parser(
        "report", help="Print a reproducible JSON security report for a recipe"
    )
    report_parser.add_argument("name")

    advisories_parser = sub.add_parser("advisories", help="List published abom advisories")
    advisories_parser.add_argument("query", nargs="?")

    advisory_parser = sub.add_parser("advisory", help="Show one abom advisory by id")
    advisory_parser.add_argument("id")

    remove_parser = sub.add_parser("remove", help="Remove an installed tool")
    remove_parser.add_argument("name")

    link_parser = sub.add_parser("link", help="Link an installed tool into a target repository")
    link_parser.add_argument("name")
    link_parser.add_argument("target_repo")
    link_parser.add_argument("--link-name")

    use_parser = sub.add_parser(
        "use",
        help="Wire an installed skill or MCP into Cursor, Claude, or Copilot paths",
    )
    use_parser.add_argument("name")
    use_parser.add_argument("target_repo")
    use_parser.add_argument(
        "--agent",
        default="cursor",
        choices=("cursor", "claude", "copilot", "agents", "all"),
        help="Which agent skill/MCP locations to update",
    )
    use_parser.add_argument("--link-name")
    use_parser.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Write MCP config under the home directory",
    )

    upgrade_parser = sub.add_parser(
        "upgrade", help="Move installed recipe checkouts to the recipe ref"
    )
    upgrade_parser.add_argument("name", nargs="?")

    rollback_parser = sub.add_parser("rollback", help="Restore the previous receipt commit")
    rollback_parser.add_argument("name")

    lock_parser = sub.add_parser("lock", help="Write abom.lock.json from installed receipt commits")
    lock_parser.add_argument("--path", type=Path, help="Lock file path")
    lock_parser.add_argument(
        "--check",
        action="store_true",
        help="Verify installed commits match the lock file",
    )

    sub.add_parser("catalogs", help="List merged catalog files and the private catalogs directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            if args.git_url:
                print(cmd_install(args.name, args.git_url))
            else:
                print(cmd_install_recipe(args.name, locked=args.locked, lock_file=args.lock_file))
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
        elif args.command == "report":
            print(cmd_report(args.name), end="")
        elif args.command == "advisories":
            for line in cmd_advisories(args.query):
                print(line)
        elif args.command == "advisory":
            print(cmd_advisory(args.id))
        elif args.command == "remove":
            print(cmd_remove(args.name))
        elif args.command == "link":
            print(cmd_link(args.name, args.target_repo, args.link_name))
        elif args.command == "use":
            print(
                cmd_use(
                    args.name,
                    args.target_repo,
                    agent=args.agent,
                    link_name=args.link_name,
                    global_scope=args.global_scope,
                )
            )
        elif args.command == "upgrade":
            for line in cmd_upgrade(args.name):
                print(line)
        elif args.command == "rollback":
            print(cmd_rollback(args.name))
        elif args.command == "lock":
            if args.check:
                print(cmd_lock_check(args.path))
            else:
                print(cmd_lock(args.path))
        elif args.command == "catalogs":
            for line in cmd_catalogs():
                print(line)
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


def _inspect_named(name: str):
    recipe = _load_recipe(name)
    checkout = tools_dir() / recipe.name
    if checkout.exists() or checkout.is_symlink():
        receipt = _read_receipt(name)
        commit = receipt.get("commit") if receipt else None
        if not isinstance(commit, str):
            try:
                commit = git_stdout(["rev-parse", "HEAD"], cwd=checkout)
            except AbomError:
                commit = None
        return inspect_material(recipe, checkout, commit=commit)
    with tempfile.TemporaryDirectory(prefix="abom-check-") as temporary:
        destination = Path(temporary) / recipe.name
        _clone_recipe(recipe, destination)
        commit = git_stdout(["rev-parse", "HEAD"], cwd=destination)
        return inspect_material(recipe, destination, commit=commit)


def _clone_recipe(recipe: Recipe, destination: Path) -> None:
    run_git(["clone", "--depth", "1", "--branch", recipe.ref, recipe.git, str(destination)])
    if recipe.path is not None and not (destination / recipe.path).exists():
        raise AbomError(f"recipe '{recipe.name}' path '{recipe.path}' was not found after clone")


def _clone_at_commit(git_url: str, commit: str, destination: Path) -> None:
    try:
        validate_git_url(git_url)
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc
    destination.mkdir(parents=True, exist_ok=False)
    run_git(["init"], cwd=destination)
    run_git(["remote", "add", "origin", git_url], cwd=destination)
    run_git(["fetch", "--depth", "1", "origin", commit], cwd=destination)
    run_git(["checkout", "--force", "FETCH_HEAD"], cwd=destination)


def _upgrade_one(name: str) -> str:
    receipt = _read_receipt(name)
    if receipt is None:
        raise AbomError(f"tool '{name}' has no receipt; only catalog installs can be upgraded")
    recipe = _recipe_from_receipt(receipt)
    destination = tools_dir() / name
    if not destination.exists() and not destination.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    old_commit = receipt.get("commit")
    with tempfile.TemporaryDirectory(prefix="abom-upgrade-") as temporary:
        fresh = Path(temporary) / name
        _clone_recipe(recipe, fresh)
        new_commit = git_stdout(["rev-parse", "HEAD"], cwd=fresh)
        report = inspect_material(recipe, fresh, commit=new_commit)
        if not report.ok:
            raise AbomError(format_report(report))
        if isinstance(old_commit, str) and new_commit == old_commit:
            return f"unchanged {name} @ {new_commit}"
        _replace_checkout(destination, fresh)
        _write_receipt(
            recipe,
            new_commit,
            previous_commit=old_commit if isinstance(old_commit, str) else None,
        )
    return f"upgraded {name} -> {new_commit}"


def _replace_checkout(destination: Path, fresh: Path) -> None:
    backup = destination.with_name(destination.name + ".abom-old")
    if backup.exists() or backup.is_symlink():
        _discard_checkout(backup)
    destination.rename(backup)
    try:
        shutil.move(str(fresh), str(destination))
    except Exception:
        if destination.exists() or destination.is_symlink():
            _discard_checkout(destination)
        backup.rename(destination)
        raise
    _discard_checkout(backup)


def _material_root(name: str, checkout: Path) -> Path:
    receipt = _read_receipt(name)
    subpath = receipt.get("path") if receipt else None
    if not subpath:
        return checkout
    material = checkout / subpath
    if not material.exists() and not material.is_symlink():
        raise AbomError(f"installed recipe '{name}' is missing path '{subpath}'")
    return material


def _recipe_from_receipt(receipt: dict) -> Recipe:
    try:
        return Recipe(
            name=str(receipt["name"]),
            kind=str(receipt["kind"]),
            description=str(receipt.get("description") or receipt["name"]),
            homepage=str(receipt.get("homepage") or ""),
            license=str(receipt.get("license") or "MIT"),
            git=str(receipt["git"]),
            ref=str(receipt.get("ref") or "main"),
            path=receipt.get("path"),
        )
    except KeyError as exc:
        raise AbomError(f"receipt is missing {exc}") from exc


def _write_receipt(recipe: Recipe, commit: str, previous_commit: str | None = None) -> None:
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
    if previous_commit:
        payload["previous_commit"] = previous_commit
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
