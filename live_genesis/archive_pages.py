"""Static HTML for Answers Over Time: the question index and one page per question.

Pure functions from the public payload (archive.public_payload, or a reviewed
published/archive.json) to HTML text. No database, no model, no JavaScript, no inline style
or script: the pages work under the site's Content-Security-Policy and with scripts off.
Every piece of text that reaches the page goes through `esc`; model output is untrusted.
All links are relative, so the same pages work in a static snapshot under any sub-path and
in both web apps (`/archive/index.html`, `/archive/q/Q01.html`).
"""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

from . import archive
from . import web  # CONTENT_SECURITY_POLICY is read at call time (web imports this module for its routes)

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = ("archive.css", "archive-mark.svg")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty", 25: "twenty-five", 30: "thirty",
                40: "forty"}
SOURCE_LABELS = {"engine": "server call", "api": "self-declared", "demo": "scripted demo"}
MAX_COLUMNS = archive.MAX_ANSWERERS + 10   # layout classes exist up to this many answer columns


def esc(value: object) -> str:
    """The one escaping helper: safe in element content and in a double-quoted attribute."""
    return html.escape(str(value), quote=True)


def asset_versions() -> dict[str, str]:
    """Short content hash per asset, appended to its URL so a changed file is fetched again."""
    return {name: hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12] for name in ASSETS}


def count_word(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


def _date(value: str) -> tuple[int, int, int] | None:
    try:
        year, month, day = int(value[0:4]), int(value[5:7]), int(value[8:10])
    except (ValueError, TypeError):
        return None
    return (year, month, day) if 1 <= month <= 12 and 1 <= day <= 31 else None


def month_year(value: str) -> str:
    parts = _date(value)
    return f"{MONTHS[parts[1] - 1]} {parts[0]}" if parts else "Undated"


def full_date(value: str) -> str:
    parts = _date(value)
    return f"{parts[2]} {MONTHS[parts[1] - 1]} {parts[0]}" if parts else "date not recorded"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


class _View:
    """Lookups over one payload, shared by both page kinds."""

    def __init__(self, payload: dict):
        problem = archive.payload_problem(payload)
        if problem:
            raise ValueError(f"archive payload is malformed: {problem}")
        self.payload = payload
        self.questions = payload["questions"]
        self.runs = sorted(payload["runs"], key=lambda r: r["number"])
        self.labels = list(payload["answerers"])
        # Mock rows are never rendered, whatever the caller passed in.
        self.answers = [a for a in payload["answers"] if a["source"] in archive.PUBLIC_SOURCES]
        self.cell = {(a["question"], a["answerer"], a["run"]): a for a in self.answers}
        self.by_series: dict[tuple[str, str], list[dict]] = {}
        for answer in sorted(self.answers, key=lambda a: a["run"]):
            self.by_series.setdefault((answer["question"], answer["answerer"]), []).append(answer)
        self.run_date = {r["number"]: r["date"] for r in self.runs}
        self.demo = any(a["source"] == "demo" for a in self.answers) or any(r["demo"] for r in self.runs)
        self.empty = not self.answers

    def series(self, question_id: str, label: str) -> list[tuple[dict, dict | None]]:
        return [(run, self.cell.get((question_id, label, run["number"]))) for run in self.runs]

    def tag(self) -> str:
        if self.empty:
            return "No answers recorded yet"
        if self.demo:
            return "Scripted demo answers and dates · no model was called"
        return "Recorded answers · model output, may be wrong"

    def previous_model(self, question_id: str, label: str, run_number: int) -> str | None:
        earlier = [a for a in self.by_series.get((question_id, label), ()) if a["run"] < run_number]
        return earlier[-1]["model"] if earlier else None


def _kind(answer: dict | None) -> str:
    if answer is None or answer["state"] != "answered":
        return "none"
    return "changed" if answer["changed"] else "same"


def _head(title: str, up: str, versions: dict[str, str], description: str) -> str:
    return ('<!doctype html>\n<html lang="en">\n<head>\n'
            f'  <meta http-equiv="Content-Security-Policy" content="{esc(web.CONTENT_SECURITY_POLICY)}" />\n'
            '  <meta charset="utf-8" />\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
            f'  <title>{esc(title)}</title>\n'
            f'  <meta name="description" content="{esc(description)}" />\n'
            f'  <link rel="icon" type="image/svg+xml" href="{up}static/archive-mark.svg?v={versions["archive-mark.svg"]}" />\n'
            f'  <link rel="stylesheet" href="{up}static/archive.css?v={versions["archive.css"]}" />\n'
            '</head>\n<body>\n')


def _bar(up: str, index_href: str, versions: dict[str, str], on_index: bool) -> str:
    current = ' aria-current="page"' if on_index else ""
    return ('<div class="bar"><div class="bar-in">\n'
            f'  <a class="wordmark" href="{index_href}"><img class="mark" src="{up}static/archive-mark.svg?v={versions["archive-mark.svg"]}" '
            'alt="" width="72" height="24" />Answers Over Time</a>\n'
            '  <nav aria-label="Archive navigation">\n'
            f'    <a href="{index_href}"{current}>Questions</a>\n'
            f'    <a href="{index_href}#method">Method</a>\n'
            f'    <a href="{up}">Observatory</a>\n'
            '  </nav>\n'
            '  <span class="mono small muted">READ-ONLY ARCHIVE</span>\n'
            '</div></div>\n')


def _strip(view: _View, question_id: str, label: str) -> tuple[str, int, int]:
    squares, changes, missing = [], 0, 0
    for _run, answer in view.series(question_id, label):
        kind = _kind(answer)
        changes += kind == "changed"
        missing += kind == "none"
        squares.append(f'<span class="sq sq-{kind}"></span>')
    summary = (f"{label}: {_plural(changes, 'change')} across {_plural(len(view.runs), 'run')}"
               + (f", {missing} without an answer" if missing else ""))
    return (f'<div class="strip-row"><span class="strip-name mono">{esc(label)}</span>'
            f'<div class="strip" role="img" aria-label="{esc(summary)}">{"".join(squares)}</div></div>'), changes, missing


def _last_change(view: _View, question_id: str) -> str:
    if not any(a["question"] == question_id for a in view.answers):
        return "No answers recorded yet"
    changed = [a for a in view.answers if a["question"] == question_id and _kind(a) == "changed"]
    if not changed:
        return f"No change in any model across {_plural(len(view.runs), 'run')}"
    last = max(changed, key=lambda a: a["run"])
    return f"Last change: {last['answerer']}, {month_year(view.run_date[last['run']])}"


def render_index(payload: dict, versions: dict[str, str] | None = None) -> str:
    view = _View(payload)
    versions = versions or asset_versions()
    total = count_word(len(view.questions))
    out = [_head("Answers Over Time", "../", versions,
                 "The same simple questions, asked of AI models again every month, with every answer kept."),
           _bar("../", "index.html", versions, True), '<div class="wrap">\n']
    out.append('<header class="hero">\n  <div class="hero-copy">\n'
               f'    <span class="tag tag-dark mono">{esc(view.tag())}</span>\n'
               '    <h1 class="display">Simple questions, asked again every month.</h1>\n'
               '    <p class="lede">People ask AI the same basic things year after year. This archive keeps every answer '
               'with its date and the model that gave it, so you can see when an answer changes.</p>\n'
               '  </div>\n  <div class="legend">\n'
               '    <strong class="legend-title">How to read a strip</strong>\n'
               '    <span class="legend-note">One square per run, oldest on the left.</span>\n'
               '    <div class="legend-row"><span class="sq sq-same"></span><span>Same answer as the run before, or the first answer</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-changed"></span><span>The answer changed</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-none"></span><span>No answer recorded</span></div>\n'
               '  </div>\n</header>\n')
    for group in payload["groups"]:
        questions = [q for q in view.questions if q["group"] == group["name"]]
        if not questions:
            continue
        out.append(f'<section class="group">\n  <div class="group-head"><h2>{esc(group["name"])}</h2>'
                   f'<span class="muted">{esc(group["why"])}</span></div>\n')
        for question in questions:
            strips = "".join(_strip(view, question["id"], label)[0] for label in view.labels)
            if not strips:
                strips = '<span class="muted small">No runs recorded yet.</span>'
            out.append('  <article class="q-row">\n    <div class="q-main">'
                       f'<span class="q-id mono">{esc(question["id"])}</span>'
                       f'<div class="q-text"><a href="q/{esc(question["id"])}.html">{esc(question["text"])}</a>'
                       f'<span class="muted small">{esc(_last_change(view, question["id"]))}</span></div></div>\n'
                       f'    <div class="q-strips">{strips}</div>\n  </article>\n')
        out.append('</section>\n')
    if payload["locked"]:
        list_note = (f'The list of {total} questions was fixed by the owner on {esc(full_date(payload["locked_at"] or ""))} '
                     'and does not change.')
    else:
        list_note = (f'This list of {total} questions is a draft. Once the owner fixes it and the archive starts, '
                     'it does not change.')
    out.append(f'<p class="list-note muted">{list_note} Runs are made about once a month.</p>\n')
    out.append('<section id="method" class="method">\n  <h2 class="display">How the record is kept</h2>\n  <div class="method-grid">\n'
               '    <div><strong>Answered alone</strong><span>No model sees another model\'s answer, in that run or any earlier one.</span></div>\n'
               '    <div><strong>Same words every time</strong><span>The exact prompt is shown on each question\'s page and never edited.</span></div>\n'
               '    <div><strong>Labelled by source</strong><span>Every answer says whether this server called the model or an outside client sent it. '
               'Scripted sample answers are labelled as scripted.</span></div>\n'
               '    <div><strong>"Changed" is a plain comparison</strong><span>A square is dark when the text differs from that model\'s previous '
               'answer, ignoring letter case, spacing, and punctuation at the ends. It does not say which answer is right.</span></div>\n'
               '  </div>\n</section>\n')
    honesty = ("Sample answers on this page are scripted, not model output." if view.demo
               else "Answers are model output and may be wrong.")
    out.append(f'<footer class="foot"><span>Part of LIVE Genesis. {honesty}</span>'
               '<span class="mono small">No payments · nothing can be changed from this page</span></footer>\n')
    out.append('</div>\n</body>\n</html>\n')
    return "".join(out)


def _moved(view: _View, question_id: str) -> str:
    """The generated "what moved" list: only what the comparison itself shows."""
    items: list[tuple[str, str]] = []
    for run in reversed(view.runs):
        for label in view.labels:
            answer = view.cell.get((question_id, label, run["number"]))
            if answer is None:
                continue
            before = view.previous_model(question_id, label, run["number"])
            new_model = before is not None and before != answer["model"]
            when = month_year(run["date"])
            if _kind(answer) == "changed":
                text = f"{label}'s answer changed."
                if new_model:
                    text += f" Its model ID changed from {before} to {answer['model']}."
                items.append((when, text))
            elif new_model:
                items.append((when, f"{label}'s model ID changed from {before} to {answer['model']}; "
                                    + ("the answer stayed the same." if _kind(answer) == "same" else "no answer was recorded.")))
    for label in view.labels:
        series = [a for _run, a in view.series(question_id, label) if a is not None and a["state"] == "answered"]
        if series and not any(a["changed"] for a in series):
            items.append(("—", f"{label} gave the same answer in every run recorded."))
    if not items:
        return '<p class="muted small">Nothing has been recorded for this question yet.</p>'
    return "".join(f'<div class="moved-row"><span class="moved-when mono">{esc(when)}</span><span>{esc(text)}</span></div>'
                   for when, text in items)


def _cell(view: _View, question_id: str, label: str, run: dict) -> str:
    answer = view.cell.get((question_id, label, run["number"]))
    if answer is None:
        return '<div class="cell cell-none" role="cell">No answer recorded in this run</div>'
    source = SOURCE_LABELS[answer["source"]]
    before = view.previous_model(question_id, label, run["number"])
    meta = f"{answer['model']} · {source}" + (" · new model ID" if before is not None and before != answer["model"] else "")
    if answer["state"] == "withheld":
        return (f'<div class="cell cell-none" role="cell"><span>Withheld by content check ({esc(answer["reason"])})</span>'
                f'<span class="cell-meta mono">{esc(meta)}</span></div>')
    if answer["state"] != "answered":
        return (f'<div class="cell cell-none" role="cell"><span>No answer recorded: {esc(answer["reason"] or "none given")}</span>'
                f'<span class="cell-meta mono">{esc(meta)}</span></div>')
    if answer["changed"]:
        return ('<div class="cell cell-changed" role="cell"><span class="changed-tag mono">CHANGED</span>'
                f'<span class="cell-text">{esc(answer["text"])}</span><span class="cell-meta mono">{esc(meta)}</span></div>')
    return (f'<div class="cell cell-same" role="cell"><span class="cell-text">{esc(answer["text"])}</span>'
            f'<span class="cell-meta mono">{esc(meta)}</span></div>')


def render_question(payload: dict, question_id: str, versions: dict[str, str] | None = None) -> str:
    view = _View(payload)
    versions = versions or asset_versions()
    question = next((q for q in view.questions if q["id"] == question_id), None)
    if question is None:
        raise KeyError(question_id)
    columns = min(max(len(view.labels), 1), MAX_COLUMNS)
    out = [_head(f"{question['id']} · Answers Over Time", "../../", versions, question["text"]),
           _bar("../../", "../index.html", versions, False), '<div class="wrap">\n']
    out.append('<header class="q-hero">\n  <div class="chips">'
               f'<span class="q-id mono">{esc(question["id"])}</span><span class="chip">{esc(question["group"])}</span>'
               f'<span class="tag tag-dark mono">{esc(view.tag())}</span></div>\n'
               f'  <h1 class="display display-q">{esc(question["text"])}</h1>\n'
               f'  <p class="lede">{esc(question["rationale"])}</p>\n</header>\n')
    out.append('<div class="split">\n<main class="answers">\n'
               '  <div class="group-head spread"><h2>Every answer, newest first</h2>'
               '<span class="muted small">Dark cells differ from that model\'s answer before</span></div>\n')
    if view.runs and view.labels:
        out.append(f'  <div class="grid-scroll" tabindex="0" role="region" aria-label="Answers by date and model">\n'
                   f'  <div class="grid n-{columns}" role="table" aria-label="Answers to {esc(question["id"])}, newest first">\n'
                   '    <div class="grid-row grid-head mono" role="row"><span role="columnheader">DATE</span>'
                   + "".join(f'<span role="columnheader">{esc(label.upper())}</span>' for label in view.labels) + '</div>\n')
        for run in reversed(view.runs):
            note = "scripted date" if run["demo"] else f"run {run['number']}"
            out.append('    <div class="grid-row" role="row"><div class="when" role="rowheader">'
                       f'<span class="when-month">{esc(month_year(run["date"]))}</span>'
                       f'<span class="when-day mono">{esc(full_date(run["date"]))}</span>'
                       f'<span class="when-day mono">{esc(note)}</span></div>'
                       + "".join(_cell(view, question_id, label, run) for label in view.labels) + '</div>\n')
        out.append('  </div>\n  </div>\n')
    else:
        out.append('  <p class="muted">No answers have been recorded for this question yet.</p>\n')
    out.append('</main>\n<aside class="side">\n'
               '  <div class="side-block"><h2>The exact prompt</h2>\n'
               f'    <pre class="prompt mono">{esc(question["prompt"])}</pre>\n'
               '    <span class="muted small">Sent on its own. The model is given no date, no other answers and no earlier runs.</span></div>\n'
               f'  <div class="side-block"><h2>What moved</h2>\n    <div class="moved">{_moved(view, question_id)}</div>\n'
               '    <span class="muted smaller">This list is generated from the comparison. No model wrote it.</span></div>\n'
               '  <div class="panel"><strong class="panel-title">Source of each answer</strong>\n'
               '    <span><strong>server call</strong> means this server called the model and holds the receipt.</span>\n'
               '    <span><strong>self-declared</strong> means an outside client sent the answer and named its own model. That name is not checked.</span>\n'
               + ('    <span><strong>scripted demo</strong> means the answer and its date were written by hand as a sample. No model was called.</span>\n'
                  if view.demo else '')
               + '  </div>\n</aside>\n</div>\n')
    honesty = ("Sample answers on this page are scripted, not model output" if view.demo
               else "Answers are model output and may be wrong")
    out.append(f'<footer class="foot foot-line"><a class="back" href="../index.html">All {esc(count_word(len(view.questions)))} questions</a>'
               f'<span class="mono small">{honesty} · no payments</span></footer>\n')
    out.append('</div>\n</body>\n</html>\n')
    return "".join(out)


def render_site(payload: dict) -> dict[str, str]:
    """Every archive page, keyed by its path under `archive/`."""
    versions = asset_versions()
    pages = {"index.html": render_index(payload, versions)}
    for question in payload["questions"]:
        pages[f"q/{question['id']}.html"] = render_question(payload, question["id"], versions)
    return pages
