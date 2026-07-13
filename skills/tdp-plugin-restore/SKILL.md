---
name: tdp-plugin-restore
description: Restore TDP plugins from an external USB drive. Triggers when the user mentions restoring, recovering, or syncing plugin data from a USB drive or external storage. Handles both origin resources and plugin files. Use this skill whenever the user wants to restore their TDP plugin files from a USB drive to the local system.
---

## Overview

This skill restores a specified TDP plugin from an external USB drive to local storage.

**Local paths:**
- Origin: `~/space/tdp-scripts/origin/<plugin name>/`
- Plugin: `~/space/tdp-scripts/plugin/<plugin name>_*`

**USB paths:**
- Mac: `/Volumes/SanDisk/origin/`, `/Volumes/SanDisk/plugin/`
- Linux: `/mnt/f/origin/`, `/mnt/f/plugin/`

**Rules:**
- No compression
- Check if source exists before copying; if not, report and skip
- Delete all existing files in the target before copying
- Copy recursively
- Auto-detect OS to choose USB path

## Usage

When the user requests a restore operation, extract the **plugin name** from their request.

### Restore (USB → local)

1. Detect OS and set USB base path accordingly
2. Verify USB paths exist; if not, prompt the user to connect the USB drive
3. For both `origin` and `plugin` directories:
   a. Remove all contents at the local destination for this plugin
   b. Copy from USB to local recursively
4. Report completion with summary of what was restored

## Error Handling

- If the USB drive is not mounted, tell the user to connect it and wait
- If a plugin does not exist on the source, report it and skip
- If a plugin does not exist on the target, create the parent directories as needed
