from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


class AbomError(Exception):
    pass


def abom_home() -> Path:
    root = os.environ.get("ABOM_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".abom").resolve(strict=False)


def tools_dir() -> Path:
    path = abom_home() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_git(args: list[str], cwd: Path | None = None) -> None:
    try:
        subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise AbomError(stderr or f"git {' '.join(args)} failed") from exc


def cmd_install(name: str, git_url: str) -> str:
    destination = tools_dir() / name
    if destination.exists() or destination.is_symlink():
        raise AbomError(f"tool '{name}' already installed")
    run_git(["clone", "--depth", "1", git_url, str(destination)])
    return f"installed {name} -> {destination}"


def cmd_search(query: str | None) -> list[str]:
    installed = sorted(p.name for p in tools_dir().iterdir() if p.is_dir() or p.is_symlink())
    if query:
        installed = [name for name in installed if query.lower() in name.lower()]
    return installed


def cmd_remove(name: str) -> str:
    destination = tools_dir() / name
    if not destination.exists() and not destination.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")
    if destination.is_symlink():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)
    else:
        destination.unlink()
    return f"removed {name}"


def cmd_link(name: str, target_repo: str, link_name: str | None) -> str:
    source_entry = tools_dir() / name
    if not source_entry.exists() and not source_entry.is_symlink():
        raise AbomError(f"tool '{name}' is not installed")

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
        source_entry.resolve(strict=False).is_dir() if source_entry.is_symlink() else source_entry.is_dir()
    )
    destination.symlink_to(link_target, target_is_directory=target_is_directory)
    return f"linked {name} -> {destination}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abom", description="Manage agent tools from git repositories")
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install", help="Install a tool from a git repository")
    install_parser.add_argument("name")
    install_parser.add_argument("git_url")

    search_parser = sub.add_parser("search", help="Search installed tools")
    search_parser.add_argument("query", nargs="?")

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
            print(cmd_install(args.name, args.git_url))
        elif args.command == "search":
            for name in cmd_search(args.query):
                print(name)
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


if __name__ == "__main__":
    raise SystemExit(main())
