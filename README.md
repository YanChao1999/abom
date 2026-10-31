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

Version 0.0.3. GitHub Actions runs format, lint, and test on every pull request. A pull request that adds or edits `src/abom/recipes.json` also runs the recipe pull-check. Pushing a `v*` tag publishes a GitHub release, TestPyPI, and PyPI from the same build. Pushes to `main` publish the GitHub Pages site.

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
| `anthropics-claude-api` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/claude-api` |
| `anthropics-canvas-design` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/canvas-design` |
| `anthropics-theme-factory` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/theme-factory` |
| `anthropics-web-artifacts-builder` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/web-artifacts-builder` |
| `anthropics-slack-gif-creator` | skill | [anthropics/skills](https://github.com/anthropics/skills) `skills/slack-gif-creator` |
| `f-prompts` | prompt | [f/prompts.chat](https://github.com/f/prompts.chat) `PROMPTS.md` |
| `danielmiessler-summarize` | prompt | [danielmiessler/fabric](https://github.com/danielmiessler/fabric) `data/patterns/summarize/system.md` |
| `danielmiessler-extract-wisdom` | prompt | [danielmiessler/fabric](https://github.com/danielmiessler/fabric) `data/patterns/extract_wisdom/system.md` |
| `danielmiessler-explain-code` | prompt | [danielmiessler/fabric](https://github.com/danielmiessler/fabric) `data/patterns/explain_code/system.md` |
| `modelcontextprotocol-filesystem` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/filesystem` |
| `modelcontextprotocol-memory` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/memory` |
| `modelcontextprotocol-fetch` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/fetch` |
| `modelcontextprotocol-git` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/git` |
| `modelcontextprotocol-time` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/time` |
| `modelcontextprotocol-sequentialthinking` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/sequentialthinking` |
| `modelcontextprotocol-everything` | mcp | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) `src/everything` |
| `f-prompts-chat` | package | [f/prompts.chat](https://github.com/f/prompts.chat) `packages/prompts.chat` |
| `modelcontextprotocol-python-sdk` | package | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) repository root |

```bash
abom recipes
abom info anthropics-skill-creator
abom check anthropics-skill-creator
cd /path/to/project
abom install anthropics-skill-creator --use --agent cursor
abom use modelcontextprotocol-filesystem
abom show anthropics-skill-creator
abom upgrade anthropics-skill-creator
abom lock
abom sync
abom rollback anthropics-skill-creator
abom tap add team ./team-catalog.json
abom catalogs
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
- a skill or prompt that tells the agent to ignore or disregard earlier instructions is rejected

A pull request that adds or edits a recipe also asks Copilot, with no checkout tools enabled, to review the cloned material for prompt injection, hidden instructions, secret exfiltration, and remote-shell install hooks. The job fails when that review does not pass.

## Advisories

abom publishes recipe advisories with ids like `ABOM-2026-0001`, similar in role to CVE records for agent materials. Active advisories are checked during `abom check`, `abom install`, `abom upgrade`, and `abom rollback`. `abom report <name>` prints a reproducible JSON report with the rules version and any advisory hits.

```bash
abom advisories
abom advisory ABOM-2026-0001
abom report anthropics-skill-creator
```

The rejection rules are versioned in `src/abom/rules.json`. The advisory feed is `src/abom/advisories.json` (override with `ABOM_ADVISORIES`). To publish one, append an object with `id`, `severity`, `summary`, `description`, `published`, and `affected` (`recipe` and/or `git`, optional `commits` / `refs`, or `all_commits: true`).

## Commands

- `abom install <name>`: install the named recipe from the merged catalog into `$ABOM_HOME/tools`.
- `abom install <name> --use [target] [--agent …] [--workspace …]`: install, then wire. Bare `--use` targets the nearest workspace from cwd (uv-style).
- `abom install <name> --locked`: install the commit pinned in `abom.lock.json`.
- `abom install <name> <git_url>`: clone a git repository that is not in the catalog.
- `abom use <name> [target_repo] [--agent cursor|claude|copilot|agents|codex|all] [--workspace …]`: wire into a project. With no target, discovers the nearest workspace from cwd (`.abom/`, `abom.lock.json`, `.cursor`, `.git`, …). Agent prefs are stored in `<workspace>/.abom/project.json` and reused on later `use` in that project. MCP `cwd` / filesystem args default to that workspace.
- `abom upgrade [name]`: move installed catalog checkouts to the current recipe ref tip.
- `abom rollback <name>`: restore the previous receipt commit.
- `abom lock` / `abom lock --check`: write or verify `abom.lock.json` from installed receipt commits.
- `abom sync [--lock-file …]`: install or reinstall tools so they match `abom.lock.json`.
- `abom tap list` / `abom tap add <name> <catalog.json>` / `abom tap remove <name>`: manage private catalog taps over the public `core` trust root.
- `abom catalogs`: list named catalog sources (core, taps, private, extras) and the trust root.
- `abom recipes [query]`: list recipes, optionally filtered by name, kind, or description.
- `abom info <name>`: print one catalog entry.
- `abom show <name>`: print the catalog entry, whether it is installed, and the check result when it is installed.
- `abom check <name>`: verify the kind and install safety, including published advisories. Exits with an error when the check fails.
- `abom report <name>`: print a reproducible JSON security report (`abom.report/v1`).
- `abom advisories [query]` / `abom advisory <id>`: list or show published advisories.
- `abom search [query]`: list installed tools, optionally filtered by name.
- `abom remove <name>`: remove an installed tool and its receipt.
- `abom link <name> <target_repo> [--link-name <name>]`: create a symlink at `<target_repo>/.abom/<name>` pointing to the installed material. `target_repo` must already exist.

Set `ABOM_HOME` to override `~/.abom`. The bundled catalog is the public `core` trust root. Register private overlays with `abom tap add`, drop JSON into `$ABOM_HOME/catalogs/*.json`, or set `ABOM_EXTRA_CATALOGS` to a path list (`os.pathsep` separated). Later sources override earlier names. `ABOM_CATALOG` still replaces the whole merge for tests. Receipts keep `previous_commit` and a short `history` list for rollbacks.
