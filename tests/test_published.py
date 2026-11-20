"""Reviewed-data publishing: what the guard scan exempts, what git may hold, and the workflow that publishes.
Offline; no model is called. The export, approve, check and render steps themselves are covered in
tests/test_snapshot_archive_only.py and tests/test_archive_chatbots.py."""
import re
from pathlib import Path

import pytest

from live_genesis import snapshot

REPO = Path(__file__).resolve().parent.parent


def symlinks_work(folder):
    probe = folder / 'link-probe'
    try:
        probe.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def test_hash_fields_are_exempt_only_when_well_formed():
    assert snapshot.guard_hits({'id': 3, 'head_hash': 'a' * 64}, 'chatbots') == []
    assert snapshot.guard_hits({'id': 3, 'head_hash': 'someone@example.org'}, 'chatbots') == \
        ['chatbots id 3, field head_hash: looks like an email address']
    assert snapshot.guard_hits({'id': 3, 'answers': [{'text': 'b' * 64}]}, 'chatbots')  # not a hash field


def test_published_folder_is_committable_but_private_files_stay_ignored():
    rules = [line.strip() for line in (REPO / '.gitignore').read_text(encoding='utf-8').splitlines() if line.strip() and not line.startswith('#')]
    assert not any('published' in rule for rule in rules)
    assert {'.env', '*.db', '*.sqlite', 'secrets/*', 'data/*'} <= set(rules)
    assert 'published/* -text' in (REPO / '.gitattributes').read_text(encoding='utf-8')
    assert snapshot.PUBLISHED == REPO / 'published'


# ---- the workflow must fail closed: parsed structurally (line-based; no YAML dependency) ------

WORKFLOW = REPO / '.github' / 'workflows' / 'snapshot.yml'
CHECK_LINE = 'python -m live_genesis.snapshot --check published'


def parse_jobs(text):
    """{job: {'keys': {key: value}, 'steps': [{'keys': {key: value}, 'run': str}]}} for the
    two-space-indented layout this workflow uses."""
    jobs, job, step, in_run = {}, None, None, False
    lines = text.splitlines()
    start = lines.index('jobs:')
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 2:
            job = jobs.setdefault(line.strip().rstrip(':'), {'keys': {}, 'steps': []}); step = None; in_run = False
        elif indent == 4 and job is not None:
            key, _, value = line.strip().partition(':')
            job['keys'][key] = value.strip(); step = None; in_run = False
        elif indent == 6 and line.lstrip().startswith('- '):
            key, _, value = line.strip()[2:].partition(':')
            step = {'keys': {key: value.strip()}, 'run': ''}; job['steps'].append(step)
            in_run = key == 'run'
            if in_run and value.strip() != '|':
                step['run'] = value.strip()
        elif indent == 8 and step is not None and not in_run:
            key, _, value = line.strip().partition(':')
            step['keys'][key] = value.strip()
            in_run = key == 'run'
            if in_run and value.strip() != '|':
                step['run'] = value.strip()
        elif indent == 8 and step is not None and in_run and re.match(r'[a-z][a-z-]*:', line.strip()) and step['keys'].get('run') != '|':
            key, _, value = line.strip().partition(':')
            step['keys'][key] = value.strip(); in_run = False
        elif step is not None and in_run:
            step['run'] += line.strip() + '\n'
        elif step is not None:
            step['keys'].setdefault('_nested', ''); step['keys']['_nested'] += line.strip() + '\n'
    return jobs


def workflow_problems(text):
    problems = []
    jobs = parse_jobs(text)
    if set(jobs) != {'test', 'snapshot'}:
        return [f'unexpected jobs {sorted(jobs)}']
    if not any('python -m pytest' in step['run'] for step in jobs['test']['steps']):
        problems.append('test job does not run pytest')
    job = jobs['snapshot']
    if job['keys'].get('needs') != 'test':
        problems.append('snapshot job must need the test job')
    if job['keys'].get('if') != "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'":
        problems.append('snapshot job condition changed')
    if 'continue-on-error' in job['keys'] or 'continue-on-error' in jobs['test']['keys']:
        problems.append('a job continues on error')
    steps = job['steps']
    for step in steps:
        for banned in ('if', 'continue-on-error'):
            if banned in step['keys']:
                problems.append(f"a snapshot step has `{banned}`")
        if '||' in step['run'] or 'set +e' in step['run']:
            problems.append('a snapshot step swallows a failure')
    checks = [i for i, step in enumerate(steps) if '--check' in step['run']]
    flags = [i for i, step in enumerate(steps) if re.search(r'HAVE_PUBLISHED=1"? *>>', step['run'])]
    if len(checks) != 1 or flags != checks:
        problems.append('exactly one step may run the check, and only it may set HAVE_PUBLISHED')
    else:
        run = steps[checks[0]]['run'].splitlines()
        check_at = [n for n, line in enumerate(run) if line == CHECK_LINE]
        flag_at = [n for n, line in enumerate(run) if 'HAVE_PUBLISHED' in line]
        if len(check_at) != 1 or len(flag_at) != 1 or check_at[0] > flag_at[0]:
            problems.append('the check must be one plain command that precedes HAVE_PUBLISHED')
        if run[0] != 'if [ -e published ]; then':
            problems.append('the check must run whenever published/ exists')
        later = [i for i, step in enumerate(steps) if 'live_genesis.snapshot "$RUNNER_TEMP' in step['run'] or 'git push' in step['run']]
        if not later or min(later) < checks[0]:
            problems.append('the check must come before any build or publish step')
    pushes = [i for i, step in enumerate(steps) if 'git push' in step['run']]
    if pushes != [len(steps) - 1]:
        problems.append('the publish step must be the single last step')
    builds = ''.join(step['run'] for step in steps)
    # Owner decision, 20 Nov 2026: once real answers are published, the scripted sample is not on the site.
    if '--sibling' in builds or 'site/demo' in builds:
        problems.append('the scripted sample must not be built beside published answers')
    for needed in ('--from-data published --archive-only', 'if [ "${HAVE_PUBLISHED:-}" = "1" ]; then'):
        if needed not in builds:
            problems.append(f'missing: {needed}')
    # Owner decision, 13 Nov 2026: every published variant is Answers Over Time alone. Since the agent society was
    # removed the flag changes nothing, but it stays on every build so the workflow says what it publishes.
    for gone in ('seed_agents', 'cli demo\n', 'demo-game', 'demo-document'):
        if gone in builds:
            problems.append(f'the workflow still runs a removed society command: {gone.strip()}')
    if 'python -m live_genesis.cli demo-archive' not in builds:
        problems.append('the scripted demo archive is not built')
    built = [line for line in builds.splitlines() if 'live_genesis.snapshot "$RUNNER_TEMP' in line]
    if len(built) != 2 or not all(line.endswith(' --archive-only') for line in built):
        problems.append('every site build must use --archive-only')
    if 'runner.temp' in text:
        problems.append('runner.temp is not allowed in job-level env')
    if text.count('contents: write') != 1 or text.count('contents: read') != 1 or jobs['snapshot']['keys'].get('permissions') != '':
        problems.append('permissions changed')
    return problems


def test_workflow_fails_closed():
    assert workflow_problems(WORKFLOW.read_text(encoding='utf-8')) == []


def mutate(old, new):
    def apply(text):
        assert text.count(old) == 1, old
        return text.replace(old, new)
    return apply


CHECK_STEP = '      - name: Reviewed data must be intact and approved (fails the job, nothing is published)\n'
MUTATIONS = {
    'check || true': mutate(CHECK_LINE + '\n', CHECK_LINE + ' || true\n'),
    'continue-on-error on the check step': mutate(CHECK_STEP, CHECK_STEP + '        continue-on-error: true\n'),
    'HAVE_PUBLISHED before the check': mutate(
        '            ' + CHECK_LINE + '\n            echo "HAVE_PUBLISHED=1" >> "$GITHUB_ENV"\n',
        '            echo "HAVE_PUBLISHED=1" >> "$GITHUB_ENV"\n            ' + CHECK_LINE + '\n'),
    'if: always() on the publish step': mutate('      - name: Publish to gh-pages\n', '      - name: Publish to gh-pages\n        if: always()\n'),
    'needs: test deleted': mutate('    needs: test\n', ''),
    'check step deleted': lambda text: text[:text.index(CHECK_STEP)] + text[text.index('      - name: Build scripted demo archive'):],
    'check moved after the build': mutate('          python -m live_genesis.snapshot "$RUNNER_TEMP/site" --archive-only\n',
                                         '          python -m live_genesis.snapshot "$RUNNER_TEMP/site" --archive-only\n          ' + CHECK_LINE + '\n'),
    'demo-only site built without the flag': mutate('"$RUNNER_TEMP/site" --archive-only\n', '"$RUNNER_TEMP/site"\n'),
    'reviewed root built without the flag': mutate('--from-data published --archive-only\n', '--from-data published\n'),
    'sample site built beside the published answers': mutate('--from-data published --archive-only\n',
                                                              '--from-data published --sibling demo/ --archive-only\n'),
    'check only under a narrower condition': mutate('if [ -e published ]; then', 'if [ -f published/APPROVED ]; then'),
    'job-level continue-on-error': mutate('    needs: test\n', '    needs: test\n    continue-on-error: true\n'),
}


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_workflow_mutations_are_caught(name):
    mutated = MUTATIONS[name](WORKFLOW.read_text(encoding='utf-8'))
    assert workflow_problems(mutated), name
