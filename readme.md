# cc-clean

`cc-clean` is a small local helper for cleaning Claude CLI state without
blindly deleting the whole `~/.claude` tree.

It provides:

- a terminal UI inspired by `codex-session-cloner`
- safe defaults that avoid deleting old sessions unless you opt in
- backup-aware cleanup for files and directories
- targeted scrubbing for `~/.claude.json:userID`
- optional removal of custom auth env keys from `~/.claude/settings.json`

## Run from source

```bash
cd /Users/zsj/code/python/cc-clean
PYTHONPATH=src python -m cc_clean
```

## CLI examples

Preview the default safe plan:

```bash
PYTHONPATH=src python -m cc_clean plan
```

Run a full reset with backups:

```bash
PYTHONPATH=src python -m cc_clean clean --preset full --yes
```

Dry-run only:

```bash
PYTHONPATH=src python -m cc_clean clean --preset safe --dry-run --yes
```
