# AGENTS.md - Agent Coding Guidelines for This Repository

## Project Overview

This is a skills repository containing:
- `skills/cot-cli/` - Documentation for the Cot CLI tool (Cross OS Toolkit)
- `skills/github-release-downloader/` - Python script for downloading GitHub Release assets

## Running the Python Script

The main script is located at `skills/github-release-downloader/scripts/download_release.py`.

### Using uv (Recommended)

```bash
uv run skills/github-release-downloader/scripts/download_release.py <owner> <repo>
uv run skills/github-release-downloader/scripts/download_release.py owner repo --tag v1.0.0
uv run skills/github-release-downloader/scripts/download_release.py owner repo --asset-name "linux" --save-dir ./downloads
```

### Requirements

- Python 3.12+
- Dependencies (auto-installed by uv):
  - `pygithub` - GitHub API client
  - `requests` - HTTP library for downloads
  - `click` - CLI framework

### Environment Variables

- `GITHUB_TOKEN` - Optional GitHub personal access token for higher API rate limits

## Code Style Guidelines

### Python Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) style guide
- Use 4 spaces for indentation (not tabs)
- Maximum line length: 100 characters
- Use type hints for function arguments and return values
- Use `Optional[T]` instead of `T | None` for Python 3.10 compatibility
- Use `f-strings` for string formatting

### Naming Conventions

- **Classes**: `PascalCase` (e.g., `GitHubReleaseDownloader`)
- **Functions/Methods**: `snake_case` (e.g., `download_asset`, `get_latest_release`)
- **Variables**: `snake_case` (e.g., `download_url`, `save_dir`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `SUPPORTED_EXTENSIONS`)
- **Private methods**: Prefix with underscore (e.g., `_internal_method`)

### Imports

- Group imports in this order:
  1. Standard library imports (`os`, `sys`, `pathlib`)
  2. Third-party imports (`click`, `requests`, `github`)
  3. Local application imports

```python
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict

import click
import requests
from github import Github, Auth

from mymodule import MyClass
```

### Type Hints

Always use type hints for:
- Function arguments
- Function return values
- Class attributes

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
    print(f"\n下载失败: {e}")
    if file_path.exists():
        file_path.unlink()
    return False
```

### Documentation

- Use docstrings for all public classes and functions
- Follow Google-style docstring format:

```python
def get_latest_release(self, owner: str, repo: str) -> Optional[GitRelease]:
    """
    获取仓库的最新Release

    Args:
        owner: 仓库所有者
        repo: 仓库名称

    Returns:
        最新Release对象，如果不存在则返回None
    """
```

### Docstrings in SKILL.md Files

SKILL.md files follow a specific format with YAML front matter:

```yaml
---
name: skill-name
description: Description of what the skill does
metadata:
  author: Name <email>
license: MIT
---
```

### Git Workflow

- Create feature branches for new changes
- Write meaningful commit messages
- Do not commit:
  - API tokens or secrets
  - Large binary files
  - Generated files (use .gitignore)

### File Paths

- Use `Path` from `pathlib` for file path operations
- Use forward slashes (`/`) in code for cross-platform compatibility
- Use `parents=True, exist_ok=True` for directory creation

```python
from pathlib import Path

save_path = Path(save_dir)
save_path.mkdir(parents=True, exist_ok=True)
```

### Testing

There are no formal tests in this repository. When adding tests:
- Use `pytest` framework
- Place tests in a `tests/` directory
- Test file naming: `test_*.py`

### Linting

No formal linter is configured. For Python, consider using:
- `ruff` - Fast Python linter
- `black` - Code formatter
- `mypy` - Type checker

If adding linting, add configuration to `pyproject.toml` or `setup.cfg`.

## Common Tasks

### Running the Downloader

```bash
# Download latest release
uv run skills/github-release-downloader/scripts/download_release.py microsoft vscode

# Download specific tag
uv run skills/github-release-downloader/scripts/download_release.py owner repo --tag v1.0.0

# Download to custom directory
uv run skills/github-release-downloader/scripts/download_release.py owner repo --save-dir ./my-downloads

# Filter by asset name
uv run skills/github-release-downloader/scripts/download_release.py owner repo --asset-name "linux"
```

### Adding a New Skill

1. Create directory under `skills/`
2. Add `SKILL.md` with YAML front matter
3. Add any necessary scripts
4. Update this AGENTS.md if needed
