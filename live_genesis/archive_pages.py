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

Standalone: `render_site(payload, standalone=True)` is the archive published on its own (snapshot.py
`--archive-only`). Its pages carry no link to the observatory, which such a site does not hold, and
`render_home` is the site's root page. `sibling` (standalone only) is the relative address of the
other published snapshot as seen from the site root (`demo/` or `../`); the bar links its archive.

Chat apps (the optional `chatbots` block of the payload; archive_chatbots.py): replies a person collected
by hand. They have their own page (`chatbots.html`), a second grid on the page of each question that was
asked there, and their own share cards (`card/<ID>-chatbots.html` with up to four apps, and
`card/<ID>-<app label>.html` with one). They are never mixed into the model grid or a model's strip.
Every such cell says "entered by hand" (or "scripted demo", under made-up app names), and a date on which
an app's recorded conditions differ from its date before is marked. App names are plain text, never a
logo, and never the largest thing on a page or card: the question is.

Repeat samples: a run may ask each question several times (`runs[].samples`). A cell then shows the
representative answer with "2 of 3 agree", and a third state, UNSETTLED (archive.stability), next to
same and changed. Nothing of this is rendered for an archive whose runs all asked once.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

from . import archive
from . import web  # CONTENT_SECURITY_POLICY is read at call time (web imports this module for its routes)

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = ("archive.css", "archive-mark.svg", "archive-icon.svg")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty", 25: "twenty-five", 30: "thirty",
                40: "forty"}
SOURCE_LABELS = {"engine": "server call", "api": "self-declared", "demo": "scripted demo", "manual": "entered by hand"}
CHATBOTS_SUFFIX = "chatbots"   # card/<ID>-chatbots.html: the four-up card; card/<ID>-<app label>.html: one app
CARD_APPS = 4
LONG_REPLY = 420   # characters; a longer chat-app reply is shown whole in a box that scrolls
CARD_NAME = re.compile(r"(Q[0-9]{2}|T[0-9]{3})-([a-z][a-z0-9-]{1,23})")
CHAT_CARD_FOOT = "AI-generated · typed in by hand · not affiliated · may be wrong"
# Why a model has no answers in a run it was dropped from (archive.SKIP_REASONS), in plain words.
SKIP_WORDS = {"rate_limited": "the service limited the number of requests", "not_found": "the service did not know this model",
              "auth": "the service refused the key", "timeout": "the call timed out", "server_error": "the service reported an error",
              "no_usage": "the service sent no token counts", "not_ready": "the local setup was not ready",
              "other": "the call failed"}
NATIVE = ("openai", "anthropic", "openai-compatible")   # not a named endpoint: nothing to add to the label
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


class _Chat:
    """Lookups over the chat-app track of one payload (empty when the payload has none). Time is told in
    DATES here: one row or square per day on which the owner asked, whatever import carried it."""

    def __init__(self, payload: dict):
        block = payload.get("chatbots") or {"apps": [], "questions": [], "runs": [], "answers": []}
        self.apps = block["apps"]
        self.labels = [app["label"] for app in self.apps]
        self.name = {app["label"]: app["name"] for app in self.apps}
        self.maker = {app["label"]: app["maker"] for app in self.apps}
        self.attribution = {app["label"]: app.get("attribution", "") for app in self.apps}
        self.questions = list(block["questions"])
        runs = sorted(block["runs"], key=lambda r: (r["collected_on"], r["number"]))
        self.demo = any(run["demo"] for run in runs)
        self.dates = sorted({run["collected_on"] for run in runs})
        self.day = {run["number"]: run["collected_on"] for run in runs}
        self.conditions: dict[tuple[str, str], dict] = {}
        self.asked: dict[tuple[str, str], list] = {}
        for run in runs:
            for label, conditions in run["conditions"].items():
                self.conditions[(label, run["collected_on"])] = conditions
                self.asked[(label, run["collected_on"])] = run["questions"]
        self.answers = [a for a in block["answers"] if a["source"] in ("manual", "demo")]
        self.cell = {(a["question"], a["chatbot"], self.day[a["run"]]): a for a in self.answers}

    def series(self, question_id: str, label: str) -> list[tuple[str, dict | None]]:
        """The dates on which this app was asked this question, oldest first, each with its answer row."""
        return [(date, self.cell.get((question_id, label, date))) for date in self.dates
                if question_id in self.asked.get((label, date), ())]

    def dates_of(self, question_id: str) -> list[str]:
        return [date for date in self.dates if any(question_id in self.asked.get((label, date), ()) for label in self.labels)]

    def latest(self, question_id: str, label: str) -> tuple[str, dict] | None:
        """(date, answer) of this app's latest ANSWERED reply to the question, or None."""
        answered = [(date, a) for date, a in self.series(question_id, label) if a is not None and a["state"] == "answered"]
        return answered[-1] if answered else None

    @staticmethod
    def kind(answer: dict | None) -> str:
        if answer is None or answer["state"] != "answered":
            return "none"
        return "changed" if answer["changed"] else "same"

    def before(self, label: str, date: str) -> dict | None:
        """This app's conditions on its previous date, or None for its first."""
        earlier = [d for d in self.dates if d < date and (label, d) in self.conditions]
        return self.conditions[(label, earlier[-1])] if earlier else None

    def conditions_changed(self, label: str, date: str) -> list[str]:
        """What differs between this app's recorded conditions on `date` and on its date before, in words
        ("search: off → on"). Empty for its first date and when nothing differs. The model name the app
        showed is not a condition the owner chose and is reported by `shown_change`."""
        now, old = self.conditions.get((label, date)), self.before(label, date)
        if not now or not old:
            return []
        return [f"{CONDITION_NAMES[key]}: {_condition_value(key, old[key])} → {_condition_value(key, now[key])}"
                for key in CONDITION_NAMES if old[key] != now[key]]

    def shown_change(self, label: str, date: str) -> bool:
        now, old = self.conditions.get((label, date)), self.before(label, date)
        return bool(now and old and old["shown_model"] != now["shown_model"])


CONDITION_NAMES = {"account": "account", "temporary_chat": "temporary chat", "memory_off": "memory", "web_search": "search",
                   "country": "country", "app": "app"}
ACCOUNT_WORDS = {"logged_out": "logged out", "free_account": "free account", "paid_account": "paid account"}
APP_WORDS = {"web": "web", "mobile": "mobile app", "desktop": "desktop app"}


def _condition_value(key: str, value: object) -> str:
    if key == "account":
        return ACCOUNT_WORDS.get(str(value), str(value))
    if key == "temporary_chat":
        return "yes" if value else "no"
    if key == "memory_off":
        return "not applicable" if value == "not_applicable" else "off" if value else "on"
    if key == "web_search":
        return "not known" if value == "unknown" else str(value)
    if key == "app":
        return APP_WORDS.get(str(value), str(value))
    return str(value)


def conditions_line(conditions: dict) -> str:
    """"free account · temporary chat · memory off · search on · US · web": what the owner recorded."""
    memory = conditions["memory_off"]
    parts = [ACCOUNT_WORDS.get(conditions["account"], str(conditions["account"])),
             "temporary chat" if conditions["temporary_chat"] else "regular chat"]
    if memory != "not_applicable":
        parts.append("memory off" if memory else "memory on")
    parts += [f"search {_condition_value('web_search', conditions['web_search'])}", str(conditions["country"]),
              APP_WORDS.get(conditions["app"], str(conditions["app"]))]
    return " · ".join(parts)


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
        self.chat = _Chat(payload)
        self.demo = any(a["source"] == "demo" for a in self.answers) or any(r["demo"] for r in self.runs) or self.chat.demo
        self.empty = not self.answers and not self.chat.answers
        # Repeat samples: how many each run asked, whether any run asked several, and per answered cell its
        # derived label (archive.stability) against that answerer's previous answered cell.
        self.run_samples = {r["number"]: r.get("samples", 1) for r in self.runs}
        self.multi = any(count > 1 for count in self.run_samples.values())
        self.label: dict[tuple[str, str, int], str] = {}
        for (question_id, label), series in self.by_series.items():
            previous = None
            for answer in series:
                if answer["state"] == "answered":
                    self.label[(question_id, label, answer["run"])] = archive.stability(answer, previous)
                    previous = answer

    def kind(self, answer: dict | None) -> str:
        """none / same / changed / unsettled: the state of one cell, also its CSS class suffix."""
        if answer is None or answer["state"] != "answered":
            return "none"
        label = self.label[(answer["question"], answer["answerer"], answer["run"])]
        return "same" if label == "steady" else label

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

    def model_change(self, question_id: str, label: str, run_number: int) -> tuple[str, str, str, str] | None:
        """(short tag, what changed, before, now) when the model behind this answer is not the one behind
        this answerer's previous recorded answer: a different model ID was requested, the service reported
        a different model name, or the local model file has a different digest. A value is compared only
        when it was recorded on both sides."""
        now = self.cell.get((question_id, label, run_number))
        earlier = [a for a in self.by_series.get((question_id, label), ()) if a["run"] < run_number]
        if now is None or not earlier:
            return None
        before = earlier[-1]
        if before["model"] != now["model"]:
            return "new model ID", "model ID", before["model"], now["model"]
        for key, tag, what in (("reported_model", "new model ID", "model ID reported by the service"),
                               ("model_digest", "new model file", "local model file (digest)")):
            old, new = before.get(key), now.get(key)
            if old and new and old != new:
                return tag, what, old, new
        return None


def _agreement(view: _View, answer: dict) -> str:
    """"3 of 3 agree" for a cell of a run that asked several samples; "" for any other cell."""
    samples = answer.get("samples")
    if samples is None:
        return ""
    asked = view.run_samples[answer["run"]]
    if not samples:
        return "no sample gave an answer"
    return (f"{answer['agree']} of {samples} agree"
            + (f" · only {samples} of {asked} samples answered" if samples < asked else ""))


def _legend(view: _View, note: str) -> str:
    """The "how to read a strip" box of the index and the trending page. The unsettled row exists only
    when some run asked several samples."""
    unsettled = ('    <div class="legend-row"><span class="sq sq-unsettled"></span><span>Unsettled: asked several times and the '
                 'answers did not all agree, in that run or the one before</span></div>\n' if view.multi else '')
    return ('  <div class="legend">\n'
            '    <strong class="legend-title">How to read a strip</strong>\n'
            f'    <span class="legend-note">{note}</span>\n'
            '    <div class="legend-row"><span class="sq sq-same"></span><span>Same answer as the run before, or the first answer</span></div>\n'
            '    <div class="legend-row"><span class="sq sq-changed"></span><span>The answer changed</span></div>\n'
            + unsettled +
            '    <div class="legend-row"><span class="sq sq-none"></span><span>No answer recorded</span></div>\n'
            '  </div>\n</header>\n')


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


def _bar(up: str, base: str, versions: dict[str, str], current: str = "", *, standalone: bool = False,
         sibling: str | None = None, demo: bool = False) -> str:
    """`base` leads to the archive folder ("" on its own pages, "../" one folder down). The last link
    goes to the observatory; a standalone archive has none, so it is left out there (or, when the build
    names the other published snapshot, replaced by a link to that snapshot's archive)."""
    index_href = f"{base}index.html"
    if not standalone:
        last = f'    <a href="{up}">Observatory</a>\n'
    elif sibling:
        # A scripted page never calls the other snapshot (or itself) "recorded".
        last = (f'    <a href="{esc(up + sibling)}archive/index.html">'
                f'{"Main archive" if demo else "Scripted demo"}</a>\n')
    else:
        last = ""
    here = {name: ' aria-current="page"' if name == current else "" for name in ("questions", "trending", "chatbots")}
    return ('<div class="bar"><div class="bar-in">\n'
            f'  <a class="wordmark" href="{index_href}"><img class="mark" src="{up}static/archive-mark.svg?v={versions["archive-mark.svg"]}" '
            'alt="" width="57" height="24" />Answers Over Time</a>\n'
            '  <nav aria-label="Archive navigation">\n'
            f'    <a href="{index_href}"{here["questions"]}>Questions</a>\n'
            f'    <a href="{base}trending.html"{here["trending"]}>Trending</a>\n'
            f'    <a href="{base}chatbots.html"{here["chatbots"]}>Chatbots</a>\n'
            f'    <a href="{index_href}#method">Method</a>\n'
            + last +
            '  </nav>\n'
            '  <span class="mono small muted">READ-ONLY ARCHIVE</span>\n'
            '</div></div>\n')


def _strip(view: _View, question_id: str, label: str) -> tuple[str, int, int]:
    squares, changes, missing, unsettled = [], 0, 0, 0
    for _run, answer in view.series(question_id, label):
        kind = view.kind(answer)
        changes += kind == "changed"
        missing += kind == "none"
        unsettled += kind == "unsettled"
        squares.append(f'<span class="sq sq-{kind}"></span>')
    summary = (f"{label}: {_plural(changes, 'change')} across {_plural(len(squares), 'run')}"
               + (f", {unsettled} unsettled" if unsettled else "") + (f", {missing} without an answer" if missing else ""))
    return (f'<div class="strip-row"><span class="strip-name mono">{esc(label)}</span>'
            f'<div class="strip" role="img" aria-label="{esc(summary)}">{"".join(squares)}</div></div>'), changes, missing


def _last_change(view: _View, question_id: str) -> str:
    if not any(a["question"] == question_id for a in view.answers):
        return "No answers recorded yet"
    changed = [a for a in view.answers if a["question"] == question_id and view.kind(a) == "changed"]
    if not changed:
        return f"No change in any model across {_plural(len(view.asked(question_id)), 'run')}"
    last = max(changed, key=lambda a: a["run"])
    return f"Last change: {last['answerer']}, {month_year(view.run_date[last['run']])}"


HOME_DESCRIPTION = "The same simple questions, asked of AI models again every month, with every answer kept."


def render_home(versions: dict[str, str] | None = None) -> str:
    """`index.html` at the root of a standalone archive: sends the visitor to `archive/index.html`
    with a meta refresh and shows a plain link for a browser that does not follow it. No script."""
    versions = versions or asset_versions()
    head = _head("Answers Over Time", "", versions, HOME_DESCRIPTION)
    title = '  <title>'
    if head.count(title) != 1:
        raise ValueError("the page head must hold exactly one <title>")
    head = head.replace(title, '  <meta http-equiv="refresh" content="0; url=archive/index.html" />\n' + title)
    return (head + '<div class="wrap">\n<header class="hero hero-small">\n  <div class="hero-copy">\n'
            '    <span class="tag tag-dark mono">Read-only archive · static snapshot</span>\n'
            '    <h1 class="display">Answers Over Time</h1>\n'
            '    <p class="lede"><a href="archive/index.html">Open the archive</a></p>\n'
            '  </div>\n</header>\n'
            '<footer class="foot"><span>Nothing is running behind this page.</span>'
            '<span class="mono small">No payments · nothing can be changed from this page</span></footer>\n'
            '</div>\n</body>\n</html>\n')


def render_index(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                 sibling: str | None = None) -> str:
    view = _View(payload)
    versions = versions or asset_versions()
    total = count_word(len(view.questions))
    out = [_head("Answers Over Time", "../", versions, HOME_DESCRIPTION),
           _bar("../", "", versions, "questions", standalone=standalone, sibling=sibling, demo=view.demo),
           '<div class="wrap">\n']
    out.append('<header class="hero">\n  <div class="hero-copy">\n'
               f'    <span class="tag tag-dark mono">{esc(view.tag())}</span>\n'
               '    <h1 class="display">Simple questions, asked again every month.</h1>\n'
               '    <p class="lede">People ask AI the same basic things year after year. This archive keeps every answer '
               'with its date and the model that gave it, so you can see when an answer changes.</p>\n'
               '  </div>\n' + _legend(view, 'One square per run, oldest on the left.'))
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
               + ('    <div><strong>Asked more than once</strong><span>In a run with several samples each question is asked several times, '
                  'each time on its own; the answer shown is the one most samples gave, and a cell is marked unsettled, not changed, '
                  'when the samples did not all agree in that run or the one before.</span></div>\n' if view.multi else '')
               + '  </div>\n</section>\n')
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
            change = view.model_change(question_id, label, run["number"])
            when = month_year(run["date"])
            kind = view.kind(answer)
            if kind == "changed":
                text = f"{label}'s answer changed."
                if change:
                    text += f" Its {change[1]} changed from {change[2]} to {change[3]}."
                items.append((when, text))
            elif kind == "unsettled":
                if archive.split(answer):
                    text = (f"{label}'s {_plural(answer['samples'], 'sample')} did not agree: {answer['agree']} gave the answer shown. "
                            "Not counted as a change.")
                elif answer["changed"]:
                    text = (f"{label}'s answer differs from the run before, where its samples did not agree. "
                            "Not counted as a change.")
                else:
                    text = ""
                if change:
                    text = (text + " " if text else f"{label}: ") + f"Its {change[1]} changed from {change[2]} to {change[3]}."
                if text:
                    items.append((when, text))
            elif change:
                items.append((when, f"{label}'s {change[1]} changed from {change[2]} to {change[3]}; "
                                    + ("the answer stayed the same." if kind == "same" else "no answer was recorded.")))
    for label in view.labels:
        series = [a for _run, a in view.series(question_id, label) if a is not None and a["state"] == "answered"]
        if series and not any(a["changed"] or view.kind(a) == "unsettled" for a in series):
            items.append(("—", f"{label} gave the same answer in every run recorded."))
    if not items:
        return '<p class="muted small">Nothing has been recorded for this question yet.</p>'
    return "".join(f'<div class="moved-row"><span class="moved-when mono">{esc(when)}</span><span>{esc(text)}</span></div>'
                   for when, text in items)


def _cell(view: _View, question_id: str, label: str, run: dict) -> str:
    answer = view.cell.get((question_id, label, run["number"]))
    if answer is None:
        why = SKIP_WORDS.get(run.get("skipped", {}).get(label, ""))
        return ('<div class="cell cell-none" role="cell">No answer recorded in this run'
                + (f': this model was skipped after a failed call ({esc(why)})' if why else '') + '</div>')
    change = view.model_change(question_id, label, run["number"])
    agreement = _agreement(view, answer)
    meta = (f"{answer['model']} · {_where(answer)}{SOURCE_LABELS[answer['source']]}" + (f" · {change[0]}" if change else "")
            + (f" · {agreement}" if agreement else ""))
    if answer["state"] == "withheld":
        return (f'<div class="cell cell-none" role="cell"><span>Withheld by content check ({esc(answer["reason"])})</span>'
                f'<span class="cell-meta mono">{esc(meta)}</span></div>')
    if answer["state"] != "answered":
        return (f'<div class="cell cell-none" role="cell"><span>No answer recorded: {esc(answer["reason"] or "none given")}</span>'
                f'<span class="cell-meta mono">{esc(meta)}</span></div>')
    kind = view.kind(answer)
    if kind == "unsettled":
        # The other distinct answers of this run's samples, most frequent first; none when only the run before disagreed.
        others = "".join(f'<span role="listitem"><span class="mono">{esc(other["count"])} of {esc(answer["samples"])}</span> '
                         f'{esc(other["text"])}</span>'
                         for other in answer.get("others", ()))
        if not archive.split(answer):
            meta += " · the run before did not agree"
        return ('<div class="cell cell-unsettled" role="cell"><span class="unsettled-tag mono">UNSETTLED</span>'
                f'<span class="cell-text">{esc(answer["text"])}</span>'
                + (f'<div class="cell-others" role="list" aria-label="Other answers in this run">{others}</div>' if others else '')
                + f'<span class="cell-meta mono">{esc(meta)}</span></div>')
    if kind == "changed":
        return ('<div class="cell cell-changed" role="cell"><span class="changed-tag mono">CHANGED</span>'
                f'<span class="cell-text">{esc(answer["text"])}</span><span class="cell-meta mono">{esc(meta)}</span></div>')
    return (f'<div class="cell cell-same" role="cell"><span class="cell-text">{esc(answer["text"])}</span>'
            f'<span class="cell-meta mono">{esc(meta)}</span></div>')


def _where(answer: dict) -> str:
    """Where a server call's model ran, as far as the record says: on the operator's own machine, or
    through a named endpoint. Says nothing about who made the model or how open it is."""
    if answer["source"] != "engine":
        return ""
    if answer.get("local"):
        return "run locally · "
    endpoint = answer.get("endpoint")
    return f"via {endpoint} · " if endpoint and endpoint not in NATIVE else ""


def _honesty(view: _View) -> str:
    return ("Sample answers on this page are scripted, not model output" if view.demo
            else "Answers are model output and may be wrong")


def render_question(payload: dict, question_id: str, versions: dict[str, str] | None = None, *,
                    standalone: bool = False, sibling: str | None = None) -> str:
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
           _bar("../../", "../", versions, standalone=standalone, sibling=sibling, demo=view.demo),
           '<div class="wrap">\n']
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
               '<span class="muted small">Dark cells differ from that model\'s answer before'
               + (' · outlined cells: the samples did not agree' if view.multi else '') + '</span></div>\n')
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
    chat = view.chat
    in_chat = question_id in chat.questions and bool(chat.dates_of(question_id))
    if in_chat:
        out.append(_chat_grid(view, question_id))
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
                  if view.demo else
                  '    <span><strong>run locally</strong> or <strong>via</strong> a name says where the model ran. A model run locally or hosted '
                  'by a third party is not the same as its maker\'s own chat app, and can answer differently.</span>\n')
               + ('    <span><strong>unsettled</strong> means the question was asked several times in that run and the answers did not all '
                  'agree, there or in the run before. The answer shown is the one most samples gave; the difference is not counted as a '
                  'change.</span>\n' if view.multi else '')
               + ('    <span><strong>entered by hand</strong> means a person typed the question into the chat app and pasted its reply. '
                  'There is no receipt for it, so it cannot be checked.'
                  + (' On this sample page those replies are scripted and the app names are made up.' if chat.demo else '')
                  + '</span>\n' if in_chat else '')
               + '  </div>\n</aside>\n</div>\n')
    back = ('<a class="back" href="../trending.html">All trending questions</a>' if trending
            else f'<a class="back" href="../index.html">All {esc(count_word(len(view.questions)))} questions</a>')
    out.append(f'<footer class="foot foot-line">{back}'
               f'<span class="mono small">{_honesty(view)} · no payments</span></footer>\n')
    out.append('</div>\n</body>\n</html>\n')
    return "".join(out)


def render_trending(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                    sibling: str | None = None) -> str:
    """`trending.html`: the trending questions, newest first, each with its topic, the month it
    was added and the same strips as the index."""
    view = _View(payload)
    versions = versions or asset_versions()
    out = [_head("Trending · Answers Over Time", "../", versions,
                 "Questions written from what many people were searching for, asked of AI models in every run since."),
           _bar("../", "", versions, "trending", standalone=standalone, sibling=sibling, demo=view.demo),
           '<div class="wrap">\n']
    out.append('<header class="hero hero-small">\n  <div class="hero-copy">\n'
               f'    <span class="tag tag-dark mono">{esc(view.tag())}</span>\n'
               '    <h1 class="display">Questions from what people are searching for.</h1>\n'
               '    <p class="lede">Now and then a question is written from a topic many people were looking up that day. '
               'From then on it is asked in every run, in the same words, so you can see how the answers move.</p>\n'
               '  </div>\n' + _legend(view, 'One square per run, oldest on the left. A strip starts at the first run after '
                                               'the question was added.'))
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
        at = max((n for n, answer in enumerate(series) if view.kind(answer) == "changed"), default=0)
        found.append({"order": order, "label": label, "latest": series[-1], "before": series[at - 1] if at else None,
                      "changed_in": series[at]["run"] if at else 0})
    chosen = sorted(found, key=lambda m: (-m["changed_in"], m["order"]))[:CARD_MODELS]
    moved = chosen[0] if chosen and chosen[0]["before"] else None
    return sorted(chosen, key=lambda m: m["order"]), moved


def render_card(payload: dict, question_id: str, versions: dict[str, str] | None = None) -> str:
    """`card/<ID>.html`: a 9:16 portrait card for a screenshot or a screen recording (540 by 960
    CSS pixels; it scales to any window of that shape). The question, the latest answer of up to
    three models and, for one that changed, the answer before. An answer whose samples did not all
    agree shows the representative and "2 of 3 agree". No script, no animation."""
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
        agreement = f" · {latest['agree']} of {latest['samples']} agree" if archive.split(latest) else ""
        meta = (f'<div class="card-meta mono"><span class="card-label">{esc(model["label"])}</span>'
                f'<span class="card-when">{esc(month_year(view.run_date[latest["run"]]))} · {esc(SOURCE_LABELS[latest["source"]])}'
                f'{esc(agreement)}</span></div>')
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


# ---- Chat apps: replies a person collected by hand ----------------------------------------------------------

def _chat_label(chat: _Chat) -> str:
    return SOURCE_LABELS["demo" if chat.demo else "manual"]


def chat_disclaimer(makers: list[str]) -> str:
    """The notice shown wherever real hand-entered replies are: what they are and that the site is
    independent of the companies whose apps were asked (named from the recorded apps, in their order)."""
    names: list[str] = []
    for maker in makers:
        if maker not in names:
            names.append(maker)
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return ("These answers were generated by AI: each was typed by hand into the named app on the date and under the "
            "conditions shown, and copied unedited. This site is independent and is not affiliated with, sponsored by or "
            f"endorsed by {listed}; product names are their owners' trademarks. The answers may be inaccurate and are not advice.")


def _chat_strip(chat: _Chat, question_id: str, label: str) -> str:
    squares, changes, missing, moved = [], 0, 0, 0
    for date, answer in chat.series(question_id, label):
        kind = chat.kind(answer)
        marked = bool(chat.conditions_changed(label, date))
        changes += kind == "changed"
        missing += kind == "none"
        moved += marked
        squares.append(f'<span class="sq sq-{kind}{" sq-cond" if marked else ""}"></span>')
    name = chat.name[label]
    if not squares:
        return (f'<div class="strip-row"><span class="strip-name mono">{esc(name)}</span>'
                '<span class="muted small">Not asked yet.</span></div>')
    summary = (f"{name}: {_plural(changes, 'change')} across {_plural(len(squares), 'date')}"
               + (f", conditions changed on {_plural(moved, 'date')}" if moved else "")
               + (f", {missing} without a reply" if missing else ""))
    return (f'<div class="strip-row"><span class="strip-name mono">{esc(name)}</span>'
            f'<div class="strip" role="img" aria-label="{esc(summary)}">{"".join(squares)}</div></div>')


def _chat_last_change(chat: _Chat, question_id: str) -> str:
    rows = [a for a in chat.answers if a["question"] == question_id]
    if not rows:
        return "No replies recorded yet"
    changed = [a for a in rows if chat.kind(a) == "changed"]
    if not changed:
        return f"No change in any app across {_plural(len(chat.dates_of(question_id)), 'date')}"
    last = max(changed, key=lambda a: chat.day[a["run"]])
    return f"Last change: {chat.name[last['chatbot']]}, {month_year(chat.day[last['run']])}"


def _chat_cell(chat: _Chat, question_id: str, label: str, date: str) -> str:
    conditions = chat.conditions.get((label, date))
    if conditions is None or question_id not in chat.asked.get((label, date), ()):
        return '<div class="cell cell-chat cell-none" role="cell">Not asked on this date</div>'
    answer = chat.cell.get((question_id, label, date))
    moved = chat.conditions_changed(label, date)
    meta = (f'<span class="cell-meta mono">{esc(conditions["shown_model"])} · {esc(_chat_label(chat))}'
            + (' · new shown model' if chat.shown_change(label, date) else '')
            + (' · screenshot kept' if answer and answer.get("screenshot_hash") else '')
            + f'</span><span class="cell-meta mono">{esc(conditions_line(conditions))}</span>'
            + (f'<span class="cell-meta mono">conditions changed · {esc("; ".join(moved))}</span>' if moved else '')
            + (f'<span class="cell-meta">{esc(conditions["note"])}</span>' if conditions["note"] else ''))
    tag = '<span class="cond-tag mono">CONDITIONS CHANGED</span>' if moved else ''
    if answer is None:
        return f'<div class="cell cell-chat cell-none" role="cell"><span>No reply recorded</span>{meta}</div>'
    if answer["state"] == "withheld":
        return (f'<div class="cell cell-chat cell-none" role="cell"><span>Withheld by content check ({esc(answer["reason"])})</span>'
                f'{meta}</div>')
    if answer["state"] != "answered":
        return (f'<div class="cell cell-chat cell-none" role="cell"><span>No reply recorded: {esc(answer["reason"] or "none given")}</span>'
                f'{meta}</div>')
    credit = f'<span class="cell-meta mono">{esc(chat.attribution[label])}</span>' if chat.attribution[label] else ''
    # A long reply keeps its whole text on the page, in a box that scrolls on its own (no script; reachable by keyboard).
    text = (f'<span class="cell-text cell-scroll" tabindex="0" role="group" aria-label="Whole reply, scrolls">{esc(answer["text"])}</span>'
            if len(answer["text"]) > LONG_REPLY else f'<span class="cell-text">{esc(answer["text"])}</span>')
    if chat.kind(answer) == "changed":
        return ('<div class="cell cell-chat cell-changed" role="cell"><div class="cell-tags"><span class="changed-tag mono">CHANGED</span>'
                f'{tag}</div>{text}{credit}{meta}</div>')
    return ('<div class="cell cell-chat cell-same" role="cell">' + (f'<div class="cell-tags">{tag}</div>' if tag else '')
            + f'{text}{credit}{meta}</div>')


def _chat_grid(view: _View, question_id: str) -> str:
    """The second grid of a question page: one column per chat app, one row per date, newest first."""
    chat = view.chat
    columns = min(max(len(chat.labels), 1), MAX_COLUMNS)
    cards = "".join(f'<a href="../card/{esc(question_id)}-{esc(label)}.html">{esc(chat.name[label])}</a>' for label in chat.labels)
    out = ['  <div id="chat-apps" class="group-head spread chat-head"><h2>Chat apps, entered by hand</h2>'
           '<span class="muted small">Newest first · a person typed the question into each app and pasted the reply</span></div>\n',
           '  <div class="grid-scroll" tabindex="0" role="region" aria-label="Replies by date and chat app">\n'
           f'  <div class="grid n-{columns}" role="table" aria-label="Chat-app replies to {esc(question_id)}, newest first">\n'
           '    <div class="grid-row grid-head mono" role="row"><span role="columnheader">DATE</span>'
           + "".join(f'<span role="columnheader">{esc(chat.name[label].upper())}</span>' for label in chat.labels) + '</div>\n']
    for date in reversed(chat.dates_of(question_id)):
        out.append('    <div class="grid-row" role="row"><div class="when" role="rowheader">'
                   f'<span class="when-month">{esc(month_year(date))}</span>'
                   f'<span class="when-day mono">{esc(full_date(date))}</span>'
                   f'<span class="when-day mono">{"scripted date" if chat.demo else "asked by hand"}</span></div>'
                   + "".join(_chat_cell(chat, question_id, label, date) for label in chat.labels) + '</div>\n')
    out.append('  </div>\n  </div>\n'
               '  <p class="muted small">A chat app\'s reply can depend on the account, the country, whether search was on, and the day. '
               'Where the recorded conditions differ from the date before, the cell says so: a changed reply there may not mean the '
               'app changed.</p>\n'
               f'  <p class="card-links small"><span class="muted">Share cards:</span>'
               f'<a href="../card/{esc(question_id)}-{CHATBOTS_SUFFIX}.html">All chat apps</a>{cards}</p>\n')
    return "".join(out)


CHATBOTS_DESCRIPTION = "The same questions, typed into consumer chat apps by hand, with every reply and its conditions kept."


def render_chatbots(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                    sibling: str | None = None) -> str:
    """`chatbots.html`: the questions asked of the chat apps, one strip per app, the conditions recorded
    on each app's latest date, and how the replies are collected. Names are shown as text only."""
    view = _View(payload)
    chat = view.chat
    versions = versions or asset_versions()
    out = [_head("Chatbots · Answers Over Time", "../", versions, CHATBOTS_DESCRIPTION),
           _bar("../", "", versions, "chatbots", standalone=standalone, sibling=sibling, demo=view.demo),
           '<div class="wrap">\n']
    lede = ('A person asked each chat app by hand, in a new chat, and pasted the replies here. No software collected them.'
            + (' On this sample page the apps are made up and the replies are scripted.' if chat.demo else ''))
    out.append('<header class="hero hero-small">\n  <div class="hero-copy">\n'
               f'    <span class="tag tag-dark mono">{esc(view.tag())}</span>\n'
               '    <h1 class="display">The same questions, typed into the chat apps.</h1>\n'
               f'    <p class="lede">{lede}</p>\n'
               '  </div>\n'
               '  <div class="legend">\n'
               '    <strong class="legend-title">How to read a strip</strong>\n'
               '    <span class="legend-note">One square per date asked, oldest on the left.</span>\n'
               '    <div class="legend-row"><span class="sq sq-same"></span><span>Same reply as the time before, or the first reply</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-changed"></span><span>The reply changed</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-none"></span><span>No reply recorded</span></div>\n'
               '    <div class="legend-row"><span class="sq sq-same sq-cond"></span><span>A dot: the conditions differ from the time '
               'before (account, search, country and so on), so a changed reply may not mean the app changed</span></div>\n'
               '  </div>\n</header>\n')
    questions = [view.by_id[i] for i in chat.questions if i in view.by_id]
    if questions and chat.labels:
        total = count_word(len(questions))
        out.append(f'<section class="group">\n  <div class="group-head"><h2>{esc(total[:1].upper() + total[1:])} '
                   f'question{"" if len(questions) == 1 else "s"}</h2>'
                   '<span class="muted">Taken from the two question lists, in the same words.</span></div>\n')
        for question in questions:
            strips = "".join(_chat_strip(chat, question["id"], label) for label in chat.labels)
            out.append('  <article class="q-row">\n    <div class="q-main">'
                       f'<span class="q-id mono">{esc(question["id"])}</span>'
                       f'<div class="q-text"><a href="{esc(view.page(question["id"]))}#chat-apps">{esc(question["text"])}</a>'
                       f'<span class="muted small">{esc(_chat_last_change(chat, question["id"]))}</span></div></div>\n'
                       f'    <div class="q-strips">{strips}</div>\n  </article>\n')
        out.append('</section>\n')
        out.append('<section class="group">\n  <div class="group-head"><h2>How each app was asked</h2>'
                   '<span class="muted">The conditions recorded on the latest date.</span></div>\n')
        for label in chat.labels:
            dates = [d for d in chat.dates if (label, d) in chat.conditions]
            conditions = chat.conditions[(label, dates[-1])]
            out.append('  <div class="app-row"><div class="app-name"><strong>' + esc(chat.name[label]) + '</strong>'
                       f'<span class="muted small">{esc(chat.maker[label])}</span></div>'
                       f'<div class="app-how"><span>{esc(conditions_line(conditions))}</span>'
                       f'<span class="muted small">Model shown by the app: {esc(conditions["shown_model"])} · '
                       f'asked {esc(full_date(dates[-1]))} · {esc(_plural(len(dates), "date"))} in all · {esc(_chat_label(chat))}</span>'
                       + (f'<span class="muted small">{esc(conditions["note"])}</span>' if conditions["note"] else '')
                       + (f'<span class="mono small">{esc(chat.attribution[label])}</span>' if chat.attribution[label] else '')
                       + '</div></div>\n')
        out.append('</section>\n')
        if not chat.demo:
            out.append(f'<p class="list-note muted">{esc(chat_disclaimer([chat.maker[label] for label in chat.labels]))}</p>\n')
    else:
        out.append('<section class="group">\n  <div class="group-head"><h2>Questions</h2></div>\n'
                   '  <p class="muted trend-none">No chat app has been asked yet.</p>\n</section>\n')
    out.append('<section id="method" class="method">\n  <h2 class="display">How the replies are collected</h2>\n  <div class="method-grid">\n'
               '    <div><strong>Asked by hand</strong><span>A person typed each question into the app, in a new chat every time. '
               'No software contacts these apps.</span></div>\n'
               '    <div><strong>Pasted exactly</strong><span>The exact prompt is on each question\'s page. The whole reply is pasted '
               'as it came and is not edited.</span></div>\n'
               '    <div><strong>Conditions recorded and shown</strong><span>Account, temporary chat, memory, search, country, the kind of '
               'app and the model name the app displayed are written down and shown with every reply.</span></div>\n'
               '    <div><strong>Cannot be verified</strong><span>There is no receipt for a reply entered by hand. You are trusting the '
               'person who typed it in.</span></div>\n'
               '    <div><strong>Depends on the day</strong><span>An app\'s reply can depend on the account, the country, whether search '
               'was on, and the day. A changed reply does not always mean the app changed.</span></div>\n'
               '    <div><strong>Not affiliated</strong><span>This archive is not affiliated with or endorsed by the companies named. '
               'Names are used only to say which app was asked.</span></div>\n'
               '  </div>\n</section>\n')
    honesty = ("Sample replies on this page are scripted and the app names are made up." if chat.demo
               else "Replies were entered by hand, cannot be verified and may be wrong.")
    out.append(f'<footer class="foot"><span>Part of LIVE Genesis. {honesty}</span>'
               '<span class="mono small">No payments · nothing can be changed from this page</span></footer>\n')
    out.append('</div>\n</body>\n</html>\n')
    return "".join(out)


def render_chatbot_card(payload: dict, question_id: str, which: str = CHATBOTS_SUFFIX, versions: dict[str, str] | None = None) -> str:
    """A 9:16 share card of the chat-app track. `which` is "chatbots" (`card/<ID>-chatbots.html`: the
    latest reply of up to four apps) or one app's label (`card/<ID>-<label>.html`: that app's latest
    reply at a larger size, with the conditions it was asked under). Every panel carries its date.
    Laid out for a 1080 by 1920 image that a video app covers in part: the question, the app names,
    the replies and the dates sit inside x 65-940, y 270-1440; the wordmark is above and the honesty
    footer below. Raises KeyError for a question or an app that is not in the chat-app track."""
    view = _View(payload)
    chat = view.chat
    versions = versions or asset_versions()
    question = view.by_id.get(question_id)
    single = which != CHATBOTS_SUFFIX
    if question is None or question_id not in chat.questions or (single and which not in chat.labels):
        raise KeyError(question_id)
    panels = [(label, *found) for label in ([which] if single else chat.labels)
              for found in [chat.latest(question_id, label)] if found][:CARD_APPS]
    length = len(question["text"])
    size = "xl" if length <= 26 else "l" if length <= 48 else "m" if length <= 90 else "s"
    title = f"{question['id']} {chat.name[which] if single else 'chat apps'} card · Answers Over Time"
    out = [_head(title, "../../", versions, question["text"]).replace("<body>", '<body class="card-page">'),
           '<main class="card card-safe"><div class="card-in">\n'
           f'  <header class="card-top"><a class="card-brand" href="../{esc(view.page(question_id))}">'
           f'<img class="card-mark" src="../../static/archive-mark.svg?v={versions["archive-mark.svg"]}" alt="" width="57" height="24" />'
           'Answers Over Time</a>'
           '<span class="card-kicker">Chat apps</span></header>\n'
           f'  <div class="card-body">\n  <h1 class="card-q card-q-{size}">{esc(question["text"])}</h1>\n'
           f'  <div class="card-panels {"one" if single else "n-" + str(max(len(panels), 1))}">\n']
    for label, date, answer in panels:
        changed = chat.kind(answer) == "changed"
        credit = f'<span class="card-credit mono">{esc(chat.attribution[label])}</span>' if chat.attribution[label] else ''
        how = (f'<span class="card-cond mono">{esc(conditions_line(chat.conditions[(label, date)]))}</span>' if single else '')
        out.append(f'    <section class="card-panel{" card-panel-changed" if changed else ""}">'
                   f'<div class="card-meta mono"><span class="card-label">{esc(chat.name[label])}</span>'
                   f'<span class="card-when">{esc(full_date(date))} · {esc(_chat_label(chat))}'
                   f'{" · changed" if changed else ""}</span></div>'
                   f'<p class="card-answer">{esc(answer["text"])}</p>{how}{credit}</section>\n')
    if not panels:
        out.append('    <section class="card-panel"><p class="card-answer card-empty">No replies recorded yet.</p></section>\n')
    if chat.demo:
        first, second = view.tag(), f"{question['id']} · made-up app names · read-only · no payments"
    else:
        first, second = CHAT_CARD_FOOT, f"{question['id']} · same question every time · read-only · no payments"
    out.append('  </div>\n  </div>\n'
               f'  <footer class="card-foot mono"><span>{esc(first)}</span><span>{esc(second)}</span></footer>\n'
               '</div></main>\n</body>\n</html>\n')
    return "".join(out)


def static_files() -> list[str]:
    """Every file under static/ that the archive pages need: the stylesheet, the mark, the icon, the
    fonts the stylesheet names and each font's licence text. Nothing of the observatory page."""
    names = list(ASSETS)
    for font in sorted(set(re.findall(r'url\("(fonts/[A-Za-z0-9._-]+)"\)', (STATIC / "archive.css").read_text(encoding="utf-8")))):
        names.append(font)
        licence = f"{font.rsplit('.', 1)[0]}-OFL.txt"
        if (STATIC / licence).is_file():
            names.append(licence)
    return names


def render_site(payload: dict, *, standalone: bool = False, sibling: str | None = None) -> dict[str, str]:
    """Every archive page, keyed by its path under `archive/`."""
    versions = asset_versions()
    options = {"standalone": standalone, "sibling": sibling if standalone else None}
    pages = {"index.html": render_index(payload, versions, **options), "trending.html": render_trending(payload, versions, **options)}
    for folder, questions in (("q", payload["questions"]), ("t", payload["trending"]["questions"])):
        for question in questions:
            pages[f"{folder}/{question['id']}.html"] = render_question(payload, question["id"], versions, **options)
            pages[f"card/{question['id']}.html"] = render_card(payload, question["id"], versions)
    pages["chatbots.html"] = render_chatbots(payload, versions, **options)
    block = payload.get("chatbots") or {"questions": [], "apps": []}
    for question_id in block["questions"]:
        for suffix in [CHATBOTS_SUFFIX] + [app["label"] for app in block["apps"]]:
            pages[f"card/{question_id}-{suffix}.html"] = render_chatbot_card(payload, question_id, suffix, versions)
    return pages
