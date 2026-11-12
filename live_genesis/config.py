"""Where `.env` is read, split by who is allowed to see what.

Two kinds of values live in the local `.env` file:

* **Settings** (`LIVE_*`): database path, estimated spend ceiling, quotas, price
  estimates. Not secret. Every process needs them, so `load_settings()` runs when
  `live_genesis.core` is imported.
* **Provider credentials** (everything else, notably `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENAI_COMPATIBLE_API_KEY` and the key variables that
  `LIVE_ARCHIVE_ENDPOINT_<NAME>_KEY_VAR` names): needed only by the process that makes a model call. Only the operator CLI's `round`, `study-run`, `archive-run` and
  `check-provider` (which prints only whether a key is set) commands call `load_provider_credentials()`.

The web processes never call a model. They call `drop_provider_secrets()` so that a key
exported in the shell that started them is removed from their environment as well.
Values already present in the environment always win over `.env` (never overridden).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SETTINGS_PREFIX = "LIVE_"
# Removed from the environment of every web process. Extend when a provider is added.
PROVIDER_SECRET_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_COMPATIBLE_API_KEY")
# Named archive endpoints (providers.endpoints): LIVE_ARCHIVE_ENDPOINTS lists the names, and
# LIVE_ARCHIVE_ENDPOINT_<NAME>_KEY_VAR holds the NAME of the environment variable with that endpoint's key.
ENDPOINTS_VARIABLE = "LIVE_ARCHIVE_ENDPOINTS"
ENDPOINT_PREFIX = "LIVE_ARCHIVE_ENDPOINT_"
ENDPOINT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
MAX_ENDPOINTS = 8
# A key variable's name must END in _KEY or _TOKEN and may not start with LIVE_ (LIVE_* values are settings: every
# process loads them). The ending keeps a mistyped setting from removing PATH or HOME from a web process.
KEY_VARIABLE = re.compile(r"(?!LIVE_)[A-Z][A-Z0-9_]{0,59}_(?:KEY|TOKEN)")
KEY_VARIABLE_RULE = ("must be the NAME of an environment variable: capital letters, digits and _, ending in _KEY or "
                     "_TOKEN, not starting with LIVE_")


def endpoint_variable(name: str, suffix: str) -> str:
    """LIVE_ARCHIVE_ENDPOINT_<NAME>_<suffix>: the name upper-cased, '-' written as '_'."""
    return f"{ENDPOINT_PREFIX}{name.upper().replace('-', '_')}_{suffix}"


def endpoint_key_variables() -> tuple[str, ...]:
    """Names of the key variables of the configured archive endpoints (never a value). Reads the
    process environment and, for a process that has not loaded its settings yet, `.env`. Only
    well-formed names are returned: this list decides what a web process removes from its environment."""
    settings = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None and k.startswith(SETTINGS_PREFIX)}
    settings.update({k: v for k, v in os.environ.items() if k.startswith(SETTINGS_PREFIX)})
    found: list[str] = []
    for name in (part.strip() for part in settings.get(ENDPOINTS_VARIABLE, "").split(",")):
        variable = settings.get(endpoint_variable(name, "KEY_VAR"), "").strip() if ENDPOINT_NAME.fullmatch(name) else ""
        if KEY_VARIABLE.fullmatch(variable) and variable not in found and variable not in PROVIDER_SECRET_KEYS:
            found.append(variable)
    return tuple(found)


def provider_secret_keys() -> tuple[str, ...]:
    """Every variable that may hold a provider key: the three fixed ones plus the archive endpoints' key variables."""
    return PROVIDER_SECRET_KEYS + endpoint_key_variables()


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
    return [name for name in provider_secret_keys() if os.environ.pop(name, None) is not None]
