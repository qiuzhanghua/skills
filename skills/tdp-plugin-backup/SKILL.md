---
name: tdp-plugin-backup
description: Backup TDP plugins to an external USB drive. Triggers when the user mentions backing up, copying, or syncing plugin data to a USB drive or external storage. Handles both origin resources and plugin files. Use this skill whenever the user wants to save their TDP plugin files to a USB drive.
---

## Overview

This skill backs up a specified TDP plugin from local storage to an external USB drive.

**Local paths:**
- Origin: `~/space/tdp-scripts/origin/<plugin name>/`
- Plugin: `~/space/tdp-scripts/plugin/<plugin name>_*`
- Bin: `~/space/tdp-scripts/bin/<plugin name>_*`

**USB paths:**
- Mac: `/Volumes/SanDisk/origin/`, `/Volumes/SanDisk/plugin/`, `/Volumes/SanDisk/bin/`
- Linux: `/mnt/f/origin/`, `/mnt/f/plugin/`, `/mnt/f/bin/`

**Rules:**
- No compression
- Check if source exists before copying; if not, report and skip
- Delete all existing files in the target before copying
- Copy recursively
- Auto-detect OS to choose USB path

## Usage

When the user requests a backup operation, extract the **plugin name** from their request.

### Backup (local → USB)

1. Detect OS and set USB base path accordingly
2. Verify USB paths exist; if not, prompt the user to connect the USB drive
3. For `origin`, `plugin`, and `bin` directories:
   a. Remove all contents at the USB destination for this plugin
   b. Copy the local plugin directory/file to the USB destination recursively
4. Report completion with summary of what was copied

## Error Handling

- If the USB drive is not mounted, tell the user to connect it and wait
- If a plugin does not exist on the source, report it and skip
- If a plugin does not exist on the target, create the parent directories as needed
