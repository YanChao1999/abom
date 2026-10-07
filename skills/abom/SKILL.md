---
name: abom
description: Install, check, and link agent skills, prompts, MCP servers, and packages with the abom CLI. Use when the user wants to add an agent material, look up a recipe by name, vet a checkout before install, or wire an installed material into a project.
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
abom install <name>
abom search [query]
abom link <name> <target_repo> [--link-name <name>]
abom remove <name>
```

`abom show` prints the catalog entry, whether it is installed, and the check result for an installed checkout. `abom search` lists installed tools. `ABOM_HOME` overrides `~/.abom`.

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
- `abom-mcp` is the stdio MCP server (`mcp/server.py`). Launch it with `python mcp/server.py`. It exposes list, show, check, install, search, link, and remove.
