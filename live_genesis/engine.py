"""The inference adapters: one request to OpenAI, Anthropic or an OpenAI-compatible (Chat Completions) server.

`infer` is the only thing here that contacts a provider, and only live_genesis/archive.py calls it
(`cli archive-run` and `cli archive-chatbot-ask`, both through `archive._paid_call`), after its own gates: provider settings, key, `LIVE_ALLOW_PAID_CALLS=YES`, budget
config, a held reservation. No payment, email, browser or posting tool exists here.

Provider API keys are read from the process environment only. This module never loads `.env`; the
operator CLI does (config.load_provider_credentials) just before a paid run, so importing this module
can never pull a key into a process.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from . import providers

STOP_VALUES = ('end', 'length', 'other', 'unknown')


class ProviderNotReady(RuntimeError):
    """Local setup problem detected before any request was sent to a provider."""


class UsageMissing(RuntimeError):
    """The provider answered without usable token counts. live_genesis/archive.py records it under its own category."""


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    # Why the provider stopped, normalised: 'end' (finished), 'length' (cut off at the output limit),
    # 'other' (refusal, filter, tool call, ...) or 'unknown' (the server did not say).
    stop: str = 'unknown'
    # What the provider's response said about itself. Untrusted text: only live_genesis/archive.py reads these,
    # and it shape-checks each before storing. None when the server did not say (or the adapter was replaced).
    reported_model: str | None = None      # the model name in the response, which can differ from the one requested
    system_fingerprint: str | None = None  # Chat Completions only, when the server sends one
    finish_reason: str | None = None       # the raw finish / stop reason behind `stop`
    sampling: dict | None = None           # the sampling parameters actually sent ({} = none); None = not recorded


def _stop(value: Any, *, end: tuple[str, ...], length: str) -> str:
    if not isinstance(value, str) or not value:
        return 'unknown'
    return 'length' if value == length else 'end' if value in end else 'other'


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class UnexpectedCharge(UsageMissing):
    """A free-only request came back reporting a cost above zero, or one that cannot be read. The answer is not
    recorded. live_genesis/archive.py (`_paid_call`) stops the run and keeps the hold unconfirmed, so nothing more
    is sent until the owner has looked at the provider's billing page and run `cli reconcile`. A UsageMissing, so
    any other caller still treats it as a failed call."""


def _reported_charge(usage: Any) -> bool:
    """True when usage reports a nonzero or unreadable `cost` (OpenRouter adds it; the SDK keeps it as an extra).
    An absent cost is NOT proof of zero billing: only token counts and the declared-free rate are known."""
    for source in (usage, getattr(usage, 'model_extra', None)):
        if source is None:
            continue
        value = source.get('cost') if isinstance(source, dict) else getattr(source, 'cost', None)
        if value is None:
            continue
        try:
            number = Decimal(str(value))
            if not number.is_finite() or number != 0:
                return True
        except Exception:
            return True
    return False


def _chat(client: Any, model: str, system: str, prompt: str, max_output_tokens: int, *, json_mode: bool,
          reasoning_effort: str | None, sampling: dict | None, free_openrouter: bool = False) -> Completion:
    """One Chat Completions request on a client that is already built. Shared by the single
    `openai-compatible` provider and the named archive endpoints."""
    request: dict[str, Any] = {'model': model, 'max_tokens': max_output_tokens,
                               'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]}
    if json_mode:
        request['response_format'] = {'type': 'json_object'}
    if reasoning_effort:
        request['reasoning_effort'] = reasoning_effort
    sent = {key: sampling[key] for key in ('temperature', 'seed') if sampling and key in sampling}
    request.update(sent)
    if free_openrouter:
        # Checked here as well as in the settings, so no caller can reach OpenRouter with another id. Nothing is sent.
        if not providers.openrouter_free_model_ok(model):
            raise ProviderNotReady('A model ' + providers.OPENROUTER_RULE)
        request['extra_body'] = {'provider': {
            'allow_fallbacks': False,
            'max_price': {'prompt': 0, 'completion': 0, 'request': 0, 'image': 0},
        }}
    response = client.chat.completions.create(**request)
    usage = getattr(response, 'usage', None)
    if free_openrouter and _reported_charge(usage):
        # A "free" call that reports a charge (or one we cannot read) is not recorded as free.
        raise UnexpectedCharge('Charge reported on a free-only call; not recorded as free')
    tokens = (getattr(usage, 'prompt_tokens', None), getattr(usage, 'completion_tokens', None))
    if any(type(n) is not int or n < 0 for n in tokens):
        # No numbers are invented: the callers keep the hold as unconfirmed and stop.
        raise UsageMissing('Provider usage missing; inference not recorded as priced')
    choices = getattr(response, 'choices', None) or []
    text = getattr(getattr(choices[0], 'message', None), 'content', None) if choices else None
    finish = getattr(choices[0], 'finish_reason', None) if choices else None
    return Completion(text if isinstance(text, str) else '', tokens[0], tokens[1], _stop(finish, end=('stop',), length='length'),
                      reported_model=_text_or_none(getattr(response, 'model', None)),
                      system_fingerprint=_text_or_none(getattr(response, 'system_fingerprint', None)),
                      finish_reason=_text_or_none(finish), sampling=sent if sampling is not None else None)


def _trust_env(base_url: str) -> bool:
    """False for a loopback base URL: its client then ignores HTTP_PROXY / HTTPS_PROXY / ALL_PROXY, so a request
    to a server on this machine is never handed to a proxy somewhere else (the zero rate and the word "local"
    rest on that). A hosted base URL keeps the environment's proxy settings, which may be how it is reached."""
    from urllib.parse import urlsplit
    try:
        host = urlsplit(base_url).hostname
    except ValueError:
        return True
    return not providers.is_loopback_host(host)


def infer(provider: str, model: str, prompt: str, *, system: str, max_output_tokens: int,
          endpoint: Any = None, sampling: dict | None = None) -> Completion:
    """One provider request: `system` as the system text, `prompt` as the one user message, at most
    `max_output_tokens` output tokens.

    `endpoint` is a providers.Endpoint: the request then goes to that server and to no other, with that
    endpoint's own key. `sampling` ({'temperature': 0, 'seed': N}) is sent as far as the API takes it:
    Chat Completions both, the two native APIs `temperature` only (neither has a seed).
    Completion.sampling says what was sent."""
    try:
        timeout = providers.timeout_seconds()  # LIVE_INFER_TIMEOUT_SECONDS: default 35, clamped 5-600
        compat = providers.compat_settings() if provider == providers.COMPATIBLE and endpoint is None else None
        compat_key = providers.compat_api_key(compat) if compat else None
        endpoint_key = providers.endpoint_api_key(endpoint) if endpoint is not None else None
    except providers.ProviderConfigError as err:
        raise ProviderNotReady(str(err)) from None
    if endpoint is not None:
        if provider != providers.COMPATIBLE:
            raise ProviderNotReady('A named endpoint speaks the OpenAI Chat Completions API only')
        # A client per call, built from this endpoint's record alone: its base URL, its own key, no retry, and no
        # redirect followed (a redirect would carry the prompt, and could carry the key, to a host nobody configured).
        import httpx
        from openai import OpenAI
        with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=_trust_env(endpoint.base_url)) as http:
            client = OpenAI(base_url=endpoint.base_url, api_key=endpoint_key, timeout=timeout, max_retries=0, http_client=http)
            return _chat(client, model, system, prompt, max_output_tokens, json_mode=False,
                         reasoning_effort=endpoint.reasoning_effort, sampling=sampling,
                         free_openrouter=providers.is_openrouter_host(endpoint.host))
    if compat:
        # Any server speaking the OpenAI Chat Completions API (local or hosted). The base URL and key are
        # passed explicitly, never taken from OPENAI_* variables. Exceptions from here on are caught by the
        # callers, which never echo their text.
        # No redirect is followed here either (the SDK's own HTTP client would follow one): the request
        # goes to the configured host or fails. A 3xx answer is an error for the caller, like any other.
        import httpx
        from openai import OpenAI
        with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=_trust_env(compat.base_url)) as http:
            client = OpenAI(base_url=compat.base_url, api_key=compat_key, timeout=timeout, max_retries=0, http_client=http)
            return _chat(client, model, system, prompt, max_output_tokens, json_mode=compat.json_mode,
                         reasoning_effort=compat.reasoning_effort, sampling=sampling)
    sent = {'temperature': sampling['temperature']} if sampling and 'temperature' in sampling else {}
    recorded = sent if sampling is not None else None
    if provider == 'openai':
        if not os.getenv('OPENAI_API_KEY'):
            raise ProviderNotReady('OPENAI_API_KEY missing')
        import httpx
        from openai import OpenAI
        with httpx.Client(follow_redirects=False, timeout=timeout) as http:  # as above: no redirect followed
            client = OpenAI(timeout=timeout, max_retries=0, http_client=http)
            response = client.responses.create(
                model=model, instructions=system,
                input=prompt, max_output_tokens=max_output_tokens, **sent,
            )
        usage = response.usage
        if usage is None:
            raise UsageMissing('Provider usage missing; inference not recorded as priced')
        status = raw = getattr(response, 'status', None)
        if status == 'incomplete':
            # Reasoning tokens count against max_output_tokens here: a reply can be cut off before any text.
            status = getattr(getattr(response, 'incomplete_details', None), 'reason', None) or 'incomplete'
        return Completion(response.output_text, int(usage.input_tokens), int(usage.output_tokens),
                          _stop(status, end=('completed',), length='max_output_tokens'),
                          reported_model=_text_or_none(getattr(response, 'model', None)),
                          finish_reason=_text_or_none(status) or _text_or_none(raw), sampling=recorded)
    if provider == 'anthropic':
        if not os.getenv('ANTHROPIC_API_KEY'):
            raise ProviderNotReady('ANTHROPIC_API_KEY missing')
        try:
            from anthropic import Anthropic
        except ImportError as err:
            raise ProviderNotReady('Install optional Anthropic SDK: pip install -r requirements-anthropic.txt') from err
        # The Anthropic SDK's own HTTP client follows redirects too, and its key travels in an `x-api-key`
        # header that a redirect would carry along: same rule, no redirect followed.
        import httpx
        with httpx.Client(follow_redirects=False, timeout=timeout) as http:
            client = Anthropic(timeout=timeout, max_retries=0, http_client=http)
            response = client.messages.create(
                model=model, max_tokens=max_output_tokens, system=system,
                messages=[{'role':'user', 'content':prompt}], **sent,
            )
        texts = [part.text for part in response.content if getattr(part, 'type', '') == 'text']
        # Thinking tokens, where the model thinks by default, count against max_tokens as well.
        return Completion('\n'.join(texts), int(response.usage.input_tokens), int(response.usage.output_tokens),
                          _stop(getattr(response, 'stop_reason', None), end=('end_turn', 'stop_sequence'), length='max_tokens'),
                          reported_model=_text_or_none(getattr(response, 'model', None)),
                          finish_reason=_text_or_none(getattr(response, 'stop_reason', None)), sampling=recorded)
    raise ProviderNotReady(providers.UNKNOWN_PROVIDER)


# The unpatched adapter. archive.py compares `engine.infer` with this to store any run made through a
# replaced `infer` (tests, experiments) as mock, so it can never be shown as a result.
REAL_INFER = infer
