# Demo project

Checked-in example of abom as a **project supply chain**: nearest-workspace wiring, lock with commit + digest, and advisory awareness.

## What is committed

- `abom.lock.json` — pins `abom-skill` to a commit, material digest, rules version, and advisory feed digest
- `.abom/policy.json` — allows the licenses and kinds this example installs, and subscribes to the bundled advisory feed
- `.abom/project.json` — records that this workspace uses Cursor for that tool

Local install state (`.abom-home/`, `.cursor/`) is gitignored.

## Reproduce

From this directory (after `pip install abom` or a local editable install):

```bash
abom sync
abom use abom-skill
abom lock --check
abom audit
abom advisories
```

`abom sync` installs the locked commit under `$ABOM_HOME/store/<name>/<commit>` and leaves other commits in place. `abom use` rewires Cursor skills at that tree. `abom lock --check` fails if the material digest, rules version, or advisory feed digest drifts. `abom audit` replays advisories against the lock without cloning.
