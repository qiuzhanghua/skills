# AGENTS.md - Agent Coding Guidelines for This Repository

## Project Overview

This is a skills repository containing documentation and scripts:
- `skills/cot-cli/` - Documentation for the Cot CLI tool (Cross OS Toolkit)
- `skills/github-release-downloader/` - Python script for downloading GitHub Release assets

## Running Scripts

### Python Script (github-release-downloader)

```bash
# Using uv (recommended - auto-installs dependencies)
uv run skills/github-release-downloader/scripts/download_release.py <owner> <repo>
uv run skills/github-release-downloader/scripts/download_release.py owner repo --tag v1.0.0
uv run skills/github-release-downloader/scripts/download_release.py owner repo --asset-name "linux" --save-dir ./downloads

# Requirements: Python 3.12+, pygithub, requests, click
# Optional: GITHUB_TOKEN env var for higher API rate limits
```

## Build/Lint/Test Commands

**No formal build, lint, or test commands are configured.**

- **Testing**: No tests exist. If added, use `pytest` framework with `tests/test_*.py` naming
- **Linting**: Not configured. If added, consider `ruff`, `black`, or `mypy`
- **Single test**: Would run with `pytest tests/test_file.py::test_function`

## Code Style Guidelines

### Python Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) style guide
- Use 4 spaces for indentation (not tabs)
- Maximum line length: 100 characters
- Use type hints for all function arguments and return values
- Use `Optional[T]` instead of `T | None` for Python 3.10 compatibility
- Use `f-strings` for string formatting

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `GitHubReleaseDownloader` |
| Functions/Methods | snake_case | `download_asset` |
| Variables | snake_case | `download_url` |
| Constants | UPPER_SNAKE_CASE | `SUPPORTED_EXTENSIONS` |
| Private methods | Prefix with `_` | `_internal_method` |

### Imports Order

```python
# 1. Standard library
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict

# 2. Third-party
import click
import requests
from github import Github, Auth

# 3. Local application
from mymodule import MyClass
```

### Type Hints

```python
def download_asset(
    self, download_url: str, filename: str, save_dir: str, chunk_size: int = 8192
) -> bool:
    ...
```

### Error Handling

- Use try/except blocks for operations that may fail
- Catch specific exceptions when possible
- Provide meaningful error messages
- Clean up partial files on failure

```python
try:
    response = requests.get(download_url, stream=True, timeout=300)
    response.raise_for_status()
except requests.exceptions.RequestException as e:
    print(f"\nError: {e}")
    if file_path.exists():
        file_path.unlink()
    return False
```

### Documentation

- Use docstrings for all public classes and functions
- Follow Google-style docstring format:

```python
def get_latest_release(self, owner: str, repo: str) -> Optional[GitRelease]:
    """Get the latest release for a repository.

    Args:
        owner: Repository owner
        repo: Repository name

    Returns:
        Latest Release object, or None if not found
    """
```

### SKILL.md Format

Files use YAML front matter:

```yaml
---
name: skill-name
description: Description of what the skill does
metadata:
  author: Name <email>
license: MIT
---
```

### File Paths

- Use `Path` from `pathlib` for file path operations
- Use forward slashes (`/`) in code for cross-platform compatibility
- Use `parents=True, exist_ok=True` for directory creation

```python
from pathlib import Path

save_path = Path(save_dir)
save_path.mkdir(parents=True, exist_ok=True)
```

### Git Workflow

- Create feature branches for new changes
- Write meaningful commit messages
- Do not commit: API tokens, secrets, large binaries, generated files (use .gitignore)

## Adding a New Skill

1. Create directory under `skills/`
2. Add `SKILL.md` with YAML front matter
3. Add any necessary scripts
4. Update this AGENTS.md if needed
