"""Static HTML for Answers Over Time: the question index, the trending list, one page per
question of either track, and one portrait share card per question.

Pure functions from the public payload (archive.public_payload, or a reviewed
published/archive.json) to HTML text. No database, no model, no JavaScript, no inline style
or script: the pages work under the site's Content-Security-Policy and with scripts off.
Every piece of text that reaches the page goes through `esc`; model output is untrusted.
All links are relative, so the same pages work in a static snapshot under any sub-path and
in both web apps (`/archive/index.html`, `/archive/trending.html`, `/archive/q/Q01.html`,
`/archive/t/T001.html`, `/archive/card/Q01.html`).

A trending question is added after the archive started, so it has cells only in the runs that
asked it (`runs[].asked` in the payload): its strip and its table start at its first run.
"""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

from . import archive
from . import web  # CONTENT_SECURITY_POLICY is read at call time (web imports this module for its routes)

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = ("archive.css", "archive-mark.svg", "archive-icon.svg")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty", 25: "twenty-five", 30: "thirty",
                40: "forty"}
SOURCE_LABELS = {"engine": "server call", "api": "self-declared", "demo": "scripted demo"}
MAX_COLUMNS = archive.MAX_ANSWERERS + 10   # layout classes exist up to this many answer columns
FOLDER = {"core": "q", "trending": "t"}
CARD_MODELS = 3


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
        self.trend = payload["trending"]
        self.trending = self.trend["questions"]
        self.by_id = {q["id"]: q for q in self.questions + self.trending}
        self.place = ({q["id"]: ("core", n) for n, q in enumerate(self.questions, 1)}
                      | {q["id"]: ("trending", n) for n, q in enumerate(self.trending, 1)})
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

    def asked(self, question_id: str) -> list[dict]:
        """The runs that asked this question, oldest first."""
        track, position = self.place[question_id]
        return [run for run in self.runs if run["asked"][track] >= position]

    def series(self, question_id: str, label: str) -> list[tuple[dict, dict | None]]:
        return [(run, self.cell.get((question_id, label, run["number"]))) for run in self.asked(question_id)]

    def page(self, question_id: str) -> str:
        return f"{FOLDER[self.place[question_id][0]]}/{question_id}.html"

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
            f'  <link rel="icon" type="image/svg+xml" href="{up}static/archive-icon.svg?v={versions["archive-icon.svg"]}" />\n'
            f'  <link rel="stylesheet" href="{up}static/archive.css?v={versions["archive.css"]}" />\n'
            '</head>\n<body>\n')


def _bar(up: str, base: str, versions: dict[str, str], current: str = "") -> str:
    """`base` leads to the archive folder ("" on its own pages, "../" one folder down)."""
    index_href = f"{base}index.html"
    here = {name: ' aria-current="page"' if name == current else "" for name in ("questions", "trending")}
    return ('<div class="bar"><div class="bar-in">\n'
            f'  <a class="wordmark" href="{index_href}"><img class="mark" src="{up}static/archive-mark.svg?v={versions["archive-mark.svg"]}" '
            'alt="" width="57" height="24" />Answers Over Time</a>\n'
            '  <nav aria-label="Archive navigation">\n'
            f'    <a href="{index_href}"{here["questions"]}>Questions</a>\n'
            f'    <a href="{base}trending.html"{here["trending"]}>Trending</a>\n'
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
    summary = (f"{label}: {_plural(changes, 'change')} across {_plural(len(squares), 'run')}"
               + (f", {missing} without an answer" if missing else ""))
    return (f'<div class="strip-row"><span class="strip-name mono">{esc(label)}</span>'
            f'<div class="strip" role="img" aria-label="{esc(summary)}">{"".join(squares)}</div></div>'), changes, missing


def _last_change(view: _View, question_id: str) -> str:
    if not any(a["question"] == question_id for a in view.answers):
        return "No answers recorded yet"
    changed = [a for a in view.answers if a["question"] == question_id and _kind(a) == "changed"]
    if not changed:
        return f"No change in any model across {_plural(len(view.asked(question_id)), 'run')}"
    last = max(changed, key=lambda a: a["run"])
    return f"Last change: {last['answerer']}, {month_year(view.run_date[last['run']])}"


def render_index(payload: dict, versions: dict[str, str] | None = None) -> str:
    view = _View(payload)
    versions = versions or asset_versions()
    total = count_word(len(view.questions))
    out = [_head("Answers Over Time", "../", versions,
                 "The same simple questions, asked of AI models again every month, with every answer kept."),
           _bar("../", "", versions, "questions"), '<div class="wrap">\n']
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
    if view.trending:
        newest = view.trending[-1]
        trend_line = (f'<a href="trending.html">See the {esc(count_word(len(view.trending)))} trending '
                      f'question{"" if len(view.trending) == 1 else "s"}</a>'
                      f'<span class="muted small">Newest, added {esc(month_year(newest["added"]))}: {esc(newest["text"])}</span>')
    else:
        trend_line = '<a href="trending.html">Trending questions</a><span class="muted small">None has been added yet.</span>'
    out.append('<section class="group trend-link">\n  <div class="group-head"><h2>Trending</h2>'
               '<span class="muted">Questions written from what many people were searching for. New ones are added over time.</span></div>\n'
               f'  <p class="trend-line">{trend_line}</p>\n</section>\n')
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
    for run in reversed(view.asked(question_id)):
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


def _honesty(view: _View) -> str:
    return ("Sample answers on this page are scripted, not model output" if view.demo
            else "Answers are model output and may be wrong")


def render_question(payload: dict, question_id: str, versions: dict[str, str] | None = None) -> str:
    """One question of either track: `q/Q01.html` or `t/T001.html` (same layout; a trending
    question also shows its topic and where the topic came from)."""
    view = _View(payload)
    versions = versions or asset_versions()
    question = view.by_id.get(question_id)
    if question is None:
        raise KeyError(question_id)
    trending = view.place[question_id][0] == "trending"
    runs = view.asked(question_id)
    columns = min(max(len(view.labels), 1), MAX_COLUMNS)
    out = [_head(f"{question['id']} · Answers Over Time", "../../", versions, question["text"]),
           _bar("../../", "../", versions), '<div class="wrap">\n']
    if trending:
        chips = (f'<span class="chip chip-topic">{esc(question["topic"])}</span>'
                 f'<span class="muted small">added {esc(month_year(question["added"]))}</span>')
        if not view.trend["locked"] and not view.demo:
            chips += '<span class="chip">Draft question</span>'
        source = (f'  <p class="muted small">Topic taken from: {esc(question["trend_source"])}. Added {esc(full_date(question["added"]))}; '
                  'asked in every run since, in the same words.</p>\n')
    else:
        chips, source = f'<span class="chip">{esc(question["group"])}</span>', ""
    out.append('<header class="q-hero">\n  <div class="chips">'
               f'<span class="q-id mono">{esc(question["id"])}</span>{chips}'
               f'<span class="tag tag-dark mono">{esc(view.tag())}</span>'
               f'<a class="share" href="../card/{esc(question["id"])}.html">Share card</a></div>\n'
               f'  <h1 class="display display-q">{esc(question["text"])}</h1>\n'
               f'  <p class="lede">{esc(question["rationale"])}</p>\n{source}</header>\n')
    out.append('<div class="split">\n<main class="answers">\n'
               '  <div class="group-head spread"><h2>Every answer, newest first</h2>'
               '<span class="muted small">Dark cells differ from that model\'s answer before</span></div>\n')
    if runs and view.labels:
        out.append(f'  <div class="grid-scroll" tabindex="0" role="region" aria-label="Answers by date and model">\n'
                   f'  <div class="grid n-{columns}" role="table" aria-label="Answers to {esc(question["id"])}, newest first">\n'
                   '    <div class="grid-row grid-head mono" role="row"><span role="columnheader">DATE</span>'
                   + "".join(f'<span role="columnheader">{esc(label.upper())}</span>' for label in view.labels) + '</div>\n')
        for run in reversed(runs):
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
    back = ('<a class="back" href="../trending.html">All trending questions</a>' if trending
            else f'<a class="back" href="../index.html">All {esc(count_word(len(view.questions)))} questions</a>')
    out.append(f'<footer class="foot foot-line">{back}'
               f'<span class="mono small">{_honesty(view)} · no payments</span></footer>\n')
    out.append('</div>\n</body>\n</html>\n')
    return "".join(out)


def render_trending(payload: dict, versions: dict[str, str] | None = None) -> str:
    """`trending.html`: the trending questions, newest first, each with its topic, the month it
    was added and the same strips as the index."""
    view = _View(payload)
    versions = versions or asset_versions()
    out = [_head("Trending · Answers Over Time", "../", versions,
                 "Questions written from what many people were searching for, asked of AI models in every run since."),
           _bar("../", "", versions, "trending"), '<div class="wrap">\n']
    out.append('<header class="hero hero-small">\n  <div class="hero-copy">\n'
               f'    <span class="tag tag-dark mono">{esc(view.tag())}</span>\n'
               '    <h1 class="display">Questions from what people are searching for.</h1>\n'
               '    <p class="lede">Now and then a question is written from a topic many people were looking up that day. '
               'From then on it is asked in every run, in the same words, so you can see how the answers move.</p>\n'
               '  </div>\n  <div class="legend">\n'
               '    <strong class="legend-title">How to read a strip</strong>\n'
               '    <span class="legend-note">One square per run, oldest on the left. A strip starts at the first run after the question was added.</span>\n'
               '    <div class="legend-row"><span class="sq sq-same"></span><span>Same answer as the run before, or the first answer</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-changed"></span><span>The answer changed</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-none"></span><span>No answer recorded</span></div>\n'
               '  </div>\n</header>\n')
    out.append('<section class="group">\n  <div class="group-head"><h2>Trending</h2>'
               '<span class="muted">Newest first.</span></div>\n')
    ordered = sorted(view.trending, key=lambda q: (q["added"], view.place[q["id"]][1]), reverse=True)
    for question in ordered:
        strips = "".join(_strip(view, question["id"], label)[0] for label in view.labels) if view.asked(question["id"]) else ""
        if not strips:
            strips = '<span class="muted small">Not asked in any run yet.</span>'
        out.append('  <article class="q-row">\n    <div class="q-main">'
                   f'<span class="q-id mono">{esc(question["id"])}</span>'
                   f'<div class="q-text"><a href="t/{esc(question["id"])}.html">{esc(question["text"])}</a>'
                   f'<div class="q-meta"><span class="chip chip-topic">{esc(question["topic"])}</span>'
                   f'<span class="muted small">added {esc(month_year(question["added"]))}</span>'
                   f'<span class="muted small">{esc(_last_change(view, question["id"]))}</span></div></div></div>\n'
                   f'    <div class="q-strips">{strips}</div>\n  </article>\n')
    if not ordered:
        out.append('  <p class="muted trend-none">No trending question has been added yet.</p>\n')
    out.append('</section>\n')
    if view.demo:
        note = "These sample questions and their dates were written by hand for the demo. They are not taken from a real list of trends."
    elif view.trend["locked"]:
        note = ("A trending question never changes and is never removed once it is added. New ones are added over time, "
                "by hand, from a public list of popular searches.")
    else:
        note = ("These questions are drafts. Once the owner adds one for good it never changes and is never removed; "
                "new ones are added over time.")
    out.append(f'<p class="list-note muted">{note} Questions about a private person, about elections, or asking for '
               'medical, legal or money advice are not added.</p>\n')
    out.append(f'<footer class="foot"><span>Part of LIVE Genesis. {_honesty(view)}.</span>'
               '<span class="mono small">No payments · nothing can be changed from this page</span></footer>\n')
    out.append('</div>\n</body>\n</html>\n')
    return "".join(out)


def _card_models(view: _View, question_id: str) -> tuple[list[dict], dict | None]:
    """Up to CARD_MODELS answerers for the card, each with its latest answer; and the one of
    them whose answer changed most recently (with the answer it gave before), if any did."""
    found = []
    for order, label in enumerate(view.labels):
        series = [a for a in view.by_series.get((question_id, label), ()) if a["state"] == "answered"]
        if not series:
            continue
        at = max((n for n, answer in enumerate(series) if answer["changed"]), default=0)
        found.append({"order": order, "label": label, "latest": series[-1], "before": series[at - 1] if at else None,
                      "changed_in": series[at]["run"] if at else 0})
    chosen = sorted(found, key=lambda m: (-m["changed_in"], m["order"]))[:CARD_MODELS]
    moved = chosen[0] if chosen and chosen[0]["before"] else None
    return sorted(chosen, key=lambda m: m["order"]), moved


def render_card(payload: dict, question_id: str, versions: dict[str, str] | None = None) -> str:
    """`card/<ID>.html`: a 9:16 portrait card for a screenshot or a screen recording (540 by 960
    CSS pixels; it scales to any window of that shape). The question, the latest answer of up to
    three models and, for one that changed, the answer before. No script, no animation."""
    view = _View(payload)
    versions = versions or asset_versions()
    question = view.by_id.get(question_id)
    if question is None:
        raise KeyError(question_id)
    trending = view.place[question_id][0] == "trending"
    models, moved = _card_models(view, question_id)
    length = len(question["text"])
    size = "xl" if length <= 26 else "l" if length <= 48 else "m" if length <= 90 else "s"
    kicker = question["topic"] if trending else question["group"]
    out = [_head(f"{question['id']} card · Answers Over Time", "../../", versions, question["text"]).replace(
               "<body>", '<body class="card-page">'),
           '<main class="card"><div class="card-in">\n'
           f'  <header class="card-top"><a class="card-brand" href="../{esc(view.page(question_id))}">'
           f'<img class="card-mark" src="../../static/archive-mark.svg?v={versions["archive-mark.svg"]}" alt="" width="57" height="24" />'
           'Answers Over Time</a>'
           f'<span class="card-kicker">{esc(kicker)}</span></header>\n'
           f'  <h1 class="card-q card-q-{size}">{esc(question["text"])}</h1>\n'
           f'  <div class="card-panels n-{max(len(models), 1)}">\n']
    for model in models:
        latest = model["latest"]
        meta = (f'<div class="card-meta mono"><span class="card-label">{esc(model["label"])}</span>'
                f'<span class="card-when">{esc(month_year(view.run_date[latest["run"]]))} · {esc(SOURCE_LABELS[latest["source"]])}</span></div>')
        if model is moved:
            before = model["before"]
            out.append(f'    <section class="card-panel card-panel-changed">{meta}'
                       f'<p class="card-answer">{esc(latest["text"])}</p>'
                       f'<p class="card-before"><span class="mono">Before ({esc(month_year(view.run_date[before["run"]]))})</span> '
                       f'{esc(before["text"])}</p></section>\n')
        else:
            out.append(f'    <section class="card-panel">{meta}<p class="card-answer">{esc(latest["text"])}</p></section>\n')
    if not models:
        out.append('    <section class="card-panel"><p class="card-answer card-empty">No answers recorded yet.</p></section>\n')
    honesty = view.tag() if view.demo or view.empty else "Answers are model output and may be wrong"
    out.append('  </div>\n'
               f'  <footer class="card-foot mono"><span>{esc(honesty)}</span>'
               f'<span>{esc(question["id"])} · same question every run · read-only · no payments</span></footer>\n'
               '</div></main>\n</body>\n</html>\n')
    return "".join(out)


def render_site(payload: dict) -> dict[str, str]:
    """Every archive page, keyed by its path under `archive/`."""
    versions = asset_versions()
    pages = {"index.html": render_index(payload, versions), "trending.html": render_trending(payload, versions)}
    for folder, questions in (("q", payload["questions"]), ("t", payload["trending"]["questions"])):
        for question in questions:
            pages[f"{folder}/{question['id']}.html"] = render_question(payload, question["id"], versions)
            pages[f"card/{question['id']}.html"] = render_card(payload, question["id"], versions)
    return pages
