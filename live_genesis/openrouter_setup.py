"""Free-only OpenRouter setup. Catalog reads only; never calls a model or reads a key."""
from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import build_opener, HTTPRedirectHandler

BASE = 'https://openrouter.ai/api/v1'
CATALOG = BASE + '/models'
FREE_ID = re.compile(r'[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*:free')


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
        if not isinstance(model, str) or not FREE_ID.fullmatch(model) or not isinstance(pricing, dict):
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
    if any(not FREE_ID.fullmatch(m) or m not in available for m in models):
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


def write_settings(path: Path, values: dict[str, str]) -> None:
    lines = ['# Free-only OpenRouter configuration. No key and no model call.']
    lines += [f"$env:{key} = '{value.replace(chr(39), chr(39) * 2)}'" for key, value in values.items()]
    with path.open('x', encoding='utf-8') as out:
        out.write('\n'.join(lines) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', action='append', default=[], help='Exact :free id; repeat up to three times')
    parser.add_argument('--out', type=Path, help='New PowerShell settings file; never overwritten')
    parser.add_argument('--catalog', type=Path, help='Use a saved catalog for offline setup')
    args = parser.parse_args()
    try:
        available = free_models(json.loads(args.catalog.read_text(encoding='utf-8')) if args.catalog else catalog())
        if not args.model:
            print('\n'.join(available) or 'No eligible free models found.')
            return
        if args.out is None:
            parser.error('--out is required when selecting models')
        write_settings(args.out, settings(args.model, available))
        print('Settings saved. Calls remain disabled. No key was read and no model was called.')
    except (OSError, ValueError):
        parser.exit(1, 'Setup failed: check the catalog, exact free model IDs, and that the output file does not exist.\n')


if __name__ == '__main__':
    main()
