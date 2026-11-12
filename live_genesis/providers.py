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

Answers Over Time only (`cli archive-run`): NAMED endpoints (`LIVE_ARCHIVE_ENDPOINTS`, `endpoints()`),
each one more server speaking Chat Completions with its own base URL (same rule as above), its own
key variable (`..._KEY_VAR` holds the variable's NAME) and its own rates. There, zero rates are also
accepted for a hosted endpoint the operator declares free (`..._FREE_TIER=YES`, basis `declared-free`).
`round` and `study-run` never use a named endpoint. `cli archive-check-endpoints` prints them.
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


def _base_url(variable: str, raw: str, key_hint: str) -> tuple[str, str, int, bool]:
    """(scheme, host, port, loopback) of a validated base URL. One rule for `LIVE_COMPAT_BASE_URL` and for
    every named endpoint. Error messages name the variable and never repeat the URL."""
    try:
        parts = urlsplit(raw)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ProviderConfigError(f"{variable} is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ProviderConfigError(f"{variable} must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ProviderConfigError(f"{variable} must not contain a user name or password; put the key in {key_hint}")
    if parts.query or parts.fragment:
        raise ProviderConfigError(f"{variable} must not contain a query string or fragment")
    loopback = is_loopback_host(host)
    if parts.scheme != "https" and not loopback:
        raise ProviderConfigError(f"{variable} must use https unless the host is loopback "
                                  "(localhost, 127.0.0.0/8, ::1): the key and prompts would travel in clear text")
    return parts.scheme, host, port or (443 if parts.scheme == "https" else 80), loopback


def compat_settings() -> CompatSettings:
    """Validated `openai-compatible` settings. Error messages never repeat the URL."""
    raw = os.getenv("LIVE_COMPAT_BASE_URL", "").strip()
    if not raw:
        raise ProviderConfigError("LIVE_COMPAT_BASE_URL is required for LIVE_PROVIDER=openai-compatible "
                                  "(for example http://localhost:11434/v1)")
    scheme, host, port, loopback = _base_url("LIVE_COMPAT_BASE_URL", raw, "OPENAI_COMPATIBLE_API_KEY")
    effort = os.getenv("LIVE_COMPAT_REASONING_EFFORT", "").strip().lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ProviderConfigError("LIVE_COMPAT_REASONING_EFFORT must be low, medium or high (or unset)")
    return CompatSettings(base_url=raw, scheme=scheme, host=host, port=port,
                          loopback=loopback, json_mode=_switch("LIVE_COMPAT_JSON_MODE", True), reasoning_effort=effort)


# ---- Named endpoints for Answers Over Time (LIVE_ARCHIVE_ENDPOINTS) -------------------------
# Each is one server speaking the OpenAI Chat Completions API, with its own base URL, key variable and
# price rates, so one archive run can ask models on several services. `cli round` and `cli study-run`
# never use them. The record holds the NAME of the key variable, never the key.
BASIS_RATES, BASIS_LOCAL, BASIS_FREE = "rates", "local", "declared-free"
PRICING_BASES = (BASIS_RATES, BASIS_LOCAL, BASIS_FREE)
ENDPOINT_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
MAX_PAUSE_SECONDS = 600.0
RESERVED_ENDPOINT_NAMES = NAMES  # an entry `label=openai:model` always means the native provider


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str             # never print, store or publish: it can name a private host
    scheme: str
    host: str
    port: int
    loopback: bool
    key_var: str | None       # NAME of the environment variable holding the key; the key itself is never kept here
    input_rate: float         # USD per million tokens (estimates)
    output_rate: float
    basis: str                # rates | local (loopback, zero rates) | declared-free (hosted, operator's statement)
    reasoning_effort: str | None
    max_output_tokens: int | None
    send_sampling: bool
    pause_seconds: float


def _yes_no(variable: str, default: bool) -> bool:
    raw = os.getenv(variable, "").strip()
    if not raw:
        return default
    if raw in ("YES", "NO"):
        return raw == "YES"
    raise ProviderConfigError(f"{variable} must be YES or NO")


def _amount(variable: str, default: float | None, maximum: float | None = None) -> float | None:
    raw = os.getenv(variable, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        raise ProviderConfigError(f"{variable} must be a finite non-negative number" + (f" (at most {maximum:g})" if maximum else ""))
    return value


def send_sampling(name: str, default: bool) -> bool:
    """LIVE_ARCHIVE_ENDPOINT_<NAME>_SEND_SAMPLING for a named endpoint or for `openai`, `anthropic`,
    `openai-compatible` (written OPENAI_COMPATIBLE): whether archive calls send the pinned sampling."""
    return _yes_no(config.endpoint_variable(name, "SEND_SAMPLING"), default)


def endpoint_names() -> list[str]:
    raw = os.getenv(config.ENDPOINTS_VARIABLE, "").strip()
    names = [part.strip() for part in raw.split(",") if part.strip()]
    for name in names:
        if not config.ENDPOINT_NAME.fullmatch(name) or name in RESERVED_ENDPOINT_NAMES:
            raise ProviderConfigError(f"{config.ENDPOINTS_VARIABLE}: each name must be 1-24 characters of a-z 0-9 - and may "
                                      "not be openai, anthropic or openai-compatible")
    if len(set(names)) != len(names):
        raise ProviderConfigError(f"{config.ENDPOINTS_VARIABLE} lists a name twice")
    if len(names) > config.MAX_ENDPOINTS:
        raise ProviderConfigError(f"{config.ENDPOINTS_VARIABLE} lists more than {config.MAX_ENDPOINTS} endpoints")
    return names


def endpoints() -> dict[str, Endpoint]:
    """Every endpoint named in LIVE_ARCHIVE_ENDPOINTS, validated. Raises ProviderConfigError naming the
    variable at fault; a message never holds a URL or a key. Sends nothing and reads no key."""
    found: dict[str, Endpoint] = {}
    for name in endpoint_names():
        def var(suffix: str, name: str = name) -> str:
            return config.endpoint_variable(name, suffix)
        raw = os.getenv(var("BASE_URL"), "").strip()
        if not raw:
            raise ProviderConfigError(f"{var('BASE_URL')} is required for endpoint {name} (for example http://localhost:11434/v1)")
        scheme, host, port, loopback = _base_url(var("BASE_URL"), raw, f"the variable named by {var('KEY_VAR')}")
        key_var = os.getenv(var("KEY_VAR"), "").strip() or None
        if key_var is not None and (not config.KEY_VARIABLE.fullmatch(key_var) or key_var in config.PROVIDER_SECRET_KEYS):
            raise ProviderConfigError(f"{var('KEY_VAR')} {config.KEY_VARIABLE_RULE}, and not one of "
                                      + ", ".join(config.PROVIDER_SECRET_KEYS) + ". It holds a variable's name, never the key itself")
        if key_var is None and not loopback:
            raise ProviderConfigError(f"{var('KEY_VAR')} is required: endpoint {name} is not on a loopback host")
        free = _yes_no(var("FREE_TIER"), False)
        rate_in, rate_out = _amount(var("INPUT_USD_PER_M"), None), _amount(var("OUTPUT_USD_PER_M"), None)
        if loopback or free:
            rate_in, rate_out = rate_in or 0.0, rate_out or 0.0
        elif not rate_in or not rate_out:
            raise ProviderConfigError(f"{var('INPUT_USD_PER_M')} and {var('OUTPUT_USD_PER_M')} must be positive for a hosted "
                                      f"endpoint. If the service charges nothing, state it: {var('FREE_TIER')}=YES")
        basis = BASIS_RATES if rate_in or rate_out else BASIS_LOCAL if loopback else BASIS_FREE
        effort = os.getenv(var("REASONING_EFFORT"), "").strip().lower() or None
        if effort is not None and effort not in ENDPOINT_REASONING_EFFORTS:
            raise ProviderConfigError(f"{var('REASONING_EFFORT')} must be one of " + ", ".join(ENDPOINT_REASONING_EFFORTS) + " (or unset)")
        tokens_raw = os.getenv(var("MAX_OUTPUT_TOKENS"), "").strip()
        try:
            tokens = int(tokens_raw) if tokens_raw else None
        except ValueError:
            raise ProviderConfigError(f"{var('MAX_OUTPUT_TOKENS')} must be a whole number of tokens") from None
        found[name] = Endpoint(name=name, base_url=raw, scheme=scheme, host=host, port=port, loopback=loopback, key_var=key_var,
                               input_rate=rate_in, output_rate=rate_out, basis=basis, reasoning_effort=effort,
                               max_output_tokens=tokens, send_sampling=_yes_no(var("SEND_SAMPLING"), True),
                               pause_seconds=_amount(var("PAUSE_SECONDS"), 0.0, MAX_PAUSE_SECONDS) or 0.0)
    # One key variable may serve two names only when both point at the same host and port: a key is
    # never sent to a second service.
    owner: dict[str, Endpoint] = {}
    for endpoint in found.values():
        first = owner.setdefault(endpoint.key_var, endpoint) if endpoint.key_var else endpoint
        if (first.host, first.port) != (endpoint.host, endpoint.port):
            raise ProviderConfigError(f"Endpoints {first.name} and {endpoint.name} are on different hosts and name the same key "
                                      "variable. Give each service its own key variable")
    return found


def endpoint_key_is_set(endpoint: Endpoint) -> bool:
    return bool(endpoint.key_var and os.getenv(endpoint.key_var, "").strip())


def endpoint_api_key(endpoint: Endpoint) -> str:
    """The key to send to this endpoint and to no other. A loopback server with none gets a placeholder."""
    key = os.getenv(endpoint.key_var, "").strip() if endpoint.key_var else ""
    if key:
        return key
    if endpoint.loopback:
        return LOCAL_PLACEHOLDER_KEY
    raise ProviderConfigError(f"{endpoint.key_var} missing (the key variable of endpoint {endpoint.name})")


def describe_endpoints() -> tuple[list[str], bool]:
    """Lines for `cli archive-check-endpoints` and whether the settings are usable. No model call and no
    HTTP request; the only network activity is one TCP connect per LOOPBACK endpoint. Prints the host,
    never the URL path, and only whether a key variable is set."""
    lines = ["archive-check-endpoints: no model call is made and no request is sent to any hosted service."]
    try:
        found = endpoints()
    except ProviderConfigError as err:
        return lines + [f"PROBLEM: {err}", "Not ready: fix the problem above. No call was made."], False
    if not found:
        return lines + [f"No named endpoint is configured ({config.ENDPOINTS_VARIABLE} is empty). "
                        "See docs/ANSWERS-OVER-TIME.md, \"Several models from several services\"."], True
    problems: list[str] = []
    for endpoint in found.values():
        where = "loopback: stays on this machine" if endpoint.loopback else "NOT loopback: prompts leave this machine"
        lines.append(f"Endpoint {endpoint.name}: host {endpoint.host}  port {endpoint.port}  scheme {endpoint.scheme}  ({where})")
        if endpoint.key_var:
            key_set = endpoint_key_is_set(endpoint)
            lines.append(f"  Key variable {endpoint.key_var} set: {'yes' if key_set else 'no'}"
                         + ("" if key_set or not endpoint.loopback else "  (not needed for a loopback server; a placeholder is sent)"))
            if not key_set and not endpoint.loopback:
                problems.append(f"{endpoint.key_var} missing (the key variable of endpoint {endpoint.name})")
        else:
            lines.append("  Key variable: none (loopback server; a placeholder is sent)")
        lines.append(f"  Rates (estimates): ${endpoint.input_rate:g} in / ${endpoint.output_rate:g} out per million tokens. "
                     f"Pricing basis: {endpoint.basis}" + {
                         BASIS_LOCAL: " (runs on this machine; recorded as $0 estimated)",
                         BASIS_FREE: " (the operator's own statement that this service charges nothing; NOT verified, and "
                                     "the local ceiling cannot see a charge here)",
                         BASIS_RATES: ""}[endpoint.basis])
        lines.append(f"  Pinned sampling sent: {'yes' if endpoint.send_sampling else 'no'}. Reasoning effort: "
                     f"{endpoint.reasoning_effort or 'not sent'}. Pause between calls: {endpoint.pause_seconds:g} s. "
                     f"Output limit: {endpoint.max_output_tokens or 'the archive default'}")
        if endpoint.loopback:
            reachable = _loopback_port_open(endpoint.host, endpoint.port)
            lines.append(f"  TCP connection to {endpoint.host}:{endpoint.port}: {'opens' if reachable else 'REFUSED or no answer'}"
                         + ("" if reachable else "  (is the local server running?)"))
        else:
            lines.append("  TCP connection: not tried (only loopback hosts are probed)")
    try:
        lines.append(f"Timeout per call: {timeout_seconds():g} s")
    except ProviderConfigError as err:
        problems.append(str(err))
    lines.append(f"Model calls allowed (LIVE_ALLOW_PAID_CALLS=YES): {'yes' if os.getenv('LIVE_ALLOW_PAID_CALLS') == 'YES' else 'no'}")
    lines += [f"PROBLEM: {p}" for p in problems]
    lines.append("Settings look usable. Not checked: that a server answers, knows a model, or returns token usage."
                 if not problems else "Not ready: fix the problems above. No call was made.")
    return lines, not problems


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
    sub.add_parser("archive-check-endpoints", help="Answers Over Time: show each named endpoint's host, whether its key variable "
                                                   "is set, rates and pricing basis (free; no model call, no key or URL path printed)")


def handle_command(args, parser) -> bool:
    if args.command not in ("check-provider", "archive-check-endpoints"):
        return False
    config.load_provider_credentials()  # CLI process only, as `round` does; only yes/no is ever printed
    lines, usable = describe() if args.command == "check-provider" else describe_endpoints()
    print("\n".join(lines))
    if not usable:
        parser.exit(1)
    return True
