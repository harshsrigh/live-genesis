"""Where `.env` is read, split by who is allowed to see what.

Two kinds of values live in the local `.env` file:

* **Settings** (`LIVE_*`): database path, estimated spend ceiling, quotas, price
  estimates. Not secret. Every process needs them, so `load_settings()` runs when
  `live_genesis.core` is imported.
* **Provider credentials** (everything else, notably `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY` and `OPENAI_COMPATIBLE_API_KEY`): needed only by the process that
  makes a model call. Only the operator CLI's `round`, `study-run`, `archive-run` and
  `check-provider` (which prints only whether a key is set) commands call `load_provider_credentials()`.

The web processes never call a model. They call `drop_provider_secrets()` so that a key
exported in the shell that started them is removed from their environment as well.
Values already present in the environment always win over `.env` (never overridden).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SETTINGS_PREFIX = "LIVE_"
# Removed from the environment of every web process. Extend when a provider is added.
PROVIDER_SECRET_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_COMPATIBLE_API_KEY")


def _apply(want_settings: bool) -> list[str]:
    """Copy the chosen half of `.env` into os.environ; return the NAMES set (never values)."""
    applied: list[str] = []
    for name, value in dotenv_values(ENV_FILE).items():
        if value is None or name.startswith(SETTINGS_PREFIX) != want_settings:
            continue
        if name not in os.environ:
            os.environ[name] = value
            applied.append(name)
    return applied


def load_settings() -> list[str]:
    """Load only non-secret `LIVE_*` settings from `.env`. Safe for web processes."""
    return _apply(True)


def load_provider_credentials() -> list[str]:
    """Load provider API keys (and other non-`LIVE_*` SDK variables) from `.env`.

    Call ONLY from a process that is about to make an operator-requested model call.
    """
    return _apply(False)


def drop_provider_secrets() -> list[str]:
    """Remove provider API keys from this process's environment; return the names removed.

    Defence in depth for processes that never call a model. It cannot scrub copies
    already made elsewhere (e.g. `/proc/<pid>/environ` keeps the launch-time values on
    Linux), so the real control is not giving the web process the key at all.
    """
    return [name for name in PROVIDER_SECRET_KEYS if os.environ.pop(name, None) is not None]
