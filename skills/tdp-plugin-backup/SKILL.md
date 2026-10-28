---
name: tdp-plugin-backup
description: Backup TDP plugins to an external USB drive. Triggers when the user mentions backing up, copying, or syncing plugin data to a USB drive or external storage. Handles both origin resources and plugin files. Use this skill whenever the user wants to save their TDP plugin files to a USB drive.
---

# TDP Plugin Backup (local → USB)

Back up one TDP plugin from the local `tdp-scripts` tree to the mounted USB drive.

## Paths

Local root: `$TDP_LOCAL_ROOT` (default `~/space/tdp-scripts`)

- `origin/<plugin>/` — origin resources (directory)
- `plugin/<plugin>_*` — plugin archives
- `bin/<plugin>_*` — executables

USB root: `$TDP_USB_ROOT`, otherwise auto-detected by `scripts/usb-root.sh`:

1. volume label `SanDisk` or `TDP` (via `lsblk`), then
   `/media/$USER/<label>`, `/run/media/$USER/<label>`, `/Volumes/<label>` (macOS)
2. fallback `/mnt/f`, `/mnt/usb` (Windows-style mount)

On this machine the USB is `/media/q/SanDisk` (exFAT, label `SanDisk`).

## Usage

```bash
scripts/backup.sh <plugin>             # 实际备份
scripts/backup.sh --dry-run <plugin>   # 只打印计划，不写入
scripts/backup.sh gridcraft
```

The plugin name may contain letters, digits, `.`, `_`, `+`, `-`; anything else is
rejected (exit 2) so a name can never turn into a glob.

## What the script does

1. Resolves the USB root; fails with a clear message if the drive is not mounted.
2. `origin/<plugin>`: copies to `<dst>.new`, verifies with `diff -r`, then replaces
   the destination.
3. `plugin/<plugin>_*` and `bin/<plugin>_*`: copies to `.tdpnew-*` staging names,
   verifies with `cmp`, deletes only the old `<plugin>_*` files, then moves the
   staged files into place.
4. `sync`s the USB and prints how many files were copied per section.
5. Exits 1 when the plugin has no local `origin/plugin/bin` files.

## Safety notes

- Only files belonging to the requested plugin are removed (its `origin/<plugin>`
  directory and `<plugin>_*` files); other plugins are untouched.
- Staging + verification happen **before** anything is deleted, so a failed copy
  leaves the previous backup intact.
- exFAT has no permissions or symlinks; files are copied as-is (this repo has no
  symlinked plugin files).
- Writing to `/media/...` is outside the workspace sandbox: run the script with
  full file access, otherwise `cp` fails with “权限不够”.
