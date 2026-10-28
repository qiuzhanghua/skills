---
name: tdp-plugin-restore
description: Restore TDP plugins from an external USB drive. Triggers when the user mentions restoring, recovering, or syncing plugin data from a USB drive or external storage. Handles both origin resources and plugin files. Use this skill whenever the user wants to restore their TDP plugin files from a USB drive to the local system.
---

# TDP Plugin Restore (USB → local)

Restore one TDP plugin from the mounted USB drive back into the local `tdp-scripts`
tree.

## Paths

USB root: `$TDP_USB_ROOT`, otherwise auto-detected by `scripts/usb-root.sh`:

1. volume label `SanDisk` or `TDP` (via `lsblk`), then
   `/media/$USER/<label>`, `/run/media/$USER/<label>`, `/Volumes/<label>` (macOS)
2. fallback `/mnt/f`, `/mnt/usb` (Windows-style mount)

On this machine the USB is `/media/q/SanDisk` (exFAT, label `SanDisk`).

Local root: `$TDP_LOCAL_ROOT` (default `~/space/tdp-scripts`)

- `origin/<plugin>/` — origin resources (directory)
- `plugin/<plugin>_*` — plugin archives
- `bin/<plugin>_*` — executables

## Usage

```bash
scripts/restore.sh <plugin>             # 实际恢复
scripts/restore.sh --dry-run <plugin>   # 只打印计划，不写入
scripts/restore.sh gridcraft
```

The plugin name may contain letters, digits, `.`, `_`, `+`, `-`; anything else is
rejected (exit 2) so a name can never turn into a glob.

## What the script does

1. Resolves the USB root; fails with a clear message if the drive is not mounted.
2. `origin/<plugin>`: copies to `<dst>.new`, verifies with `diff -r`, then replaces
   the local destination.
3. `plugin/<plugin>_*` and `bin/<plugin>_*`: copies to `.tdpnew-*` staging names,
   verifies with `cmp`, deletes only the old local `<plugin>_*` files, then moves
   the staged files into place.
4. `sync`s and prints how many files were restored per section.
5. Exits 1 when the plugin is not present on the USB drive.

## Safety notes

- Only files belonging to the requested plugin are removed locally (its
  `origin/<plugin>` directory and `<plugin>_*` files); other plugins are untouched.
- Staging + verification happen **before** anything is deleted, so a failed copy
  leaves the previous local copy intact.
- A plugin that exists on the USB but not locally is simply created.
- exFAT has no permissions or symlinks; files are copied as-is.
