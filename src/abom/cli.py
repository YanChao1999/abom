from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from abom.advisory import (
    AdvisoryError,
    advisory_feed_digest,
    format_advisory,
    format_advisory_line,
    load_advisories,
    load_rules_version,
    match_advisories,
)
from abom.check import format_report, inspect_checkout, inspect_material, report_to_dict
from abom.lockfile import (
    LockError,
    default_lock_path,
    material_digest,
    read_lock,
    write_lock,
)
from abom.policy import (
    Policy,
    PolicyError,
    ensure_policy_advisories,
    load_policy,
    policy_advisory_sources,
    policy_violations,
)
from abom.project import (
    ProjectError,
    find_workspace,
    init_project,
    load_project,
    record_tool_wire,
    remove_tool_wire,
    tool_wire,
)
from abom.recipe import (
    Recipe,
    RecipeError,
    catalog_sources,
    format_recipe,
    format_recipe_line,
    iter_recipes,
    load_recipe,
    private_catalogs_dir,
    validate_git_url,
)
from abom.taps import TapError, add_tap, load_tap_entries, remove_tap, update_taps
from abom.wire import (
    WireError,
    link_prompt,
    link_skill,
    mcp_config_path,
    mcp_server_entry,
    merge_mcp_server,
    prompt_dirs,
    remove_mcp_server,
    skill_dirs,
    unlink_path,
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


def checkout_path(name: str, commit: str) -> Path:
    """Content-addressed checkout. Two commits of one recipe do not share a tree."""
    if not commit or commit in {".", ".."} or "/" in commit or "\\" in commit:
        raise AbomError(f"invalid commit '{commit}'")
    return abom_home() / "store" / name / commit


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


def _commits_match(actual: str, wanted: str) -> bool:
    return actual == wanted or actual.startswith(wanted) or wanted.startswith(actual)


def _policy_for(root: Path | None = None) -> Policy | None:
    try:
        workspace = root if root is not None else find_workspace()
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc
    try:
        return load_policy(workspace)
    except PolicyError as exc:
        raise AbomError(str(exc)) from exc


def _active_link(name: str) -> Path:
    return tools_dir() / name


def _activate_link(name: str, commit: str) -> Path:
    """Point tools/<name> at one stored tree. Other commits stay on disk."""
    target = checkout_path(name, commit)
    link = _active_link(name)
    desired = Path(os.path.relpath(target, link.parent))
    if link.is_symlink() and os.readlink(link) == str(desired):
        return target
    if link.is_symlink() or (link.exists() and not link.is_dir()):
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    link.symlink_to(desired, target_is_directory=True)
    return target


def _activate_receipt(recipe: Recipe, commit: str) -> None:
    existing = _read_receipt(recipe.name)
    previous = None
    if existing and isinstance(existing.get("commit"), str) and existing["commit"] != commit:
        previous = existing["commit"]
    _write_receipt(recipe, commit, previous_commit=previous)
    _activate_link(recipe.name, commit)


def _active_checkout(name: str) -> Path:
    link = _active_link(name)
    if not link.exists() and not link.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    return link.resolve() if link.is_symlink() else link


def _checkout_for_use(name: str, workspace: Path) -> Path:
    lock_path = workspace / "abom.lock.json"
    if lock_path.is_file():
        try:
            tools = read_lock(lock_path)
        except LockError as exc:
            raise AbomError(str(exc)) from exc
        entry = tools.get(name)
        commit = entry.get("commit") if entry else None
        if isinstance(commit, str) and commit:
            dest = checkout_path(name, commit)
            if not dest.exists():
                raise AbomError(f"tool '{name}' commit {commit} is not in the store; run abom sync")
            return dest
    return _active_checkout(name)


def _checkout_commit(checkout: Path) -> str | None:
    try:
        return git_stdout(["rev-parse", "HEAD"], cwd=checkout)
    except AbomError:
        return None


def _tree_material(checkout: Path, subpath: object) -> Path:
    if not isinstance(subpath, str) or not subpath.strip():
        return checkout
    material = checkout / subpath
    if not material.exists() and not material.is_symlink():
        raise AbomError(f"installed material is missing path '{subpath}'")
    return material


def _write_project_lock(lock_path: Path, name: str, *, keep_existing: bool) -> str:
    """Update one lock entry. An existing pin is kept when the project already recorded it."""
    existing: dict[str, dict] = {}
    if lock_path.is_file():
        try:
            existing = read_lock(lock_path)
        except LockError as exc:
            raise AbomError(str(exc)) from exc
    current = existing.get(name) or {}
    pinned = current.get("commit") if keep_existing else None
    if isinstance(pinned, str) and checkout_path(name, pinned).exists():
        receipt = _read_receipt(name) or {}
        receipt = {
            "commit": pinned,
            "git": current.get("git") or receipt.get("git"),
            "ref": current.get("ref") or receipt.get("ref"),
            "path": current.get("path") if current.get("path") is not None else receipt.get("path"),
            "kind": current.get("kind") or receipt.get("kind"),
        }
        checkout = checkout_path(name, pinned)
    else:
        receipt = _read_receipt(name)
        if receipt is None or not receipt.get("commit"):
            raise AbomError(f"installed tool '{name}' has no receipt commit; reinstall it first")
        checkout = checkout_path(name, receipt["commit"])
        if not checkout.exists():
            checkout = _active_checkout(name)
    try:
        digest = material_digest(_tree_material(checkout, receipt.get("path")))
    except LockError as exc:
        raise AbomError(str(exc)) from exc
    existing[name] = _lock_entry(receipt, digest, lock_path)
    try:
        write_lock(lock_path, existing)
    except OSError as exc:
        raise AbomError(f"could not write lock file '{lock_path}': {exc}") from exc
    return f"wrote {lock_path} ({len(existing)} tools)"


def _lock_entry(receipt: dict, digest: str, lock_path: Path) -> dict:
    policy = _policy_for(lock_path.parent)
    try:
        rules_version = load_rules_version()
        feed = advisory_feed_digest(policy_advisory_sources(policy))
    except (AdvisoryError, PolicyError) as exc:
        raise AbomError(str(exc)) from exc
    return {
        "commit": receipt["commit"],
        "digest": digest,
        "rules_version": rules_version,
        "advisory_digest": feed,
        "git": receipt.get("git"),
        "ref": receipt.get("ref"),
        "path": receipt.get("path"),
        "kind": receipt.get("kind"),
    }


def _recipe_for_lock_entry(name: str, entry: dict) -> Recipe:
    recipe = _load_recipe(name)
    git = entry.get("git")
    if not isinstance(git, str) or not git.strip():
        return recipe
    ref = entry.get("ref")
    path = entry.get("path")
    return replace(
        recipe,
        git=git.strip(),
        ref=ref.strip() if isinstance(ref, str) and ref.strip() else recipe.ref,
        path=path.strip() if isinstance(path, str) and path.strip() else recipe.path,
    )


def _publish_tree(recipe: Recipe, fresh: Path, policy: Policy | None) -> tuple[Path, str]:
    commit = git_stdout(["rev-parse", "HEAD"], cwd=fresh)
    report = inspect_material(recipe, fresh, commit=commit, policy=policy)
    if not report.ok:
        raise AbomError(format_report(report))
    dest = checkout_path(recipe.name, commit)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(fresh), str(dest))
    return dest, commit


def ensure_checkout(
    recipe: Recipe,
    commit: str | None = None,
    *,
    policy: Policy | None = None,
    activate: bool = True,
) -> tuple[Path, str]:
    """Install recipe@commit under the store. A different commit is left in place."""
    if commit:
        dest = checkout_path(recipe.name, commit)
        if dest.exists():
            actual = git_stdout(["rev-parse", "HEAD"], cwd=dest)
            if not _commits_match(actual, commit):
                raise AbomError(f"store at {dest} is {actual}, expected {commit}")
            report = inspect_material(recipe, dest, commit=actual, policy=policy)
            if not report.ok:
                raise AbomError(format_report(report))
            if activate:
                _activate_receipt(recipe, actual)
            return dest, actual
        with tempfile.TemporaryDirectory(prefix="abom-store-") as temporary:
            fresh = Path(temporary) / recipe.name
            _clone_at_commit(recipe.git, commit, fresh)
            actual = git_stdout(["rev-parse", "HEAD"], cwd=fresh)
            if not _commits_match(actual, commit):
                raise AbomError(f"checked out {actual}, expected lock commit {commit}")
            dest, actual = _publish_tree(recipe, fresh, policy)
        if activate:
            _activate_receipt(recipe, actual)
        return dest, actual

    with tempfile.TemporaryDirectory(prefix="abom-store-") as temporary:
        fresh = Path(temporary) / recipe.name
        _clone_recipe(recipe, fresh)
        dest, actual = _publish_tree(recipe, fresh, policy)
    if activate:
        _activate_receipt(recipe, actual)
    return dest, actual


def cmd_install_recipe(
    name: str,
    *,
    locked: bool = False,
    lock_file: Path | None = None,
    use_target: str | None = None,
    agent: str | None = None,
    workspace: str | None = None,
) -> str:
    recipe = _load_recipe(name)
    link = _active_link(recipe.name)
    if link.exists() or link.is_symlink():
        raise AbomError(f"tool '{recipe.name}' already installed")

    pin: str | None = None
    policy_root: Path | None = None
    if locked:
        try:
            tools = read_lock(lock_file or default_lock_path())
        except LockError as exc:
            raise AbomError(str(exc)) from exc
        entry = tools.get(recipe.name)
        if entry is None:
            raise AbomError(f"lock file has no entry for '{recipe.name}'")
        pin = entry["commit"]
        policy_root = (lock_file or default_lock_path()).parent
    if use_target is not None:
        if use_target in {".", ""}:
            try:
                policy_root = find_workspace()
            except ProjectError as exc:
                raise AbomError(str(exc)) from exc
        else:
            policy_root = Path(use_target).expanduser().resolve(strict=False)
    policy = _policy_for(policy_root)

    try:
        destination, _commit = ensure_checkout(recipe, pin, policy=policy)
    except Exception:
        if link.is_symlink() or (link.exists() and not link.is_dir()):
            link.unlink()
        raise
    lines = [f"installed {recipe.name} -> {destination}"]
    if use_target is not None:
        target = None if use_target in {".", ""} else use_target
        lines.append(
            cmd_use(
                recipe.name,
                target,
                agent=agent,
                workspace=workspace,
            )
        )
    return "\n".join(lines)


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
    if receipt and isinstance(receipt.get("history"), list) and receipt["history"]:
        lines.append("history: " + ", ".join(str(item) for item in receipt["history"][:5]))
    if recipe.tap == "core":
        lines.append("trust: public core")
    elif recipe.tap:
        lines.append(f"trust: tap/{recipe.tap}")
    try:
        workspace = find_workspace()
        local = tool_wire(workspace, recipe.name)
        if local or (workspace / ".abom" / "project.json").is_file():
            lines.append(f"workspace: {workspace}")
            if local.get("agent"):
                lines.append(f"wire.agent: {local['agent']}")
    except (ProjectError, AbomError):
        pass
    commit = receipt.get("commit") if receipt else None
    if not isinstance(commit, str):
        commit = None
    lines.append(
        format_report(inspect_material(recipe, checkout, commit=commit, policy=_policy_for()))
    )
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
    destination = _active_link(name)
    if not destination.exists() and not destination.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    receipt = _read_receipt(name)
    if destination.is_symlink() or not destination.is_dir():
        destination.unlink()
    else:
        shutil.rmtree(destination)
    if receipt and isinstance(receipt.get("commit"), str):
        _discard_checkout(checkout_path(name, receipt["commit"]))
        parent = checkout_path(name, receipt["commit"]).parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    receipt_file = receipt_path(name)
    if receipt_file.exists():
        receipt_file.unlink()
    return f"removed {name}"


def cmd_link(name: str, target_repo: str, link_name: str | None) -> str:
    source_entry = _active_link(name)
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
    target_repo: str | None = None,
    *,
    agent: str | None = None,
    link_name: str | None = None,
    global_scope: bool = False,
    workspace: str | None = None,
    commit: str | None = None,
) -> str:
    try:
        if target_repo:
            target_root = Path(target_repo).expanduser().resolve(strict=False)
            if not target_root.exists() or not target_root.is_dir():
                raise AbomError(f"target repository '{target_root}' must be an existing directory")
        else:
            target_root = find_workspace()
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc

    if commit:
        checkout = checkout_path(name, commit)
        if not checkout.exists():
            raise AbomError(f"tool '{name}' commit {commit} is not in the store; run abom sync")
    else:
        checkout = _checkout_for_use(name, target_root)
    receipt = _read_receipt(name)
    kind = (receipt or {}).get("kind")
    if not kind:
        recipe = _load_recipe(name)
        kind = recipe.kind
    # Resolve so the project link targets store/<name>/<commit>, not the shared tools symlink.
    material = _material_root(name, checkout).resolve()

    try:
        local = tool_wire(target_root, name)
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc

    resolved_agent = agent or local.get("agent") or "cursor"
    if not isinstance(resolved_agent, str) or not resolved_agent.strip():
        resolved_agent = "cursor"
    if link_name is None and isinstance(local.get("link_name"), str):
        link_name = local["link_name"]
    if not global_scope and local.get("global") is True:
        global_scope = True

    workspace_root = (
        Path(workspace).expanduser().resolve(strict=False) if workspace else target_root
    )
    final_name = link_name or name
    messages: list[str] = []

    try:
        if kind == "skill":
            for directory in skill_dirs(resolved_agent, target_root):
                messages.append(link_skill(material, directory / final_name))
        elif kind == "prompt":
            for directory in prompt_dirs(resolved_agent, target_root):
                dest = directory / final_name
                if material.is_file():
                    dest = directory / material.name
                messages.append(link_prompt(material, dest))
        elif kind == "mcp":
            agents = ("cursor", "claude") if resolved_agent == "all" else (resolved_agent,)
            entry = mcp_server_entry(name, material, workspace=workspace_root)
            for item in agents:
                if item not in {"cursor", "claude"}:
                    raise AbomError(f"mcp use supports cursor or claude, not '{item}'")
                path = mcp_config_path(item, target_root, global_scope=global_scope)
                messages.append(merge_mcp_server(path, final_name, entry))
        elif kind == "package":
            messages.append(cmd_link(name, str(target_root), final_name))
        else:
            raise AbomError(f"cannot use kind '{kind}'")
    except WireError as exc:
        raise AbomError(str(exc)) from exc

    try:
        wired_commit = _checkout_commit(checkout)
        record_tool_wire(
            target_root,
            name,
            agent=resolved_agent,
            link_name=final_name if final_name != name else None,
            global_scope=global_scope,
            commit=wired_commit,
        )
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc
    messages.append(f"workspace {target_root}")
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
    if not _active_link(name).exists() and not _active_link(name).is_symlink():
        raise AbomError(f"tool '{name}' is not installed")

    policy = _policy_for()
    dest, commit = ensure_checkout(recipe, previous.strip(), policy=policy)
    return f"rolled back {name} -> {commit if commit else dest.name}"


def cmd_lock(path: Path | None = None, *, names: list[str] | None = None) -> str:
    lock_path = path or default_lock_path()
    selected = names if names is not None else cmd_search(None)
    tools: dict[str, dict] = {}
    if names is not None and lock_path.is_file():
        try:
            tools = read_lock(lock_path)
        except LockError as exc:
            raise AbomError(str(exc)) from exc
    for name in selected:
        receipt = _read_receipt(name)
        if receipt is None or not receipt.get("commit"):
            raise AbomError(f"installed tool '{name}' has no receipt commit; reinstall it first")
        checkout = checkout_path(name, receipt["commit"])
        if not checkout.exists():
            link = _active_link(name)
            if not link.exists() and not link.is_symlink():
                raise AbomError(f"tool '{name}' is not installed")
            checkout = link.resolve() if link.is_symlink() else link
        try:
            digest = material_digest(_tree_material(checkout, receipt.get("path")))
        except LockError as exc:
            raise AbomError(str(exc)) from exc
        tools[name] = _lock_entry(receipt, digest, lock_path)
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
    policy = _policy_for(lock_path.parent)
    try:
        current_rules = load_rules_version()
        current_feed = advisory_feed_digest(policy_advisory_sources(policy))
    except (AdvisoryError, PolicyError) as exc:
        raise AbomError(str(exc)) from exc
    problems: list[str] = []
    for name, entry in tools.items():
        checkout = checkout_path(name, entry["commit"])
        if not checkout.exists():
            problems.append(f"{name}: commit {entry['commit']} is not in the store")
            continue
        wanted_digest = entry.get("digest")
        if isinstance(wanted_digest, str) and wanted_digest:
            try:
                actual = material_digest(_tree_material(checkout, entry.get("path")))
            except LockError as exc:
                problems.append(f"{name}: {exc}")
                continue
            if actual != wanted_digest:
                problems.append(f"{name}: digest mismatch (material changed since lock)")
        rules_version = entry.get("rules_version")
        if not isinstance(rules_version, str) or not rules_version:
            problems.append(f"{name}: lock entry is missing rules_version")
        elif rules_version != current_rules:
            problems.append(f"{name}: rules version {rules_version} != {current_rules}")
        feed = entry.get("advisory_digest")
        if not isinstance(feed, str) or not feed:
            problems.append(f"{name}: lock entry is missing advisory_digest")
        elif feed != current_feed:
            problems.append(f"{name}: advisory feed digest changed since lock")
    if problems:
        raise AbomError("lock check failed:\n" + "\n".join(f"- {item}" for item in problems))
    return f"lock check passed ({len(tools)} tools)"


def cmd_init(agent: str = "cursor") -> str:
    try:
        return init_project(agent=agent)
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc


def cmd_add(
    name: str,
    *,
    agent: str | None = None,
    locked: bool = False,
    lock_file: Path | None = None,
) -> list[str]:
    """Install (if needed), wire into the nearest workspace, and refresh the project lock."""
    try:
        workspace = find_workspace()
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc
    policy = _policy_for(workspace)
    recipe = _load_recipe(name)
    lock_path = lock_file or (workspace / "abom.lock.json")
    pin: str | None = None
    if locked:
        try:
            pinned = read_lock(lock_file or lock_path)
        except LockError as exc:
            raise AbomError(str(exc)) from exc
        entry = pinned.get(name)
        if entry is None:
            raise AbomError(f"lock file has no entry for '{name}'")
        pin = entry["commit"]
    # Always materialize the requested commit. An older pin of the same name stays in the store.
    destination, installed_commit = ensure_checkout(recipe, pin, policy=policy)
    lines: list[str] = [f"installed {name} -> {destination}"]
    lines.append(cmd_use(name, str(workspace), agent=agent, commit=installed_commit))
    lines.append(_write_project_lock(lock_path, name, keep_existing=False))
    return lines


def cmd_unuse(name: str, target_repo: str | None = None) -> str:
    try:
        workspace = (
            Path(target_repo).expanduser().resolve(strict=False)
            if target_repo
            else find_workspace()
        )
    except ProjectError as exc:
        raise AbomError(str(exc)) from exc
    if not workspace.is_dir():
        raise AbomError(f"workspace '{workspace}' must be an existing directory")

    local = tool_wire(workspace, name)
    agent = local.get("agent") or load_project(workspace).get("agent") or "cursor"
    link_name = local.get("link_name") or name
    global_scope = bool(local.get("global"))
    messages: list[str] = []

    receipt = _read_receipt(name)
    kind = (receipt or {}).get("kind")
    if not kind:
        try:
            kind = _load_recipe(name).kind
        except AbomError:
            kind = None

    try:
        if kind == "skill":
            for directory in skill_dirs(str(agent), workspace):
                messages.append(unlink_path(directory / link_name, label="skill"))
        elif kind == "prompt":
            for directory in prompt_dirs(str(agent), workspace):
                dest = directory / link_name
                material_name = None
                checkout = tools_dir() / name
                if checkout.exists() or checkout.is_symlink():
                    material = _material_root(name, checkout)
                    if material.is_file():
                        material_name = material.name
                if material_name:
                    messages.append(unlink_path(directory / material_name, label="prompt"))
                messages.append(unlink_path(dest, label="prompt"))
        elif kind == "mcp":
            agents = ("cursor", "claude") if agent == "all" else (str(agent),)
            for item in agents:
                if item not in {"cursor", "claude"}:
                    continue
                path = mcp_config_path(item, workspace, global_scope=global_scope)
                messages.append(remove_mcp_server(path, link_name))
        elif kind == "package":
            messages.append(unlink_path(workspace / ".abom" / link_name, label="package"))
        else:
            # Fall back to clearing known locations when kind is unknown.
            for directory in skill_dirs("all", workspace):
                messages.append(unlink_path(directory / link_name, label="skill"))
            for item in ("cursor", "claude"):
                path = mcp_config_path(item, workspace, global_scope=global_scope)
                messages.append(remove_mcp_server(path, link_name))
    except WireError as exc:
        raise AbomError(str(exc)) from exc

    remove_tool_wire(workspace, name)
    messages.append(f"removed wire for {name} from {workspace}")
    return "\n".join(messages)


def cmd_catalogs() -> list[str]:
    try:
        sources = catalog_sources()
    except RecipeError as exc:
        raise AbomError(str(exc)) from exc
    lines = [f"{name}  {path}" for name, path in sources]
    private = private_catalogs_dir()
    lines.append(f"private catalogs dir: {private}")
    lines.append("trust root: core (bundled public catalog)")
    return lines


def cmd_sync(lock_file: Path | None = None) -> list[str]:
    path = lock_file or default_lock_path()
    try:
        tools = read_lock(path)
    except LockError as exc:
        raise AbomError(str(exc)) from exc
    if not tools:
        raise AbomError(f"lock file '{path}' has no tools")
    policy = _policy_for(path.parent)
    lines: list[str] = []
    for name, entry in tools.items():
        wanted = entry["commit"]
        stored = checkout_path(name, wanted)
        receipt = _read_receipt(name)
        if stored.exists() and receipt and receipt.get("commit") == wanted:
            wanted_digest = entry.get("digest")
            if isinstance(wanted_digest, str) and wanted_digest:
                try:
                    actual = material_digest(
                        _tree_material(stored, entry.get("path") or receipt.get("path"))
                    )
                except LockError as exc:
                    raise AbomError(str(exc)) from exc
                if actual != wanted_digest:
                    raise AbomError(f"{name}: digest mismatch (material changed since lock)")
            lines.append(f"ok {name} @ {wanted}")
            continue
        recipe = _recipe_for_lock_entry(name, entry)
        _destination, commit = ensure_checkout(recipe, wanted, policy=policy)
        lines.append(f"synced {name} -> {commit}")
    return lines


def cmd_audit(lock_file: Path | None = None) -> str:
    """Replay advisories and project policy against a lock file. Does not install."""
    path = (lock_file or default_lock_path()).expanduser()
    try:
        tools = read_lock(path)
    except LockError as exc:
        raise AbomError(str(exc)) from exc
    if not tools:
        raise AbomError(f"lock file '{path}' has no tools")
    try:
        policy = load_policy(path.parent)
        ensure_policy_advisories(policy)
        extra = policy_advisory_sources(policy)
        rules_version = load_rules_version()
        feed = advisory_feed_digest(extra)
    except (AdvisoryError, PolicyError) as exc:
        raise AbomError(str(exc)) from exc

    lines = [
        "audit: pass",
        f"lock: {path}",
        f"rules: {rules_version}",
        f"advisory_digest: {feed}",
    ]
    failed = False
    for name, entry in tools.items():
        notes: list[str] = []
        try:
            hits = match_advisories(
                recipe_name=name,
                git=entry.get("git") or "",
                commit=entry.get("commit"),
                ref=entry.get("ref") if isinstance(entry.get("ref"), str) else None,
                extra=extra or None,
            )
        except AdvisoryError as exc:
            failed = True
            lines.append(f"- {name}: {exc}")
            continue
        locked_rules = entry.get("rules_version")
        if isinstance(locked_rules, str) and locked_rules != rules_version:
            notes.append(f"rules {locked_rules} != {rules_version}")
            failed = True
        locked_feed = entry.get("advisory_digest")
        if isinstance(locked_feed, str) and locked_feed != feed:
            notes.append("advisory feed changed since lock")
            failed = True
        kind = entry.get("kind") if isinstance(entry.get("kind"), str) else None
        license_id = None
        try:
            license_id = load_recipe(name).license
        except RecipeError:
            license_id = None
        for violation in policy_violations(policy, kind=kind, license_id=license_id):
            notes.append(violation)
            failed = True
        if hits:
            failed = True
            notes.append("advisories: " + ", ".join(hit.advisory.id for hit in hits))
        else:
            notes.append("advisories: none")
        lines.append(f"- {name} @ {entry['commit']}  " + "; ".join(notes))
    if failed:
        lines[0] = "audit: fail"
        raise AbomError("\n".join(lines))
    return "\n".join(lines)


def cmd_tap_list() -> list[str]:
    lines = ["core  bundled  public trust root"]
    try:
        for entry in load_tap_entries():
            kind = entry["kind"]
            if kind == "path":
                detail = entry["path"]
            elif kind == "url":
                detail = entry["url"]
            else:
                detail = (
                    f"{entry['git']}@{entry.get('ref', 'main')}:{entry.get('path', 'recipes.json')}"
                )
            lines.append(f"{entry['name']}  {kind}  {detail}")
    except TapError as exc:
        raise AbomError(str(exc)) from exc
    return lines


def cmd_tap_add(name: str, source: str, *, ref: str = "main", path: str = "recipes.json") -> str:
    try:
        return add_tap(name, source, ref=ref, path=path)
    except TapError as exc:
        raise AbomError(str(exc)) from exc


def cmd_tap_remove(name: str) -> str:
    try:
        return remove_tap(name)
    except TapError as exc:
        raise AbomError(str(exc)) from exc


def cmd_tap_update(name: str | None = None) -> list[str]:
    try:
        return update_taps(name)
    except TapError as exc:
        raise AbomError(str(exc)) from exc


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
    install_parser.add_argument(
        "--use",
        dest="use_target",
        nargs="?",
        const=".",
        metavar="TARGET",
        help="After install, wire into TARGET (default: nearest workspace from cwd)",
    )
    install_parser.add_argument(
        "--agent",
        default=None,
        choices=("cursor", "claude", "copilot", "agents", "codex", "all"),
        help="Agent paths to update when --use is set (default: project or cursor)",
    )
    install_parser.add_argument(
        "--workspace",
        help="MCP cwd/args root when --use is set (default: the resolved TARGET workspace)",
    )

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

    init_parser = sub.add_parser(
        "init", help="Initialize .abom/project.json in the nearest workspace"
    )
    init_parser.add_argument(
        "--agent",
        default="cursor",
        choices=("cursor", "claude", "copilot", "agents", "codex", "all"),
        help="Default agent for this project",
    )

    add_parser = sub.add_parser(
        "add",
        help="Install a recipe if needed, wire it into the nearest workspace, and update abom.lock.json",
    )
    add_parser.add_argument("name")
    add_parser.add_argument(
        "--agent",
        default=None,
        choices=("cursor", "claude", "copilot", "agents", "codex", "all"),
    )
    add_parser.add_argument("--locked", action="store_true")
    add_parser.add_argument("--lock-file", type=Path)

    unuse_parser = sub.add_parser(
        "unuse", help="Remove wiring for a tool from the nearest workspace"
    )
    unuse_parser.add_argument("name")
    unuse_parser.add_argument(
        "target_repo",
        nargs="?",
        help="Workspace root; omit to use the nearest workspace from cwd",
    )

    link_parser = sub.add_parser("link", help="Link an installed tool into a target repository")
    link_parser.add_argument("name")
    link_parser.add_argument("target_repo")
    link_parser.add_argument("--link-name")

    use_parser = sub.add_parser(
        "use",
        help="Wire an installed skill or MCP into Cursor, Claude, or Copilot paths",
    )
    use_parser.add_argument("name")
    use_parser.add_argument(
        "target_repo",
        nargs="?",
        help="Project to wire into; omit to use the nearest workspace from cwd",
    )
    use_parser.add_argument(
        "--agent",
        default=None,
        choices=("cursor", "claude", "copilot", "agents", "codex", "all"),
        help="Agent paths to update; omit to reuse .abom/project.json for this workspace",
    )
    use_parser.add_argument("--link-name")
    use_parser.add_argument(
        "--workspace",
        help="MCP cwd/args root; defaults to the resolved project workspace",
    )
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

    sync_parser = sub.add_parser(
        "sync",
        help="Install stored commits so they match abom.lock.json without removing other pins",
    )
    sync_parser.add_argument("--lock-file", type=Path, help="Lock file path")

    audit_parser = sub.add_parser(
        "audit",
        help="Replay advisories and project policy against a lock file without installing",
    )
    audit_parser.add_argument("--lock-file", type=Path, help="Lock file path")

    sub.add_parser("catalogs", help="List merged catalog taps and the private catalogs directory")

    tap_parser = sub.add_parser("tap", help="Manage private catalog taps over the public core root")
    tap_sub = tap_parser.add_subparsers(dest="tap_command", required=True)
    tap_sub.add_parser("list", help="List the core trust root and registered taps")
    tap_add = tap_sub.add_parser(
        "add", help="Register a local path, https catalog JSON, or git catalog as a named tap"
    )
    tap_add.add_argument("name")
    tap_add.add_argument("source", help="Local JSON path, https://…/catalog.json, or git URL")
    tap_add.add_argument("--ref", default="main", help="Git ref when source is a repository")
    tap_add.add_argument(
        "--path",
        default="recipes.json",
        help="Catalog path inside a git tap (default: recipes.json)",
    )
    tap_remove = tap_sub.add_parser("remove", help="Remove a registered tap")
    tap_remove.add_argument("name")
    tap_update = tap_sub.add_parser("update", help="Refresh cached url/git taps")
    tap_update.add_argument("name", nargs="?")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            if args.git_url:
                print(cmd_install(args.name, args.git_url))
            else:
                print(
                    cmd_install_recipe(
                        args.name,
                        locked=args.locked,
                        lock_file=args.lock_file,
                        use_target=args.use_target,
                        agent=args.agent,
                        workspace=args.workspace,
                    )
                )
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
        elif args.command == "init":
            print(cmd_init(agent=args.agent))
        elif args.command == "add":
            for line in cmd_add(
                args.name,
                agent=args.agent,
                locked=args.locked,
                lock_file=args.lock_file,
            ):
                print(line)
        elif args.command == "unuse":
            print(cmd_unuse(args.name, args.target_repo))
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
                    workspace=args.workspace,
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
        elif args.command == "sync":
            for line in cmd_sync(args.lock_file):
                print(line)
        elif args.command == "audit":
            print(cmd_audit(args.lock_file))
        elif args.command == "catalogs":
            for line in cmd_catalogs():
                print(line)
        elif args.command == "tap":
            if args.tap_command == "list":
                for line in cmd_tap_list():
                    print(line)
            elif args.tap_command == "add":
                print(cmd_tap_add(args.name, args.source, ref=args.ref, path=args.path))
            elif args.tap_command == "remove":
                print(cmd_tap_remove(args.name))
            elif args.tap_command == "update":
                for line in cmd_tap_update(args.name):
                    print(line)
            else:
                parser.error(f"unknown tap command: {args.tap_command}")
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
    policy = _policy_for()
    link = _active_link(recipe.name)
    if link.exists() or link.is_symlink():
        checkout = link.resolve() if link.is_symlink() else link
        receipt = _read_receipt(name)
        commit = receipt.get("commit") if receipt else None
        if not isinstance(commit, str):
            commit = _checkout_commit(checkout)
        return inspect_material(recipe, checkout, commit=commit, policy=policy)
    with tempfile.TemporaryDirectory(prefix="abom-check-") as temporary:
        destination = Path(temporary) / recipe.name
        _clone_recipe(recipe, destination)
        commit = git_stdout(["rev-parse", "HEAD"], cwd=destination)
        return inspect_material(recipe, destination, commit=commit, policy=policy)


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
    if not _active_link(name).exists() and not _active_link(name).is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    old_commit = receipt.get("commit")
    policy = _policy_for()
    with tempfile.TemporaryDirectory(prefix="abom-upgrade-") as temporary:
        fresh = Path(temporary) / name
        _clone_recipe(recipe, fresh)
        new_commit = git_stdout(["rev-parse", "HEAD"], cwd=fresh)
        if isinstance(old_commit, str) and new_commit == old_commit:
            return f"unchanged {name} @ {new_commit}"
        _publish_tree(recipe, fresh, policy)
    _activate_receipt(recipe, new_commit)
    return f"upgraded {name} -> {new_commit}"


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
    existing = _read_receipt(recipe.name) or {}
    history: list[str] = []
    raw_history = existing.get("history")
    if isinstance(raw_history, list):
        history = [item for item in raw_history if isinstance(item, str) and item.strip()]
    if previous_commit:
        history = [previous_commit, *[item for item in history if item != previous_commit]]
    history = history[:10]
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
        "tap": recipe.tap,
        "catalog": recipe.catalog,
    }
    if previous_commit:
        payload["previous_commit"] = previous_commit
    if history:
        payload["history"] = history
    path = receipts_dir() / f"{recipe.name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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
