# abom

`abom` is a Python CLI for managing agent materials (skills, prompts, MCP configs) as git-backed tools.

## Package management

This project is designed for `uv` workflows:

```bash
uv sync
uv run abom --help
```

## Commands

- `abom install <name> <git_url>`: clone a tool repo into `$ABOM_HOME/tools` (or `~/.abom/tools` when `ABOM_HOME` is not set).
- `abom search [query]`: list installed tools, optionally filtered by name.
- `abom remove <name>`: remove an installed tool.
- `abom link <name> <target_repo> [--link-name <name>]`: create a symlink at `<target_repo>/.abom/<name>` pointing to the installed tool. `target_repo` must already exist.

Set `ABOM_HOME` to override `~/.abom`.
