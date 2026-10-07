# abom

`abom` is a Python CLI for managing agent materials (skills, prompts, MCP configs) as git-backed tools. The recipe list lives in the repo as `src/abom/recipes.json`. `abom install <name>` looks up that name and clones the git URL stored there.

## Package management

Homepage: <https://yanchao1999.github.io/abom/>

```bash
pip install abom
abom recipes
```

TestPyPI:

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ abom
```

For local development:

```bash
uv sync --extra dev
uv run abom recipes
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

Version 0.0.2. GitHub Actions runs format, lint, and test on every pull request. A pull request that adds or edits `src/abom/recipes.json` also runs the recipe pull-check. Pushing a `v*` tag publishes a GitHub release, TestPyPI, and PyPI from the same build. Pushes to `main` publish the GitHub Pages site.

Trusted publishing uses the GitHub environments `testpypi` and `pypi`. On each index, add this repository as a trusted publisher: workflow `deploy.yml`, and the matching environment name. The PyPI project name is `abom`.

## Recipes

Add a material by appending an object to the `recipes` list in `src/abom/recipes.json`. Name it `<publisher>-<material>` so the source is visible in the name, then install by that name:

```bash
abom install anthropics-skill-creator
```

`kind` is `skill`, `prompt`, `mcp`, or `package`. `source.git` is the clone URL. `source.ref` is a branch or tag (`main` when omitted). `source.path` is optional; when set, `abom link` points at that file or directory inside the checkout.

```json
{
  "name": "anthropics-example-name",
  "kind": "skill",
  "description": "One line describing what this material does and when to use it.",
  "homepage": "https://example.com/example-name",
  "license": "Apache-2.0",
  "source": {
    "git": "https://github.com/org/repo.git",
    "ref": "main",
    "path": "path/inside/repo"
  }
}
```

The catalog includes these recipes:

| Name | Kind | Source |
| --- | --- | --- |
| `abom-package` | package | [YanChao1999/abom](https://github.com/YanChao1999/abom) repository root |
| `abom-skill` | skill | [YanChao1999/abom](https://github.com/YanChao1999/abom) `skills/abom` |
| `abom-mcp` | mcp | [YanChao1999/abom](https://github.com/YanChao1999/abom) `mcp` |
| `anthropics-skill-creator` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/skill-creator` |
| `anthropics-frontend-design` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/frontend-design` |
| `anthropics-mcp-builder` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/mcp-builder` |
| `anthropics-webapp-testing` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/webapp-testing` |
| `f-prompts` | prompt | [f/prompts.chat](https://github.com/f/prompts.chat) `PROMPTS.md` |
| `danielmiessler-summarize` | prompt | [danielmiessler/fabric](https://github.com/danielmiessler/fabric) `data/patterns/summarize/system.md` |
| `modelcontextprotocol-filesystem` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/filesystem` |
| `modelcontextprotocol-memory` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/memory` |
| `modelcontextprotocol-fetch` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/fetch` |
| `modelcontextprotocol-git` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/git` |
| `f-prompts-chat` | package | [f/prompts.chat](https://github.com/f/prompts.chat) `packages/prompts.chat` |
| `modelcontextprotocol-python-sdk` | package | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) repository root |

```bash
abom recipes
abom info anthropics-skill-creator
abom check anthropics-skill-creator
abom install anthropics-skill-creator
abom show anthropics-skill-creator
abom link anthropics-skill-creator /path/to/project
```

`abom check` clones the recipe when it is not installed yet, then deletes that temporary checkout. Install runs the same check and keeps the checkout only when it passes. The check confirms the kind and blocks install-time hijacks:

- a skill must be a directory with `SKILL.md` frontmatter (`name` and `description`)
- a prompt must be a text prompt file, or a directory of them
- a package must have `package.json` or `pyproject.toml`
- the checkout must contain a license file, and the recognized license must satisfy the recipe's `license` field
- an mcp must have a server manifest or entry file (`package.json`, `pyproject.toml`, `index.ts`, `index.js`, `server.py`, or `main.py`)
- clone URLs cannot use a scheme that runs a helper, such as `ext::`
- git hooks and filesystem monitors from the cloned repo are not executed
- a symlink that points outside the checkout is rejected
- a prompt, skill, or package lifecycle script that pipes a download into a shell is rejected

## Commands

- `abom install <name>`: install the named recipe from `recipes.json` into `$ABOM_HOME/tools`.
- `abom install <name> <git_url>`: clone a git repository that is not in the catalog.
- `abom recipes [query]`: list recipes, optionally filtered by name, kind, or description.
- `abom info <name>`: print one catalog entry.
- `abom show <name>`: print the catalog entry, whether it is installed, and the check result when it is installed.
- `abom check <name>`: verify the kind and install safety. Exits with an error when the check fails.
- `abom search [query]`: list installed tools, optionally filtered by name.
- `abom remove <name>`: remove an installed tool and its receipt.
- `abom link <name> <target_repo> [--link-name <name>]`: create a symlink at `<target_repo>/.abom/<name>` pointing to the installed material. `target_repo` must already exist.

Set `ABOM_HOME` to override `~/.abom`.
