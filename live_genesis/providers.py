"""Provider settings shared by the engine, the study harness, the budget gate and `cli check-provider`.

Nothing here imports a provider SDK or makes an HTTP request, so `controls` (and through it
the web processes) may import it. Three providers exist:

* `openai`             OpenAI Responses API, key `OPENAI_API_KEY`.
* `anthropic`          Anthropic Messages API, key `ANTHROPIC_API_KEY`.
* `openai-compatible`  any server speaking the OpenAI Chat Completions API at
                       `LIVE_COMPAT_BASE_URL` (a local Ollama / LM Studio / llama.cpp server, or a
                       hosted endpoint), key `OPENAI_COMPATIBLE_API_KEY`.

Rules enforced here for `openai-compatible`:
* The base URL must be https unless its host is loopback (a key and every prompt would
  otherwise travel in clear text). It may carry no user:password, query or fragment.
* Loopback means the literal name `localhost`, an IPv4 literal in 127.0.0.0/8, or `::1`.
  Nothing is resolved: any other name or spelling is treated as a remote host.
* A loopback server needs no key (a placeholder is sent); any other host needs one.
* Zero price rates are accepted only for a loopback base URL (see `zero_rates_allowed`).

The base URL is a non-secret `LIVE_*` setting, but it can name a private host: it is never
stored in the database and never shown on a public page. The key is never printed or logged.
"""
from __future__ import annotations

import ipaddress
import math
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import config

NAMES = ("openai", "anthropic", "openai-compatible")
COMPATIBLE = "openai-compatible"
UNKNOWN_PROVIDER = "LIVE_PROVIDER must be openai, anthropic or openai-compatible"
KEY_NAMES = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", COMPATIBLE: "OPENAI_COMPATIBLE_API_KEY"}
DEFAULT_MODELS = {"openai": "gpt-5.6-luna", "anthropic": "claude-haiku-5-5"}  # openai-compatible has none: set LIVE_MODEL
DEFAULT_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 35.0, 5.0, 600.0
# Output-token limit of one resident turn (LIVE_MAX_OUTPUT_TOKENS). The engine's hold, its prompt guidance and
# all three adapters use the same effective value.
DEFAULT_MAX_OUTPUT_TOKENS, MIN_MAX_OUTPUT_TOKENS, MAX_MAX_OUTPUT_TOKENS = 1200, 200, 4000
REASONING_EFFORTS = ("low", "medium", "high")
LOCAL_PLACEHOLDER_KEY = "local-no-key"  # sent to a loopback server when no key is configured; not a credential
_IPV4_LOOPBACK = ipaddress.ip_network("127.0.0.0/8")
_IPV6_LOOPBACK = ipaddress.ip_address("::1")


class ProviderConfigError(RuntimeError):
    """Provider settings are unusable. Raised before any request is sent; never contains a key or a URL."""


def name() -> str:
    """The configured provider, validated."""
    provider = os.getenv("LIVE_PROVIDER", "openai").strip().lower()
    if provider not in NAMES:
        raise ProviderConfigError(UNKNOWN_PROVIDER)
    return provider


def timeout_seconds() -> float:
    """LIVE_INFER_TIMEOUT_SECONDS for every adapter: default 35, clamped to 5-600."""
    raw = os.getenv("LIVE_INFER_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ProviderConfigError("LIVE_INFER_TIMEOUT_SECONDS must be a number of seconds (5-600)")
    return min(max(value, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)


def max_output_tokens() -> int:
    """LIVE_MAX_OUTPUT_TOKENS for a resident turn, every adapter: default 1200, clamped to 200-4000."""
    raw = os.getenv("LIVE_MAX_OUTPUT_TOKENS", "").strip()
    if not raw:
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        value = int(raw)
    except ValueError:
        raise ProviderConfigError("LIVE_MAX_OUTPUT_TOKENS must be a whole number of tokens (200-4000)") from None
    return min(max(value, MIN_MAX_OUTPUT_TOKENS), MAX_MAX_OUTPUT_TOKENS)


def is_loopback_host(host: str | None) -> bool:
    """True for `localhost`, an IPv4 literal in 127.0.0.0/8 and `::1`. Never resolves a name."""
    if not host:
        return False
    host = host.strip().lower()
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in _IPV4_LOOPBACK if address.version == 4 else address == _IPV6_LOOPBACK


@dataclass(frozen=True)
class CompatSettings:
    base_url: str  # never print, store or publish: it can name a private host
    scheme: str
    host: str
    port: int
    loopback: bool
    json_mode: bool
    reasoning_effort: str | None


def _switch(variable: str, default: bool) -> bool:
    raw = os.getenv(variable, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "on", "yes", "true"):
        return True
    if raw in ("0", "off", "no", "false"):
        return False
    raise ProviderConfigError(f"{variable} must be on or off")


def compat_settings() -> CompatSettings:
    """Validated `openai-compatible` settings. Error messages never repeat the URL."""
    raw = os.getenv("LIVE_COMPAT_BASE_URL", "").strip()
    if not raw:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL is required for LIVE_PROVIDER=openai-compatible "
                                  "(for example http://localhost:11434/v1)")
    try:
        parts = urlsplit(raw)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL must not contain a user name or password; "
                                  "put the key in OPENAI_COMPATIBLE_API_KEY")
    if parts.query or parts.fragment:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL must not contain a query string or fragment")
    loopback = is_loopback_host(host)
    if parts.scheme != "https" and not loopback:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL must use https unless the host is loopback "
                                  "(localhost, 127.0.0.0/8, ::1): the key and prompts would travel in clear text")
    effort = os.getenv("LIVE_COMPAT_REASONING_EFFORT", "").strip().lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ProviderConfigError("LIVE_COMPAT_REASONING_EFFORT must be low, medium or high (or unset)")
    return CompatSettings(base_url=raw, scheme=parts.scheme, host=host, port=port or (443 if parts.scheme == "https" else 80),
                          loopback=loopback, json_mode=_switch("LIVE_COMPAT_JSON_MODE", True), reasoning_effort=effort)


def key_is_set(provider: str) -> bool:
    return bool(os.getenv(KEY_NAMES[provider], "").strip())


def compat_api_key(settings: CompatSettings) -> str:
    """The key to send. A loopback server with no key configured gets a harmless placeholder."""
    key = os.getenv(KEY_NAMES[COMPATIBLE], "").strip()
    if key:
        return key
    if settings.loopback:
        return LOCAL_PLACEHOLDER_KEY
    raise ProviderConfigError("OPENAI_COMPATIBLE_API_KEY missing (required for a non-loopback LIVE_COMPAT_BASE_URL)")


def check_ready(provider: str | None = None) -> str:
    """Local gate shared by `engine.run_round` and `study.run_study`: known provider, usable settings,
    key present where one is needed. Returns the provider name. Sends nothing."""
    provider = name() if provider is None else provider
    if provider not in NAMES:
        raise ProviderConfigError(UNKNOWN_PROVIDER)
    timeout_seconds()
    max_output_tokens()
    if provider == COMPATIBLE:
        compat_api_key(compat_settings())
    elif not key_is_set(provider):
        raise ProviderConfigError(f"{KEY_NAMES[provider]} missing")
    return provider


def zero_rates_allowed() -> bool:
    """LIVE_INPUT_USD_PER_M=0 / LIVE_OUTPUT_USD_PER_M=0 are accepted when, and only when, the provider is
    `openai-compatible` AND its base URL is valid AND its host is loopback. Anything else, including any
    setting error, keeps the fail-closed rule: rates must be positive."""
    try:
        return name() == COMPATIBLE and compat_settings().loopback
    except ProviderConfigError:
        return False


# ---- cli check-provider (free; no model call) ---------------------------------------------

def _loopback_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Try a TCP connection, only to addresses that are themselves loopback: if `localhost` resolves
    elsewhere on this machine, nothing is contacted."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for family, kind, proto, _, address in found:
        if not is_loopback_host(address[0]):
            continue
        try:
            with socket.socket(family, kind, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(address)
            return True
        except OSError:
            continue
    return False


def describe() -> tuple[list[str], bool]:
    """Lines for `check-provider` and whether the settings are usable. Makes NO model call and no HTTP
    request; the only network activity is one TCP connect to a loopback address. Prints no key and no
    URL path."""
    lines: list[str] = ["check-provider: no model call is made and nothing leaves this machine."]
    problems: list[str] = []
    provider_raw = os.getenv("LIVE_PROVIDER", "openai").strip().lower()
    try:
        provider: str | None = name()
    except ProviderConfigError as err:
        provider = None
        problems.append(str(err))
    lines.append(f"Provider: {provider_raw or '(empty)'}" + ("" if provider else "  [not valid]"))
    model = os.getenv("LIVE_MODEL", "").strip() or (DEFAULT_MODELS.get(provider or "", ""))
    lines.append(f"Model (LIVE_MODEL; each agent keeps the model saved by init / set-model): {model or '(not set)'}")
    if provider == COMPATIBLE and not model:
        problems.append("LIVE_MODEL is required for openai-compatible (the exact model id the server expects)")
    settings: CompatSettings | None = None
    if provider == COMPATIBLE:
        try:
            settings = compat_settings()
        except ProviderConfigError as err:
            problems.append(str(err))
        if settings:
            lines.append(f"Base URL host: {settings.host}  port {settings.port}  scheme {settings.scheme}  "
                         f"({'loopback' if settings.loopback else 'NOT loopback: prompts leave this machine'})")
            lines.append(f"JSON mode (response_format json_object): {'on' if settings.json_mode else 'off'}")
            lines.append(f"Reasoning effort: {settings.reasoning_effort or 'not sent'}")
        else:
            lines.append("Base URL host: (not usable)")
    try:
        lines.append(f"Timeout per call: {timeout_seconds():g} s")
    except ProviderConfigError as err:
        problems.append(str(err))
    try:
        lines.append(f"Output limit per reply (LIVE_MAX_OUTPUT_TOKENS): {max_output_tokens()} tokens. "
                     "A reply cut off at it is discarded; `cli turns` shows it as truncated")
    except ProviderConfigError as err:
        problems.append(str(err))
    if provider:
        key_set = key_is_set(provider)
        needed = not (settings and settings.loopback) if provider == COMPATIBLE else True
        lines.append(f"Key set ({KEY_NAMES[provider]}): {'yes' if key_set else 'no'}"
                     + ("" if key_set or needed else "  (not needed for a loopback server; a placeholder is sent)"))
        if needed and not key_set and not (provider == COMPATIBLE and settings is None):
            problems.append(f"{KEY_NAMES[provider]} missing")
    rates = []
    for variable in ("LIVE_INPUT_USD_PER_M", "LIVE_OUTPUT_USD_PER_M"):
        try:
            value = float(os.getenv(variable, "0"))
        except ValueError:
            value = math.nan
        rates.append("zero" if value == 0 else "positive" if math.isfinite(value) and value > 0 else "INVALID")
    zero_ok = zero_rates_allowed()
    lines.append(f"Rates (estimates): input {rates[0]}, output {rates[1]}. Zero rates allowed here: "
                 f"{'yes (openai-compatible on a loopback host)' if zero_ok else 'no'}")
    if "INVALID" in rates or ("zero" in rates and not zero_ok):
        problems.append("LIVE_INPUT_USD_PER_M and LIVE_OUTPUT_USD_PER_M must be positive "
                        "(zero is accepted only for openai-compatible on a loopback base URL)")
    opted = os.getenv("LIVE_ALLOW_PAID_CALLS") == "YES"
    lines.append(f"Model calls allowed (LIVE_ALLOW_PAID_CALLS=YES): {'yes' if opted else 'no'}")
    if settings and settings.loopback:
        reachable = _loopback_port_open(settings.host, settings.port)
        lines.append(f"TCP connection to {settings.host}:{settings.port}: {'opens' if reachable else 'REFUSED or no answer'}"
                     + ("" if reachable else "  (is the local server running? From WSL or Docker, localhost may not be the Windows host)"))
    elif settings:
        lines.append("TCP connection: not tried (only loopback hosts are probed)")
    lines += [f"PROBLEM: {p}" for p in problems]
    lines.append("Settings look usable. Not checked: that the server answers, knows this model, or returns token usage."
                 if not problems else "Not ready: fix the problems above. No call was made.")
    return lines, not problems


def add_parser(sub) -> None:
    sub.add_parser("check-provider", help="Show the provider settings a round would use (free; no model call, no key printed)")


def handle_command(args, parser) -> bool:
    if args.command != "check-provider":
        return False
    config.load_provider_credentials()  # CLI process only, as `round` does; only yes/no is ever printed
    lines, usable = describe()
    print("\n".join(lines))
    if not usable:
        parser.exit(1)
    return True
