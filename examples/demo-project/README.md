# Demo project

Checked-in example of abom as a **project supply chain**: nearest-workspace wiring, lock with commit + digest, and advisory awareness.

## What is committed

- `abom.lock.json` — pins `abom-skill` to a commit and material digest
- `.abom/project.json` — records that this workspace uses Cursor for that tool

Local install state (`.abom-home/`, `.cursor/`) is gitignored.

## Reproduce

From this directory (after `pip install abom` or a local editable install):

```bash
abom sync
abom use abom-skill
abom lock --check
abom advisories
```

`abom sync` installs the locked commit into `$ABOM_HOME`. `abom use` rewires Cursor skills for this workspace. `abom lock --check` fails if the material digest drifts.
