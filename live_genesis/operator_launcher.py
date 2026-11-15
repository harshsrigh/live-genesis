"""Windows operator launcher for the free-only OpenRouter collector: `python -m live_genesis.operator_launcher`.

A guided wrapper over the existing paths (openrouter_setup, archive.plan, archive.run_archive, archive.status). It adds
no collector, no database, no route and no scheduler. Nothing is asked of any model at launch: the only network access
before consent is the public model catalog (no key, no prompt); a model call needs the owner to type the exact
confirmation text for this concrete run. The key is read with a hidden prompt, held in this process only and never
printed, written or passed on a command line."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from . import archive, controls, core, openrouter_setup as setup, providers

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path.home() / 'live-genesis-openrouter.sqlite'
KEY_VAR = 'OPENROUTER_API_KEY'
FREE_DAILY_REQUESTS = 50          # OpenRouter's documented free-model limit when checked, 14 Nov 2026 (docs/OPENROUTER-COLLECTOR.md)
RUN_CAP_USD = 0.01                # the gate needs a positive cap; every rate is zero, so the estimate stays 0
PREVIEW_PLACEHOLDER = 'preview-only-no-key'


class Stop(Exception):
    """The operator declined, or a choice was invalid. Nothing further happens."""


def choose_database(text: str | None) -> Path:
    path = Path(text).expanduser() if text else DEFAULT_DB
    path = path.resolve()
    if path == REPO or REPO in path.parents:
        raise Stop('Keep the database outside the repository (it must never be committed). Choose another --db path.')
    return path


def pick_models(available: list[str], ask) -> list[str]:
    if not available:
        raise Stop('No eligible free model was listed. Nothing was changed.')
    print('Eligible free models (explicit :free IDs, every catalog price zero):')
    for number, model in enumerate(available, 1):
        print(f'  {number:>3}. {model}')
    raw = ask('Numbers of one to three models, comma separated: ')
    try:
        numbers = [int(part) for part in raw.replace(' ', '').split(',') if part]
    except ValueError:
        numbers = []
    if not numbers or any(not 1 <= number <= len(available) for number in numbers):
        raise Stop('Not a valid list of numbers from the list. Nothing was changed.')
    picks = [available[number - 1] for number in numbers]
    if not 1 <= len(picks) <= 3 or len(set(picks)) != len(picks):
        raise Stop('Choose one to three different models.')
    return picks


def pick_samples(ask) -> int:
    print('Samples: each question is asked this many times of each model. The proposed setup uses 1; an older setup')
    print('recorded a preference for 3. Choose deliberately: it multiplies the calls and cannot be changed silently later.')
    try:
        count = int(ask(f'Samples per question per model (1-{archive.MAX_SAMPLES}): ').strip())
    except ValueError:
        raise Stop('Not a number. Nothing was changed.') from None
    if not 1 <= count <= archive.MAX_SAMPLES:
        raise Stop(f'Samples must be 1-{archive.MAX_SAMPLES}.')
    return count


def pick_question_set(ask) -> str:
    print('Question set: "core" is the locked model-track list (the proposed first private test). It is NOT the ten')
    print('manual chat-app prompts, and API replies are never relabelled as manual.')
    choice = (ask('Question set [core/trending] (default core): ').strip().lower() or 'core')
    if choice not in archive.TRACKS:
        raise Stop('Choose core or trending.')
    return choice


def load_key(secret, *, preview_only: bool) -> None:
    if os.environ.get(KEY_VAR):
        print(f'{KEY_VAR} is already set in this window (value not shown).')
    elif preview_only:
        os.environ[KEY_VAR] = PREVIEW_PLACEHOLDER      # the plan needs a key variable to exist; nothing is sent in a preview
    else:
        value = secret('OpenRouter key (hidden; nothing is echoed): ').strip()
        if not value:
            raise Stop('No key entered. Nothing was called.')
        os.environ[KEY_VAR] = value


def apply_settings(models: list[str], available: list[str], samples: int) -> None:
    values = setup.settings(models, available)
    values['LIVE_ARCHIVE_SAMPLES'] = str(samples)
    values['LIVE_ALLOW_PAID_CALLS'] = 'NO'             # raised only after the typed confirmation, in this process
    os.environ.update(values)
    os.environ['LIVE_COMPAT_JSON_MODE'] = 'off'        # answers are plain sentences, as in `cli archive-run`


def ensure_locked(track: str, ask) -> None:
    with core.connect() as cx:
        locked = archive._locked(cx, track)
    if locked:
        return
    print(f'The {track} question list is not locked in this database. Real answers need the lock; it is audited and its')
    print('questions can never change afterwards.')
    if ask('Lock it now? Type yes to lock: ').strip().lower() != 'yes':
        raise Stop('Not locked, so nothing can be collected. Nothing was called.')
    archive.lock(track)


def show_plan(track: str, samples: int) -> dict:
    planned = archive.plan(None, track, samples)
    print(archive.render_plan(planned))
    asked = len(planned['questions'])
    print(f"COUNT: {asked} questions x {len(planned['models'])} models x {samples} samples = {planned['calls']} calls.")
    print(f"Estimated cost: $0 (every rate is zero; {controls.COST_CAVEAT}). A free label is a check, not a billing guarantee:")
    print('keep the OpenRouter account unfunded and check its dashboard after the run.')
    if planned['calls'] > FREE_DAILY_REQUESTS:
        print(f"WARNING: {planned['calls']} calls exceed {FREE_DAILY_REQUESTS} free requests per day. A rate-limited model is")
        print('skipped for the rest of the run and leaves a gap. Failures use quota too. Never switch to a paid model.')
    return planned


def results(track: str) -> None:
    state = archive.status()
    runs = [r for r in state['runs'] if r['kind'] == 'real']
    print('Saved results (local only; the public site excludes the model track):')
    for run in runs[-3:]:
        print(f"  run {run['number']}: {run['status']}, {run['answers']} answers"
              + (f" - {run['stop_reason']}" if run['stop_reason'] else ''))
    last = state.get('last_run_answerers') or {}
    for item in last.get('answerers', []):
        print(f"  {item['answerer']}: {item['outcome']}{' (' + item['reason'] + ')' if item['reason'] else ''}")
    print(f'Database file: {core.db_path()}')
    print('Read the pages locally (PowerShell):')
    print(f'  $env:LIVE_DB = "{core.db_path()}"; python -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000')


def run(args, *, ask=input, secret=getpass.getpass) -> int:
    try:
        os.environ['LIVE_DB'] = str(choose_database(args.db))
        print(f'Database (outside git): {os.environ["LIVE_DB"]}')
        print('No model is called at launch. Only the public model catalog is read, with no key.')
        core.init_db()
        catalog = json.loads(Path(args.catalog).read_text(encoding='utf-8')) if args.catalog else setup.catalog()
        available = setup.free_models(catalog)
        models = pick_models(available, ask)
        samples = pick_samples(ask)
        track = pick_question_set(ask)
        load_key(secret, preview_only=args.preview_only)
        apply_settings(models, available, samples)
        if args.preview_only:
            show_plan(track, samples) if _locked(track) else print('Preview needs the lock; run without --preview-only to lock.')
            print('Preview only: nothing was called or recorded.')
            return 0
        ensure_locked(track, ask)
        planned = show_plan(track, samples)
        phrase = f"RUN {planned['calls']} CALLS"
        print(f'To collect, type exactly: {phrase}')
        if ask('> ').strip() != phrase:
            raise Stop('Confirmation not given. Nothing was called.')
        os.environ['LIVE_ALLOW_PAID_CALLS'] = 'YES'     # this process only, this run only
        done = archive.run_archive(max_usd=RUN_CAP_USD, question_set=track, samples=samples)
        print(f"Run {done['run']}: {done['status']}" + (f" - {done['stop_reason']}" if done['stop_reason'] else ''))
        results(track)
        return 0 if done['status'] in ('complete', 'complete_with_gaps') else 1
    except Stop as stop:
        print(stop)
        return 1
    except (archive.ArchiveRefused, controls.ConfigError, providers.ProviderConfigError, ValueError, OSError) as err:
        print(f'Stopped: {err}')
        return 1
    finally:
        os.environ.pop('LIVE_ALLOW_PAID_CALLS', None)
        if os.environ.get(KEY_VAR) == PREVIEW_PLACEHOLDER:
            os.environ.pop(KEY_VAR, None)


def _locked(track: str) -> bool:
    with core.connect() as cx:
        return bool(archive._locked(cx, track))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', help=f'Database file, outside the repository (default {DEFAULT_DB})')
    parser.add_argument('--catalog', help='A saved model catalog (JSON) instead of reading the public one')
    parser.add_argument('--preview-only', action='store_true', help='Show the plan and stop: no key, no call, nothing recorded')
    return run(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
