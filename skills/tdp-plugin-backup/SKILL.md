---
name: tdp-plugin-backup
description: Backup TDP plugins to an external USB drive. Triggers when the user mentions backing up, copying, or syncing plugin data to a USB drive or external storage. Handles both origin resources and plugin files. Use this skill whenever the user wants to save their TDP plugin files to a USB drive.
---

# TDP Plugin Backup (local → USB)

Back up one TDP plugin — or every plugin — from the local `tdp-scripts` tree to the
mounted USB drive.

## Paths

Local root: `$TDP_LOCAL_ROOT` (default `~/space/tdp-scripts`)

- `origin/<plugin>/` — origin resources (directory)
- `plugin/<plugin>_*` — plugin archives
- `bin/<plugin>_*` — executables

USB root: `$TDP_USB_ROOT`, otherwise `scripts/usb-root.sh` resolves it **without
assuming a username**:

1. volume label `SanDisk` or `TDP`, taken from the mount table (`lsblk`), then
   globs `/media/*/<label>`, `/run/media/*/<label>`, `/Volumes/<label>` (macOS)
2. if the label is unknown but exactly one removable mount exists under
   `/media/*/*` or `/run/media/*/*`, use it
3. fallback `/mnt/f`, `/mnt/usb` (Windows-style mount)
4. otherwise fail and list the candidates, asking for `TDP_USB_ROOT`

The mount point comes from the system, not from `$USER`/`whoami`: those are wrong
under `sudo`, systemd units or cron, while `/media/<user>/<label>` is created by the
desktop session's udisks. On this machine the drive is `/media/q/SanDisk` (exFAT,
label `SanDisk`).

## Usage

```bash
scripts/backup.sh <plugin>             # 备份单个插件
scripts/backup.sh --all                # 备份本地全部插件
scripts/backup.sh --dry-run <plugin>   # 只打印计划
scripts/backup.sh --all --dry-run      # 列出将要备份的插件
```

Plugin names may contain letters, digits, `.`, `_`, `+`, `-`; anything else is
rejected (exit 2) so a name can never turn into a glob.

`--all` derives the plugin list from `origin/*` directory names plus the
`<name>_<version>_<os>_<arch>...` prefixes of `plugin/` and `bin/` files, dedupes
them, then backs up each one. A per-plugin failure is reported and does not stop
the rest; the script exits non-zero if any failed.

## What the script does

1. Resolves the USB root; fails with a clear message if the drive is not mounted.
2. `origin/<plugin>`: copies to `<dst>.new`, verifies with `diff -r`, then replaces
   the destination.
3. `plugin/<plugin>_*` and `bin/<plugin>_*`: copies to `.tdpnew-*` staging names,
   verifies with `cmp`, deletes only the old `<plugin>_*` files, then moves the
   staged files into place.
4. `sync`s the USB and prints how many files were copied per plugin.
5. Exits 1 when the plugin has no local `origin/plugin/bin` files.

## Safety notes

- Only files belonging to the requested plugin are removed (its `origin/<plugin>`
  directory and `<plugin>_*` files); other plugins are untouched.
- Staging + verification happen **before** anything is deleted, so a failed copy
  leaves the previous backup intact.
- exFAT has no permissions or symlinks; files are copied as-is.
- Writing to `/media/...` is outside the workspace sandbox: run the script with
  full file access, otherwise `cp` fails with “权限不够”.
