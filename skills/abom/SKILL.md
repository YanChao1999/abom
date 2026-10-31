---
name: abom
description: Install, check, use, sync, upgrade, lock, roll back, and tap private catalogs for agent skills, prompts, MCP servers, and packages with the abom CLI. Use when the user wants to add an agent material, wire it into Cursor/Claude/Copilot/Codex paths, pin commits, or overlay a private catalog on the public core trust root.
---

# abom

abom is the agent bill of materials manager. Recipes live in `src/abom/recipes.json`. Install by recipe name. The git URL stays in the catalog.

## When to use

Use this skill when the user asks to install a skill, prompt, MCP server, or package, to add a recipe, or to see what is already installed. Run `abom check <name>` before `abom install <name>` when the recipe is new.

## Commands

```bash
abom recipes [query]
abom info <name>
abom show <name>
abom check <name>
abom install <name> [--use --agent cursor]
abom use <name>   # nearest workspace from cwd; agent from .abom/project.json
abom upgrade [name]
abom lock
abom sync
abom rollback <name>
abom tap list
abom tap add <name> <catalog.json>
abom catalogs
abom search [query]
abom link <name> <target_repo> [--link-name <name>]
abom remove <name>
```

`abom use` / `install --use` wires into the nearest project workspace from cwd (like `uv`: walk up for `.abom`, `abom.lock.json`, `.cursor`, `.git`, else cwd). Agent prefs live in `<workspace>/.abom/project.json`. MCP `cwd`/filesystem args default to that workspace. `abom sync` installs to lock commits. `abom tap` overlays private catalogs on public `core`. Advisories use ids like `ABOM-2026-0001`. `ABOM_HOME` overrides `~/.abom`.

## Add a recipe

Append one object to the `recipes` list. The name is `<publisher>-<material>` and must match `^[a-z0-9]+(?:[._-][a-z0-9]+)*$`.

```json
{
  "name": "abom-skill",
  "kind": "skill",
  "description": "One line describing what this material does and when to use it.",
  "homepage": "https://github.com/YanChao1999/abom",
  "license": "MIT",
  "source": {
    "git": "https://github.com/YanChao1999/abom.git",
    "ref": "main",
    "path": "skills/abom"
  }
}
```

`kind` is `skill`, `prompt`, `mcp`, or `package`. `source.path` is the file or directory inside the clone that `abom link` points at.

## Checks

`abom check` and `abom install` reject a checkout that does not match its kind, a clone URL whose scheme can run a helper, a symlink that leaves the checkout, a skill or prompt that contains a prompt-injection instruction, or a skill, prompt, or package lifecycle script that pipes a download into a shell. The checkout must also include a license file whose text satisfies the recipe `license` field. A pull request that changes a recipe asks Copilot to review the cloned material for those compromises. Fix the recipe or the upstream material before installing.

Install this package from GitHub with `pip install git+https://github.com/YanChao1999/abom.git`, then run `abom recipes`.

## This repo

- `abom-skill` is this skill (`skills/abom`).
- `abom-mcp` is the stdio MCP server (`mcp/server.py`). Launch it with `python mcp/server.py`. It exposes list, show, check, install, use, upgrade, sync, tap, rollback, search, link, and remove.
