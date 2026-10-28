---
name: tdp-plugin-restore
description: Restore TDP plugins from an external USB drive. Triggers when the user mentions restoring, recovering, or syncing plugin data from a USB drive or external storage. Handles both origin resources and plugin files. Use this skill whenever the user wants to restore their TDP plugin files from a USB drive to the local system.
---

# TDP Plugin Restore (USB → local)

Restore one TDP plugin — or every plugin on the drive — from the mounted USB drive
back into the local `tdp-scripts` tree.

## Paths

USB root: `$TDP_USB_ROOT`, otherwise `scripts/usb-root.sh` resolves it **without
assuming a username**:

1. volume label `SanDisk` or `TDP`, taken from the mount table (`lsblk`), then
   globs `/media/*/<label>`, `/run/media/*/<label>`, `/Volumes/<label>` (macOS)
2. if the label is unknown but exactly one removable mount exists under
   `/media/*/*` or `/run/media/*/*`, use it
3. fallback `/mnt/f`, `/mnt/usb` (Windows-style mount)
4. otherwise fail and list the candidates, asking for `TDP_USB_ROOT`

The mount point comes from the system, not from `$USER`/`whoami`: those are wrong
under `sudo`, systemd units or cron. On this machine the drive is
`/media/q/SanDisk` (exFAT, label `SanDisk`).

Local root: `$TDP_LOCAL_ROOT` (default `~/space/tdp-scripts`)

- `origin/<plugin>/` — origin resources (directory)
- `plugin/<plugin>_*` — plugin archives
- `bin/<plugin>_*` — executables

## Usage

```bash
scripts/restore.sh <plugin>             # 恢复单个插件
scripts/restore.sh --all                # 恢复 U 盘上的全部插件
scripts/restore.sh --dry-run <plugin>   # 只打印计划
scripts/restore.sh --all --dry-run      # 列出 U 盘上的插件与目标路径
```

Plugin names may contain letters, digits, `.`, `_`, `+`, `-`; anything else is
rejected (exit 2) so a name can never turn into a glob.

`--all` derives the plugin list from `origin/*` on the USB plus the
`<name>_<version>_<os>_<arch>...` prefixes of its `plugin/` and `bin/` files,
dedupes them, then restores each. A per-plugin failure is reported and does not
stop the rest; the script exits non-zero if any failed.

**`--all` restores the whole backup volume** (here about 30 GB across 40+ plugins);
run `--all --dry-run` first and make sure there is enough disk space. Prefer a
single plugin name when recovering one thing.

## What the script does

1. Resolves the USB root; fails with a clear message if the drive is not mounted.
2. `origin/<plugin>`: copies to `<dst>.new`, verifies with `diff -r`, then replaces
   the local destination.
3. `plugin/<plugin>_*` and `bin/<plugin>_*`: copies to `.tdpnew-*` staging names,
   verifies with `cmp`, deletes only the old local `<plugin>_*` files, then moves
   the staged files into place.
4. `sync`s and prints how many files were restored per plugin.
5. Exits 1 when the plugin is not present on the USB drive.

## Version rules

The source side decides whether a section is copied:

- **Source has one version** → compare it with the destination's highest version:
  - source is higher → replace the destination files for this plugin
  - source is lower or equal → skip, keep the destination (no downgrade)
- **Source has multiple versions** → copy all of them, replacing the destination's
  file set for this plugin (the same as "mirror the source").
- No version can be parsed, or the destination is missing → normal copy.

Versions come from file names: `origin` files such as
`deckcraft-0.10.0-linux-x86_64.tar.gz` / `node-v20.20.2-linux-x64.tar.gz`, and
`plugin`/`bin` names such as `gridcraft_0.11.1_linux_amd64.tar.zst`. A leading `v`
is accepted; comparison is numeric dotted (`0.10.0` > `0.9.0`), and suffixes after
the numeric part are ignored.

Per-plugin output shows either `跳过：源 X <= 目标 Y` or the copy that happened.

## Safety notes

- Only files belonging to the requested plugin are removed locally (its
  `origin/<plugin>` directory and `<plugin>_*` files); other plugins are untouched.
- Staging + verification happen **before** anything is deleted, so a failed copy
  leaves the previous local copy intact.
- A plugin that exists on the USB but not locally is simply created.
- exFAT has no permissions or symlinks; files are copied as-is.
