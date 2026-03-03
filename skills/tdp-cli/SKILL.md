---
name: tdp-cli
description: Helps users use tdp CLI to manage plugins, models, servers, and more.
---

# TDP CLI

This skill helps you use the `tdp` CLI tool (太极开发框架).

## When to Use This Skill

Use this skill when the user:

- wants to list all plugins
- wants to install a plugin
- wants to remove a plugin
- wants to use/activate a plugin
- wants to start or stop a server
- wants to manage huggingface models or datasets
- wants to manage modelscope models
- wants to tag or version their project
- wants to unzip a file
- wants to initialize a project

## What is the TDP CLI?

The TDP CLI (`tdp`) is 太极开发框架 (Taiji Development Framework).

**Key commands:**

- `tdp list` - List all plugins
- `tdp install <package>` - Install a plugin
- `tdp remove <package>` - Remove a plugin
- `tdp use <plugin>` - Use/activate a plugin
- `tdp start <name>` - Start a server
- `tdp stop <name>` - Stop a server
- `tdp init` - Initialize a project
- `tdp tag` - Manage version tags (current|date|hash|next|show|write)
- `tdp huggingface` - Show huggingface models path
- `tdp huggingface-model <name>` - Show specific huggingface model path
- `tdp huggingface-datasets <name>` - Show huggingface datasets path
- `tdp modelscope` - Show modelscope models path
- `tdp unzip <file>` - Unzip a file
- `tdp xf <file>` - Extract a file
- `tdp version` - Show version
- `tdp libc` - Show C library info

## Example Usage

**List all plugins:**

```bash
tdp list
```

**Install a plugin:**

```bash
tdp install my_plugin.zip
```

**Start a server:**

```bash
tdp start my_server
```

**Stop a server:**

```bash
tdp stop my_server
```

**Show version:**

```bash
tdp version
```
