"""Prompt loading + lightweight templating. Prompts live in prompts/*.md as plain
text so they're easy to edit without touching code (per build brief §7).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

from scout.config import get_settings


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    """Load a prompt by path relative to prompts_dir. Cached."""
    s = get_settings()
    path = s.prompts_dir / name
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **vars: object) -> str:
    """Load and substitute $vars using string.Template (safe_substitute)."""
    return Template(load_prompt(name)).safe_substitute(**{k: str(v) for k, v in vars.items()})


def clear_prompt_cache() -> None:
    load_prompt.cache_clear()


def prompt_path(name: str) -> Path:
    return get_settings().prompts_dir / name
