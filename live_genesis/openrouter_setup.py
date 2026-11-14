"""Free-only OpenRouter setup. Catalog reads only; never calls a model or reads a key."""
from __future__ import annotations

import argparse
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import build_opener, HTTPRedirectHandler

from . import providers

BASE = 'https://openrouter.ai/api/v1'
CATALOG = BASE + '/models'
FREE_ID = providers.OPENROUTER_FREE_ID   # one rule, shared with the request path (engine._chat)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def catalog() -> dict:
    # Public metadata, no Authorization header, no prompt, no SDK.
    with build_opener(NoRedirect()).open(CATALOG, timeout=20) as response:
        data = response.read(8_000_001)
    if len(data) > 8_000_000:
        raise ValueError('Catalog is too large')
    return json.loads(data)


def free_models(data: dict) -> list[str]:
    """Require an explicit :free id and zero on every advertised price dimension."""
    found = []
    for row in data.get('data', []):
        model, pricing = row.get('id', ''), row.get('pricing', {})
        if not providers.openrouter_free_model_ok(model) or not isinstance(pricing, dict):
            continue
        if 'prompt' not in pricing or 'completion' not in pricing:
            continue
        try:
            if any(not Decimal(str(v)).is_finite() or Decimal(str(v)) != 0 for v in pricing.values()):
                continue
        except (InvalidOperation, ValueError):
            continue
        architecture = row.get('architecture', {})
        if 'text' not in architecture.get('output_modalities', ['text']):
            continue
        found.append(model)
    return sorted(set(found))


def settings(models: list[str], available: list[str]) -> dict[str, str]:
    if not 1 <= len(models) <= 3 or len(set(models)) != len(models):
        raise ValueError('Choose one to three different models')
    if any(not providers.openrouter_free_model_ok(m) or m not in available for m in models):
        raise ValueError('Every model must be an explicit :free id with zero catalog prices')
    return {
        'LIVE_ARCHIVE_ENDPOINTS': 'openrouter-free',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_BASE_URL': BASE,
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_KEY_VAR': 'OPENROUTER_API_KEY',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_FREE_TIER': 'YES',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_INPUT_USD_PER_M': '0',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_OUTPUT_USD_PER_M': '0',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_SEND_SAMPLING': 'NO',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_PAUSE_SECONDS': '4',
        'LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_MAX_OUTPUT_TOKENS': '200',
        'LIVE_ARCHIVE_MODELS': ','.join(f'or-free-{i}=openrouter-free:{m}' for i, m in enumerate(models, 1)),
        'LIVE_ARCHIVE_SAMPLES': '1',
        'LIVE_ALLOW_PAID_CALLS': 'NO',
    }


def write_settings(path: Path, values: dict[str, str], shell: str = 'powershell') -> None:
    """A new settings file; an existing file is never overwritten. PowerShell (`$env:NAME = '...'`) or,
    for WSL / Linux / macOS, POSIX shell (`export NAME='...'`). Holds no key."""
    lines = ['# Free-only OpenRouter configuration. No key and no model call.']
    if shell == 'bash':
        lines += [f"export {key}='{value.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for key, value in values.items()]
    else:
        lines += [f"$env:{key} = '{value.replace(chr(39), chr(39) * 2)}'" for key, value in values.items()]
    with path.open('x', encoding='utf-8') as out:
        out.write('\n'.join(lines) + '\n')


def configured_models() -> list[str]:
    """The model ids that the current settings would send to OpenRouter (LIVE_ARCHIVE_MODELS entries on an
    endpoint whose host is OpenRouter). Reads settings only: no key, no database, no request."""
    records = providers.endpoints()
    on_openrouter = {name for name, record in records.items() if providers.is_openrouter_host(record.host)}
    found = []
    for entry in (part.strip() for part in os.getenv('LIVE_ARCHIVE_MODELS', '').split(',') if part.strip()):
        _, _, rest = entry.partition('=')
        endpoint, _, model = rest.partition(':')
        if endpoint.strip().lower() in on_openrouter:
            found.append(model.strip())
    return found


def check(models: list[str], available: list[str]) -> list[tuple[str, bool]]:
    """Each configured model with whether the catalog lists it, now, as an explicit free id at zero prices."""
    return [(model, providers.openrouter_free_model_ok(model) and model in available) for model in models]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', action='append', default=[], help='Exact :free id; repeat up to three times')
    parser.add_argument('--out', type=Path, help='New settings file; never overwritten')
    parser.add_argument('--shell', choices=('powershell', 'bash'), default='powershell',
                        help='Format of the settings file: powershell (default) or bash (WSL, Linux, macOS)')
    parser.add_argument('--catalog', type=Path, help='Use a saved catalog for offline setup')
    parser.add_argument('--check', action='store_true',
                        help='Before a collection: is every configured OpenRouter model still listed free? Reads the '
                             'public catalog only; no key, no model call. Exit code 1 if any is not')
    args = parser.parse_args()
    try:
        available = free_models(json.loads(args.catalog.read_text(encoding='utf-8')) if args.catalog else catalog())
        if args.check:
            try:
                results = check(configured_models(), available)
            except providers.ProviderConfigError as err:
                parser.exit(1, f'Check failed: {err}\n')
            if not results:
                parser.exit(1, 'Check failed: the current settings name no model on an OpenRouter endpoint. '
                               'Load your settings file first.\n')
            for model, ok in results:
                print(f"{model}: {'still listed free' if ok else 'NOT listed free now. Do not collect with it'}")
            if not all(ok for _, ok in results):
                parser.exit(1, 'At least one model is no longer free. Nothing was sent. Choose another free model in a NEW '
                               'settings file; never switch to a paid one.\n')
            print('All configured OpenRouter models are listed free. No key was read and no model was called.')
            return
        if not args.model:
            print('\n'.join(available) or 'No eligible free models found.')
            return
        if args.out is None:
            parser.error('--out is required when selecting models')
        write_settings(args.out, settings(args.model, available), args.shell)
        print('Settings saved. Calls remain disabled. No key was read and no model was called.')
    except (OSError, ValueError):
        parser.exit(1, 'Setup failed: check the catalog, exact free model IDs, and that the output file does not exist.\n')


if __name__ == '__main__':
    main()
