"""Static HTML for Answers Over Time: the front page, the list pages, one page per question, one
portrait share card per question, and the machine-readable data files that go with them.

Pure functions from the public payload (archive.public_payload, or a reviewed
published/archive.json) to text. No database, no model, no JavaScript, no inline style or script:
the pages work under the site's Content-Security-Policy and with scripts off. Every piece of text
that reaches a page goes through `esc`; model output is untrusted. All links are relative, so the
same pages work in a static snapshot under any sub-path and in both web apps.

Pages (under `archive/`):
* `index.html`: the front page. With a chat-app run in the payload it is the chat-app comparison (the
  track's own questions, each with the newest answer of every app and of the Human column). Without
  one it lists the trending and the core questions with the models' newest answers: never an empty
  chat-app shell.
* `models.html` (the core questions), `trending.html` (the trending questions): the model track.
* `c/C001.html` (a chat-app question), `q/Q01.html`, `t/T001.html` (a model question): one layout.
  This month's answers as cards; older months one tap away (radio inputs and labels, no script); a
  changed answer shows what it was before; each card has its history; a table shows what changed.
* `chatbots.html`: how the chat apps are asked, and the conditions recorded.
* `card/<ID>.html` (a model question: up to three models; a chat-app question: the apps and Human),
  `card/<ID>-<app label>.html`, `card/<ID>-human.html`: 9:16 share cards.
* `data/<ID>.json`, `data/answers.jsonl`, `data/README.txt`: the same answers for machines.

What is VISIBLE and what is metadata: the short honesty labels stay visible, once per page or card
(AI-generated; typed in by hand / we asked it ourselves; may be wrong; not affiliated; an app's own
attribution line; the sample label on scripted data; Human with quoted / summarised; Changed and the
Before line; the line that refusals are left out). Everything else about an answer (version shown,
account, search, country, endpoint, digest, sample counts ...) is in `data-*` attributes and
schema.org microdata on the answer's element, in the `<meta name="aot:...">` tags of the page head,
in the data files, and for people inside the closed "Where each answer came from" block. A visible
honesty label is never moved into metadata only.

Hidden questions (`HIDDEN`, owner's decision): a question listed there has no page, no card and no data
file and is in no list; the record keeps it. Nothing is hidden now.

Chat apps and Human (the optional `chatbots` block; archive_chatbots.py): replies a person collected
by hand. Never mixed into a model's grid or timeline. A refusal, a non-answer or a reply the content
check withheld is never shown as a card; the page says "Refusals and non-answers are left out."
App names are plain text, never a logo, and never the largest thing on a page or card.

Standalone: `render_site(payload, standalone=True)` is the archive published on its own (snapshot.py
`--archive-only`). `sibling` (standalone only) is the relative address of the other published
snapshot as seen from the site root (`demo/` or `../`); the bar links its archive.
"""
from __future__ import annotations

import hashlib
import html
import json
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
# How an answer got here, in plain words (the internal names stay in the code and the data).
SOURCE_LABELS = {"engine": "We asked it ourselves", "api": "Sent in by someone else, not checked",
                 "demo": "Sample we wrote, not a real AI answer", "manual": "Typed in by hand"}
METHODS = {"engine": "asked-by-server", "api": "sent-in-unverified", "demo": "scripted-sample", "manual": "typed-by-hand"}
HUMAN_METHOD = "collected-by-hand"          # the Human column of a real collection; a sample is scripted-sample
KINDS = ("chat-app", "model", "human")
STATES_SHOWN = ("answered", "changed", "same", "mixed")   # data-state of a visible answer; "answered" is a first answer
SAMPLE = "scripted-sample"
SAMPLE_LABEL = SOURCE_LABELS["demo"]
HIDDEN_WORDS = "Hidden: broke our content rules"
LEFT_OUT = "Refusals and non-answers are left out."
DATA_SCHEMA = "answers-over-time-data/1"
DATA_NOTICE = ("Answers Over Time. AI answers are recorded verbatim and may be wrong; they are not advice. Text marked "
               "human-written was written by people and collected by hand. Records marked scripted-sample were written "
               "as samples: no AI and no person was asked, and they must not be treated as real answers. Product names "
               "belong to their owners; this archive is not affiliated with them.")
ALL_CARD = "all"               # render_chatbot_card: the card with every app and Human
HUMAN = "human"                # the Human column's key and its card suffix (archive_chatbots.HUMAN_LABEL)
CARD_APPS = 4
CARD_NAME = re.compile(r"(C[0-9]{3})-([a-z][a-z0-9-]{1,23})")
CHAT_ID = re.compile(r"C[0-9]{3}")
DATA_NAME = re.compile(r"(?:Q[0-9]{2}|T[0-9]{3}|C[0-9]{3})\.json|answers\.jsonl|README\.txt")
CHAT_CARD_FOOT = "AI-generated · typed in by hand · not affiliated · may be wrong"
CARD_FULL = "full answer on the site"
CARD_CLAMP_CHARS = {1: 160, 2: 115, 3: 100, 4: 110}   # panels on a card -> characters that surely fit its clamp
# Server-side thresholds for the "More" control: longer than the first is clamped on a phone, than the second also on
# a tablet, than the third everywhere. Chosen so that a shorter text always fits the clamp of that width.
CLAMP_PHONE, CLAMP_TABLET, CLAMP_DESKTOP = 140, 240, 300
BEFORE_CLAMP = 85
TABLE_CLAMP = 320
MAX_PANELS = 24                # month panels on a question page (the stylesheet has rules for this many); the table has all
# Why a model has no answers in a run it was dropped from (archive.SKIP_REASONS), in plain words.
SKIP_WORDS = {"rate_limited": "the service limited the number of requests", "not_found": "the service did not know this model",
              "auth": "the service refused the key", "timeout": "the call timed out", "server_error": "the service reported an error",
              "no_usage": "the service sent no token counts", "not_ready": "the local setup was not ready",
              "other": "the call failed"}
NATIVE = ("openai", "anthropic", "openai-compatible")   # not a named endpoint: nothing to add to the label
FOLDER = {"core": "q", "trending": "t", "chat": "c"}
CARD_MODELS = 3
FIRST_PAGE = "index.html"      # the page a visitor lands on; with a chat-app run it is the chat-app comparison


def esc(value: object) -> str:
    """The one escaping helper: safe in element content and in a double-quoted attribute."""
    return html.escape(str(value), quote=True)


def asset_versions() -> dict[str, str]:
    """Short content hash per asset, appended to its URL so a changed file is fetched again."""
    return {name: hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12] for name in ASSETS}


# Questions no page shows (owner's decision; the reason is for maintainers and is never rendered). A locked
# question can never leave its list or the data file, so hiding is all the pages can do: a hidden question has
# no page, no card and no data file, is in no list and no count, and its answers are not rendered. `archive.json`
# still holds it, and so does the repository's history.
HIDDEN: dict[str, str] = {}   # id -> reason. Empty: the one question hidden so far was removed outright on 14 November 2026.


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


def iso_date(value: str) -> str:
    parts = _date(value)
    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}" if parts else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _replies(n: int) -> str:
    return "1 reply" if n == 1 else f"{n} replies"


def _listed(names: list[str], last: str = "and") -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" {last} " + names[-1]


class _Chat:
    """Lookups over the chat-app track of one payload (empty when the payload has none). Time is told in
    DATES here: one period per day on which the owner asked, whatever import carried it. The Human
    column is one more column with the key "human" and its own kind."""

    def __init__(self, payload: dict):
        block = payload.get("chatbots") or {"apps": [], "human": None, "questions": [], "runs": [], "answers": [], "human_answers": []}
        self.apps = block["apps"]
        self.human = block["human"]
        self.labels = [app["label"] for app in self.apps]
        self.columns = [{"key": app["label"], "name": app["name"], "kind": "chat-app", "maker": app["maker"],
                         "attribution": app.get("attribution", "")} for app in self.apps]
        if self.human:
            self.columns.append({"key": HUMAN, "name": self.human["name"], "kind": "human", "maker": "", "attribution": "",
                                 "meta": self.human["meta"]})
        self.name = {column["key"]: column["name"] for column in self.columns}
        self.maker = {app["label"]: app["maker"] for app in self.apps}
        self.questions = list(block["questions"])
        self.by_id = {q["id"]: q for q in self.questions}
        runs = sorted(block["runs"], key=lambda r: (r["collected_on"], r["number"]))
        self.demo = any(run["demo"] for run in runs)
        self.real = bool(runs) and not self.demo
        self.dates = sorted({run["collected_on"] for run in runs})
        self.day = {run["number"]: run["collected_on"] for run in runs}
        self.conditions: dict[tuple[str, str], dict] = {}
        self.collection: dict[str, dict] = {}
        self.asked: dict[tuple[str, str], list] = {}
        for run in runs:
            for label, conditions in run["conditions"].items():
                self.conditions[(label, run["collected_on"])] = conditions
                self.asked[(label, run["collected_on"])] = run["questions"]
            if run.get("human"):
                self.collection[run["collected_on"]] = run["human"]
                self.asked[(HUMAN, run["collected_on"])] = run["questions"]
        self.answers = [a for a in block["answers"] if a["source"] in ("manual", "demo")]
        self.cell = {(a["question"], a["chatbot"], self.day[a["run"]]): a for a in self.answers}
        for answer in block["human_answers"]:
            if answer["source"] in ("manual", "demo"):
                self.cell[(answer["question"], HUMAN, self.day[answer["run"]])] = answer

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
    """Lookups over one payload, shared by every page kind."""

    def __init__(self, payload: dict):
        problem = archive.payload_problem(payload)
        if problem:
            raise ValueError(f"archive payload is malformed: {problem}")
        self.hidden = frozenset(HIDDEN)
        self.payload = payload
        self.questions = [q for q in payload["questions"] if q["id"] not in self.hidden]
        self.trend = payload["trending"]
        self.trending = [q for q in self.trend["questions"] if q["id"] not in self.hidden]
        self.by_id = {q["id"]: q for q in self.questions + self.trending}
        # A question's place counts hidden ones too: a run asked "the first N", and N includes them.
        self.place = ({q["id"]: ("core", n) for n, q in enumerate(payload["questions"], 1)}
                      | {q["id"]: ("trending", n) for n, q in enumerate(self.trend["questions"], 1)})
        self.runs = sorted(payload["runs"], key=lambda r: r["number"])
        self.labels = list(payload["answerers"])
        # Mock rows are never rendered, whatever the caller passed in; nor is an answer to a hidden question.
        self.answers = [a for a in payload["answers"] if a["source"] in archive.PUBLIC_SOURCES and a["question"] not in self.hidden]
        self.cell = {(a["question"], a["answerer"], a["run"]): a for a in self.answers}
        self.by_series: dict[tuple[str, str], list[dict]] = {}
        for answer in sorted(self.answers, key=lambda a: a["run"]):
            self.by_series.setdefault((answer["question"], answer["answerer"]), []).append(answer)
        self.run_date = {r["number"]: r["date"] for r in self.runs}
        self.chat = _Chat(payload)
        self.chat_questions = [q for q in self.chat.questions if q["id"] not in self.hidden]
        self.models_demo = any(a["source"] == "demo" for a in self.answers) or any(r["demo"] for r in self.runs)
        self.demo = self.models_demo or self.chat.demo
        self.empty = not self.answers and not self.chat.cell
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
        """none / same / changed / unsettled: the state of one model cell."""
        if answer is None or answer["state"] != "answered":
            return "none"
        label = self.label[(answer["question"], answer["answerer"], answer["run"])]
        return "same" if label == "steady" else label

    def asked(self, question_id: str) -> list[dict]:
        """The runs that asked this question, oldest first."""
        track, position = self.place[question_id]
        return [run for run in self.runs if run["asked"][track] >= position]

    def track(self, question_id: str) -> str:
        return "chat" if question_id in self.chat.by_id else self.place[question_id][0]

    def page(self, question_id: str) -> str:
        return f"{FOLDER[self.track(question_id)]}/{question_id}.html"

    def shown(self, question_id: str) -> bool:
        return question_id not in self.hidden and (question_id in self.by_id or question_id in self.chat.by_id)

    def tag(self, track: str = "models") -> str:
        """The one short honesty label of a page or card."""
        if self.empty:
            return "No answers recorded yet"
        if track == "chat":
            return SAMPLE_LABEL if self.chat.demo else "AI-generated · typed in by hand · may be wrong"
        if self.models_demo or (self.demo and not self.answers):
            return SAMPLE_LABEL
        if not self.answers:
            return "No answers recorded yet"
        ours = all(a["source"] == "engine" for a in self.answers)
        return "AI-generated · we asked it ourselves · may be wrong" if ours else "AI-generated · may be wrong"

    def model_change(self, question_id: str, label: str, run_number: int) -> tuple[str, str, str, str] | None:
        """(short tag, what changed, before, now) when the model behind this answer is not the one behind
        this answerer's previous recorded answer: a different version was requested, the service reported
        a different one, or the local model file has a different digest. A value is compared only when
        it was recorded on both sides."""
        now = self.cell.get((question_id, label, run_number))
        earlier = [a for a in self.by_series.get((question_id, label), ()) if a["run"] < run_number]
        if now is None or not earlier:
            return None
        before = earlier[-1]
        if before["model"] != now["model"]:
            return "new version", "version", before["model"], now["model"]
        for key, tag, what in (("reported_model", "new version", "version reported by the service"),
                               ("model_digest", "new model file", "local model file (digest)")):
            old, new = before.get(key), now.get(key)
            if old and new and old != new:
                return tag, what, old, new
        return None


# ---- One question as a board: columns (answerers), periods (months), cells -----------------------------------

def _period_labels(dates: list[str]) -> list[str]:
    """A label per period, oldest first: "Nov 2026" when the months differ, "13 Nov 2026" when two fall in
    one month, and a running number when two fall on one day."""
    months = [month_year(d) for d in dates]
    if len(set(months)) == len(months):
        return months
    days = [full_date(d) for d in dates]
    if len(set(days)) == len(days):
        return days
    return [f"{day} · check {n}" for n, day in enumerate(days, 1)]


def _agreement(view: _View, answer: dict) -> str:
    """"same answer 3 times out of 3" for a cell of a check that asked several times; "" otherwise."""
    samples = answer.get("samples")
    if samples is None:
        return ""
    asked = view.run_samples[answer["run"]]
    if not samples:
        return "none of the tries gave an answer"
    return (f"same answer {answer['agree']} times out of {samples}"
            + (f" · only {samples} of {asked} tries answered" if samples < asked else ""))


def _where(answer: dict) -> str:
    """Where a model we asked ran, as far as the record says: on the operator's own machine, or through
    a named endpoint. Says nothing about who made the model or how open it is."""
    if answer["source"] != "engine":
        return ""
    if answer.get("local"):
        return "run locally"
    endpoint = answer.get("endpoint")
    return f"via {endpoint}" if endpoint and endpoint not in NATIVE else ""


def _model_board(view: _View, question_id: str) -> dict:
    question = view.by_id[question_id]
    trending = view.place[question_id][0] == "trending"
    runs = view.asked(question_id)
    labels = _period_labels([run["date"] for run in runs])
    periods = [{"key": f"run-{run['number']}", "date": iso_date(run["date"]), "label": label, "run": run}
               for run, label in zip(runs, labels)]
    columns = [{"key": label, "name": label, "kind": "model", "maker": "", "attribution": ""} for label in view.labels]
    cells: dict[tuple[str, str], dict] = {}
    for column in columns:
        previous = None
        for period in periods:
            run = period["run"]
            answer = view.cell.get((question_id, column["key"], run["number"]))
            cell = {"record": f"{question_id}/{column['key']}/{period['key']}", "answer_id": None, "shown": False, "text": None,
                    "state": "no-answer", "version": "", "method": "", "provenance": "", "form": None,
                    "collected": period["date"], "previous": None, "why_not": "No answer this time", "tags": [], "facts": [],
                    "conditions": None, "agreement": "", "others": [], "changed": None}
            if answer is None:
                why = SKIP_WORDS.get(run.get("skipped", {}).get(column["key"], ""))
                if why:
                    cell["why_not"] = f"No answer this time: {why}"
                cell["method"] = cell["provenance"] = SAMPLE if run["demo"] else ""
                cells[(column["key"], period["key"])] = cell
                continue
            change = view.model_change(question_id, column["key"], run["number"])
            where, agreement = _where(answer), _agreement(view, answer)
            sample = answer["source"] == "demo"
            cell.update(answer_id=answer["id"], version=answer.get("reported_model") or answer["model"],
                        method=METHODS[answer["source"]], provenance=SAMPLE if sample else "ai-generated",
                        agreement=agreement, changed=answer["changed"],
                        conditions={key: answer.get(key) for key in ("model",) + archive.PUBLIC_CALL_FIELDS + archive.SAMPLE_FIELDS
                                    if key != "others" and answer.get(key) is not None})
            facts = [SOURCE_LABELS[answer["source"]], f"version {answer['model']}"]
            if answer.get("reported_model") and answer["reported_model"] != answer["model"]:
                facts.append(f"the service called it {answer['reported_model']}")
            facts += [where] if where else []
            facts += [agreement] if agreement else []
            if answer.get("model_digest"):
                facts.append(f"model file {answer['model_digest']}")
            if change:
                facts.append(f"{change[0]}: {change[1]} changed from {change[2]} to {change[3]}")
            cell["facts"] = facts
            if answer["state"] == "withheld":
                cell.update(state="hidden", why_not=f"{HIDDEN_WORDS} ({answer['reason']})")
            elif answer["state"] != "answered":
                cell["why_not"] = f"No answer this time: {answer['reason'] or 'none given'}"
            else:
                kind = view.kind(answer)
                state = "mixed" if kind == "unsettled" else "answered" if previous is None else kind
                cell.update(shown=True, text=answer["text"], state=state, previous=previous,
                            others=list(answer.get("others", ())) if kind == "unsettled" else [])
                if kind == "unsettled" and not archive.split(answer):
                    cell["facts"].append("the check before gave mixed answers")
                previous = period["key"]
            cells[(column["key"], period["key"])] = cell
    if trending:
        chips = [("chip chip-topic", question["topic"]), ("chip-note", f"added {month_year(question['added'])}")]
        if not view.trend["locked"] and not view.demo:
            chips.append(("chip", "Draft question"))
        why = (f"{question['rationale']} Topic taken from: {question['trend_source']}. Added {full_date(question['added'])}; "
               "asked every time since, in the same words.")
    else:
        chips, why = [("chip", question["group"])], question["rationale"]
    return {"id": question_id, "text": question["text"], "prompt": question["prompt"], "hook": "", "why": why, "chips": chips,
            "track": "trending" if trending else "core", "columns": columns, "periods": periods, "cells": cells,
            "demo": view.models_demo, "kicker": question["topic"] if trending else question["group"],
            "prompt_note": "Sent on its own. The model gets no date, no other answers and no earlier replies."}


def _chat_board(view: _View, question_id: str) -> dict:
    chat = view.chat
    question = chat.by_id[question_id]
    dates = [d for d in chat.dates if any(question_id in chat.asked.get((column["key"], d), ()) for column in chat.columns)]
    periods = [{"key": d, "date": d, "label": label} for d, label in zip(dates, _period_labels(dates))]
    sample = chat.demo
    cells: dict[tuple[str, str], dict] = {}
    for column in chat.columns:
        key, human = column["key"], column["kind"] == "human"
        previous = None
        for period in periods:
            date = period["key"]
            if question_id not in chat.asked.get((key, date), ()):
                continue
            answer = chat.cell.get((question_id, key, date))
            conditions = chat.collection.get(date) if human else chat.conditions.get((key, date))
            cell = {"record": f"{question_id}/{key}/{date}", "answer_id": answer["id"] if answer else None, "shown": False,
                    "text": None, "state": "no-answer", "version": "" if human else (conditions or {}).get("shown_model", ""),
                    "method": SAMPLE if sample else HUMAN_METHOD if human else METHODS["manual"],
                    "provenance": SAMPLE if sample else "human-written" if human else "ai-generated",
                    "form": None, "collected": date, "previous": None, "why_not": "left out", "tags": [], "facts": [],
                    "conditions": dict(conditions) if conditions else None, "agreement": "", "others": [], "changed": None}
            if human:
                facts = [SAMPLE_LABEL if sample else "Written by people, collected by hand"]
                if conditions:
                    facts.append(f"from: {conditions['source']}")
                    if conditions["collected_on"] != date:
                        facts.append(f"collected {full_date(conditions['collected_on'])}")
                    if conditions["count"]:
                        facts.append(f"{_replies(conditions['count'])} read")
            else:
                moved = chat.conditions_changed(key, date)
                facts = [SOURCE_LABELS["demo" if sample else "manual"], f"the app showed: {conditions['shown_model']}",
                         conditions_line(conditions)]
                if chat.shown_change(key, date):
                    facts.append("the app showed a new version name")
                if moved:
                    facts.append("conditions changed · " + "; ".join(moved))
                    cell["tags"].append("Conditions changed")
                if conditions["note"]:
                    facts.append(conditions["note"])
                if answer and answer.get("screenshot_hash"):
                    facts.append("screenshot kept")
            cell["facts"] = facts
            if answer is None:
                pass
            elif answer.get("refused"):
                cell.update(state="refused", text=answer["text"])
            elif answer["state"] == "withheld":
                cell["state"] = "hidden"
            elif answer["state"] == "answered":
                state = "answered" if previous is None or answer["changed"] is None else "changed" if answer["changed"] else "same"
                cell.update(shown=True, text=answer["text"], state=state, previous=previous, changed=answer["changed"],
                            form=answer.get("manner") if human else None)
                if human:
                    cell["facts"].append(answer["manner"])
                previous = date
            cells[(key, date)] = cell
    return {"id": question_id, "text": question["text"], "prompt": question["prompt"], "hook": question["hook"],
            "why": "No deep reason. We wanted to see who's funny, and whether they're still funny next month.",
            "chips": [("chip", "Chat apps")], "track": "chat", "columns": chat.columns, "periods": periods, "cells": cells,
            "demo": sample, "kicker": "Chat apps",
            "prompt_note": "Typed into each app by hand, in a new chat. The first line is why the replies are short."}


def _board(view: _View, question_id: str) -> dict:
    if not view.shown(question_id):
        raise KeyError(question_id)
    return _chat_board(view, question_id) if question_id in view.chat.by_id else _model_board(view, question_id)


def _column(board: dict, key: str) -> dict:
    return next(column for column in board["columns"] if column["key"] == key)


def _series(board: dict, key: str) -> list[tuple[dict, dict]]:
    """(period, cell) for every period in which this column was asked, oldest first."""
    return [(period, board["cells"][(key, period["key"])]) for period in board["periods"] if (key, period["key"]) in board["cells"]]


def _latest(board: dict, key: str) -> tuple[dict, dict] | None:
    shown = [(period, cell) for period, cell in _series(board, key) if cell["shown"]]
    return shown[-1] if shown else None


def _previous(board: dict, key: str, cell: dict) -> tuple[dict, dict] | None:
    """(period, cell) of the shown answer this one is compared with, or None for a first answer."""
    if cell["previous"] is None:
        return None
    period = next(p for p in board["periods"] if p["key"] == cell["previous"])
    return period, board["cells"][(key, period["key"])]


def _groups(board: dict, key: str) -> list[dict]:
    """This answerer's distinct answers, oldest first, runs of the same answer collapsed:
    [{text, first, last, checks}]. The number of changes is len - 1."""
    out: list[dict] = []
    for period, cell in _series(board, key):
        if not cell["shown"]:
            continue
        form = archive.normalise(cell["text"])
        if out and out[-1]["form"] == form:
            out[-1].update(last=period, checks=out[-1]["checks"] + 1)
        else:
            out.append({"form": form, "text": cell["text"], "first": period, "last": period, "checks": 1})
    return out


def _span(first: dict, last: dict) -> str:
    """"Aug–Oct 2026", "Nov 2025–Jan 2026" or "Nov 2026"."""
    if first["key"] == last["key"]:
        return first["label"]
    a, b = first["label"], last["label"]
    return f"{a[:-5]}–{b}" if a[-4:] == b[-4:] and len(a) > 5 else f"{a}–{b}"


def record(board: dict, column: dict, period: dict, cell: dict) -> dict:
    """One answer as a self-contained record: what the data files hold and what the `data-*` attributes
    of its element say. Built from the payload only."""
    previous = _previous(board, column["key"], cell) if cell["shown"] else None
    return {"schema": DATA_SCHEMA, "id": cell["record"], "question_id": board["id"], "question": board["text"],
            "prompt": board["prompt"], "answerer": column["key"], "answerer_name": column["name"],
            "answerer_kind": column["kind"], "maker": column["maker"], "version": cell["version"],
            "collected": cell["collected"], "period": period["label"], "method": cell["method"],
            "provenance": cell["provenance"], "human_form": cell["form"], "state": cell["state"], "shown": cell["shown"],
            "text": cell["text"], "changed": cell["changed"],
            "previous_id": previous[1]["record"] if previous else None,
            "previous_collected": previous[1]["collected"] if previous else None,
            "previous_text": previous[1]["text"] if previous and cell["state"] == "changed" else None,
            "conditions": cell["conditions"], "attribution": column["attribution"], "lang": "en", "notice": DATA_NOTICE}


def _clamp(text: str) -> str:
    """The clamp classes of an answer text, by its length ("" when it is short enough to show whole everywhere)."""
    size = len(text)
    if size > CLAMP_DESKTOP:
        return "clamp c-s c-m c-l"
    if size > CLAMP_TABLET:
        return "clamp c-s c-m"
    return "clamp c-s" if size > CLAMP_PHONE else ""


def data_attrs(rec: dict) -> str:
    """The `data-*` attributes of one visible answer element, from its record."""
    pairs = [("question-id", rec["question_id"]), ("answerer", rec["answerer"]), ("answerer-kind", rec["answerer_kind"]),
             ("maker", rec["maker"]), ("version", rec["version"]), ("collected", rec["collected"]), ("method", rec["method"]),
             ("provenance", rec["provenance"])]
    if rec["human_form"]:
        pairs.append(("human-form", rec["human_form"]))
    pairs.append(("state", rec["state"]))
    if rec["state"] == "changed" and rec["previous_collected"]:
        pairs.append(("previous-collected", rec["previous_collected"]))
    pairs += [("truncated-display", "true" if rec["text"] and _clamp(rec["text"]) else "false"), ("lang", rec["lang"])]
    return "".join(f' data-{name}="{esc(value)}"' for name, value in pairs)


# ---- Page furniture ---------------------------------------------------------------------------------------------

def _head(title: str, up: str, versions: dict[str, str], description: str, extra: str = "") -> str:
    return ('<!doctype html>\n<html lang="en">\n<head>\n'
            f'  <meta http-equiv="Content-Security-Policy" content="{esc(web.CONTENT_SECURITY_POLICY)}" />\n'
            '  <meta charset="utf-8" />\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
            '  <meta name="color-scheme" content="light" />\n'
            f'  <title>{esc(title)}</title>\n'
            f'  <meta name="description" content="{esc(description)}" />\n'
            + extra +
            f'  <link rel="icon" type="image/svg+xml" href="{up}static/archive-icon.svg?v={versions["archive-icon.svg"]}" />\n'
            f'  <link rel="stylesheet" href="{up}static/archive.css?v={versions["archive.css"]}" />\n'
            '</head>\n<body>\n')


def _meta(view: _View, base: str, *, board: dict | None = None, track: str = "models") -> str:
    """The machine-readable part of a page head: `aot:*` meta tags and the links to the data files."""
    sample = view.chat.demo if track == "chat" else view.models_demo
    out = [f'  <meta name="aot:schema" content="{DATA_SCHEMA}" />\n',
           f'  <meta name="aot:content" content="{SAMPLE if sample else "ai-generated-answers-recorded-verbatim"}" />\n']
    if board:
        shown = [cell["collected"] for cell in board["cells"].values() if cell["shown"]]
        out += [f'  <meta name="aot:question-id" content="{esc(board["id"])}" />\n',
                f'  <meta name="aot:prompt" content="{esc(board["prompt"])}" />\n',
                f'  <meta name="aot:collected-latest" content="{esc(max(shown) if shown else "")}" />\n',
                f'  <meta name="aot:answerers" content="{esc(",".join(column["key"] for column in board["columns"]))}" />\n',
                f'  <link rel="alternate" type="application/json" href="{base}data/{esc(board["id"])}.json" />\n']
    else:
        out.append(f'  <link rel="alternate" type="application/x-ndjson" href="{base}data/answers.jsonl" />\n')
    return "".join(out)


def _bar(view: _View, up: str, base: str, versions: dict[str, str], current: str = "", *, standalone: bool = False,
         sibling: str | None = None) -> str:
    """The top bar: one line. `base` leads to the archive folder ("" on its own pages, "../" one folder
    down). The last link goes to the observatory; a standalone archive has none, so it is left out there
    (or, when the build names the other published snapshot, replaced by a link to that snapshot's archive)."""
    chat = bool(view.chat.dates)
    if not standalone:
        last = f'    <a href="{up}">Observatory</a>\n'
    elif sibling:
        # A sample page never calls the other snapshot (or itself) "recorded".
        last = (f'    <a href="{esc(up + sibling)}archive/{FIRST_PAGE}">'
                f'{"Main archive" if view.demo else "Sample site"}</a>\n')
    else:
        last = ""
    links = [("front", f"{base}index.html", "Chat apps" if chat else "Questions"), ("trending", f"{base}trending.html", "Trending"),
             ("models", f"{base}models.html", "Models")]
    links.append(("chatbots", f"{base}chatbots.html", "How it works") if chat else ("method", f"{base}models.html#method", "How it works"))
    nav = "".join(f'    <a href="{href}"{" aria-current=" + chr(34) + "page" + chr(34) if name == current else ""}>{text}</a>\n'
                  for name, href, text in links)
    return ('<a class="skip" href="#main">Skip to the content</a>\n'
            '<header class="bar"><div class="bar-in">\n'
            f'  <a class="wordmark" href="{base}{FIRST_PAGE}"><img class="mark" src="{up}static/archive-mark.svg?v={versions["archive-mark.svg"]}" '
            'alt="" width="57" height="24" />Answers Over Time</a>\n'
            '  <nav aria-label="Archive navigation">\n' + nav + last + '  </nav>\n'
            '</div></header>\n')


def chat_disclaimer(makers: list[str]) -> str:
    """The notice shown wherever real hand-entered replies are: what they are and that the site is
    independent of the companies whose apps were asked (named from the recorded apps, in their order)."""
    names: list[str] = []
    for maker in makers:
        if maker not in names:
            names.append(maker)
    return ("AI-generated answers, typed into each app by hand and copied unedited. They may be wrong and are not advice. "
            f"Not affiliated with, sponsored by or endorsed by {_listed(names, 'or')}; product names belong to their owners.")


def _foot(view: _View, track: str, shown_ids: list[str], back: str = "") -> str:
    """The honesty footer, once per page: read-only, what the answers are, and, on a chat-app page, that
    refusals and non-answers are left out."""
    sample = view.chat.demo if track == "chat" else view.models_demo
    if sample:
        honesty = "Everything here is a sample we wrote. No AI and no person was asked."
    elif track == "chat":
        honesty = chat_disclaimer([view.chat.maker[label] for label in view.chat.labels]) if view.chat.labels else \
            "Answers written by people, collected by hand."
    else:
        honesty = "Answers are AI output and may be wrong."
    lines = [LEFT_OUT] if track == "chat" else []
    return ('<footer class="foot">' + back + f'<span>Read-only archive. Part of LIVE Genesis. {esc(honesty)}</span>'
            + "".join(f'<span>{esc(line)}</span>' for line in lines)
            + '<span class="mono small">No payments · nothing can be changed from this page</span></footer>\n')


def _square(cell: dict) -> str:
    kind = {"answered": "same", "same": "same", "changed": "changed", "mixed": "mixed"}.get(cell["state"], "none")
    return f'<span class="sq sq-{kind}{" sq-cond" if cell["tags"] else ""}"></span>'


def _timeline(board: dict, column: dict) -> str:
    """One square per period this answerer was asked, oldest on the left. The accessible name says WHEN it
    changed ("Chat app A: changed Oct 2026 and Nov 2026; no answer Sep 2026; 4 months")."""
    series = _series(board, column["key"])
    if not series:
        return ""
    word = "different answer" if column["kind"] == "human" else "changed"
    changed = [period["label"] for period, cell in series if cell["state"] == "changed"]
    mixed = [period["label"] for period, cell in series if cell["state"] == "mixed"]
    missing = [period["label"] for period, cell in series if not cell["shown"]]
    moved = [period["label"] for period, cell in series if cell["tags"]]
    parts = [f"{word} {_listed(changed)}" if changed else "no change"]
    parts += [f"mixed answers {_listed(mixed)}"] if mixed else []
    parts += [f"no answer {_listed(missing)}"] if missing else []
    parts += [f"conditions changed {_listed(moved)}"] if moved else []
    parts.append(_plural(len(series), "check"))
    summary = f"{column['name']}: " + "; ".join(parts)
    return (f'<span class="timeline" role="img" aria-label="{esc(summary)}">'
            + "".join(_square(cell) for _period, cell in series) + '</span>')


def _key(view: _View, chat: bool) -> str:
    """The one-line key to the timeline squares."""
    return ('<p class="key"><span class="key-item"><span class="sq sq-same"></span>same</span>'
            '<span class="key-item"><span class="sq sq-changed"></span>changed</span>'
            + ('<span class="key-item"><span class="sq sq-mixed"></span>mixed answers</span>' if view.multi and not chat else '')
            + '<span class="key-item"><span class="sq sq-none"></span>no answer</span>'
            + ('<span class="key-item"><span class="sq sq-same sq-cond"></span>asked differently</span>' if chat else '')
            + '<span class="key-note">one square per check, oldest first</span></p>')


def _row(board: dict, href: str) -> str:
    """One question on a list page: the question as a heading with a link, then every answerer's newest
    answer in a mini-cell (name, timeline, the text clamped by the stylesheet)."""
    cells = []
    for column in board["columns"]:
        found = _latest(board, column["key"])
        line = _timeline(board, column)
        if found is None:
            if not line:
                continue
            cells.append(f'<div class="mini mini-none"><span class="mini-name mono">{esc(column["name"])}</span>{line}'
                         '<span class="mini-text muted">no answer yet</span></div>')
            continue
        period, cell = found
        rec = record(board, column, period, cell)
        newest = board["periods"][-1]["key"] == period["key"]
        cells.append(f'<div class="mini{" is-changed" if cell["state"] == "changed" and newest else ""}'
                     f'{" kind-human" if column["kind"] == "human" else ""}"{data_attrs(rec)}>'
                     f'<span class="mini-name mono">{esc(column["name"])}</span>{line}'
                     f'<span class="mini-text">{esc(cell["text"])}</span></div>')
    count = min(max(len(cells), 1), 6)
    hook = f'<span class="row-hook">{esc(board["hook"])}</span>' if board["hook"] else ''
    body = (f'<div class="minis n-{count}">{"".join(cells)}</div>' if cells
            else '<p class="muted small">Nothing recorded yet.</p>')
    return ('  <article class="row">\n'
            f'    <div class="row-head"><span class="q-id mono">{esc(board["id"])}</span>{hook}'
            f'<h3 class="row-q"><a href="{esc(href)}">{esc(board["text"])}</a></h3></div>\n'
            f'    {body}\n  </article>\n')


def _list_head(tag: str, title: str, lede: str) -> str:
    return ('<div class="list-head">\n'
            f'  <span class="tag tag-dark mono">{esc(tag)}</span>\n'
            f'  <h1 class="display">{esc(title)}</h1>\n'
            f'  <p class="lede">{lede}</p>\n</div>\n')


def _page(view: _View, head: str, bar: str, body: list[str], foot: str, schema: str = "") -> str:
    return "".join([head, bar, f'<main id="main" class="wrap"{schema}>\n', *body, '</main>\n',
                    '<div class="wrap wrap-foot">\n', foot, '</div>\n</body>\n</html>\n'])


HOME_DESCRIPTION = "The same questions, asked again every month, with every answer kept."


def render_home(versions: dict[str, str] | None = None) -> str:
    """`index.html` at the root of a standalone archive: sends the visitor to `archive/index.html`
    with a meta refresh and shows a plain link for a browser that does not follow it. No script."""
    versions = versions or asset_versions()
    head = _head("Answers Over Time", "", versions, HOME_DESCRIPTION)
    title = '  <title>'
    if head.count(title) != 1:
        raise ValueError("the page head must hold exactly one <title>")
    head = head.replace(title, f'  <meta http-equiv="refresh" content="0; url=archive/{FIRST_PAGE}" />\n' + title)
    return (head + '<main id="main" class="wrap">\n<div class="list-head">\n'
            '  <span class="tag tag-dark mono">Read-only archive · static snapshot</span>\n'
            '  <h1 class="display">Answers Over Time</h1>\n'
            f'  <p class="lede"><a href="archive/{FIRST_PAGE}">Open the archive</a></p>\n'
            '</div>\n</main>\n<div class="wrap wrap-foot">\n'
            '<footer class="foot"><span>Nothing is running behind this page.</span>'
            '<span class="mono small">No payments · nothing can be changed from this page</span></footer>\n'
            '</div>\n</body>\n</html>\n')


def _method_models(view: _View) -> str:
    return ('<section id="method" class="method">\n  <h2 class="display">How the record is kept</h2>\n  <div class="method-grid">\n'
            '    <div><strong>Answered alone</strong><span>No model sees another model\'s answer, now or from any earlier check.</span></div>\n'
            '    <div><strong>Same words every time</strong><span>Exactly what we asked is on each question\'s page. We never edit it.</span></div>\n'
            '    <div><strong>Nothing is rewritten</strong><span>An answer is kept as it came. The page may shorten what it shows; '
            'the text is never changed.</span></div>\n'
            '    <div><strong>"Changed" is a plain comparison</strong><span>The text differs from that model\'s answer before, ignoring '
            'letter case, spacing and punctuation at the ends. It does not say which answer is right.</span></div>\n'
            + ('    <div><strong>Asked more than once</strong><span>Some checks ask each question several times, each time on its own. '
               'You see the answer most tries gave; if the tries did not agree it says "Mixed answers", not "Changed".</span></div>\n'
               if view.multi else '')
            + '  </div>\n</section>\n')


def _model_rows(view: _View, questions: list[dict], folder: str) -> list[str]:
    return [_row(_model_board(view, question["id"]), f"{folder}/{question['id']}.html") for question in questions]


def _trending_section(view: _View, heading: str = "Trending", key: str = "") -> list[str]:
    ordered = sorted(view.trending, key=lambda q: (q["added"], view.place[q["id"]][1]), reverse=True)
    out = [f'<section class="group">\n  <div class="group-head"><h2>{esc(heading)}</h2>'
           f'<span class="muted">From what people were searching for. Newest first.</span>{key}</div>\n']
    out += _model_rows(view, ordered, "t")
    if not ordered:
        out.append('  <p class="muted trend-none">No trending question has been added yet.</p>\n')
    out.append('</section>\n')
    return out


def _core_sections(view: _View, key: str = "") -> list[str]:
    out = []
    for group in view.payload["groups"]:
        questions = [q for q in view.questions if q["group"] == group["name"]]
        if not questions:
            continue
        out.append(f'<section class="group">\n  <div class="group-head"><h2>{esc(group["name"])}</h2>'
                   f'<span class="muted">{esc(group["why"])}</span>{key}</div>\n')
        key = ""
        out += _model_rows(view, questions, "q")
        out.append('</section>\n')
    return out


def _core_note(view: _View) -> str:
    total = count_word(len(view.payload["questions"]))
    if view.payload["locked"]:
        note = (f'The list of {total} questions was fixed by the owner on {esc(full_date(view.payload["locked_at"] or ""))} '
                'and does not change.')
    else:
        note = f'This list of {total} questions is a draft. Once the owner fixes it and the archive starts, it does not change.'
    return f'<p class="list-note muted">{note} We check about once a month.</p>\n'


def _trending_note(view: _View) -> str:
    if view.models_demo:
        note = "These sample questions and their dates were written by hand. They are not taken from a real list of trends."
    elif view.trend["locked"]:
        note = ("A trending question never changes and is never removed once it is added. New ones are added over time, "
                "by hand, from a public list of popular searches.")
    else:
        note = ("These questions are drafts. Once the owner adds one for good it never changes and is never removed; "
                "new ones are added over time.")
    return (f'<p class="list-note muted">{note} Questions about a private person, about elections, or asking for '
            'medical, legal or money advice are not added.</p>\n')


def render_index(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                 sibling: str | None = None) -> str:
    """`index.html`, the front page: the chat-app comparison when the payload holds a chat-app run,
    else the trending and the core questions with the models' newest answers."""
    view = _View(payload)
    versions = versions or asset_versions()
    chat = view.chat
    if chat.dates:
        names = [column["name"] for column in chat.columns]
        people = "one human" if chat.human else ""
        apps = f"{count_word(len(chat.labels)).capitalize()} AI app{'' if len(chat.labels) == 1 else 's'}"
        title = f"Same question. {apps}.{' One human.' if people else ''}"
        lede = "Typed in by hand, every month. Tap a question to see who changed their answer."
        body = [_list_head(view.tag("chat"), title, esc(lede)),
                '<section class="group">\n  <div class="group-head"><h2>Newest answers</h2>'
                f'{_key(view, True)}</div>\n']
        shown = [q["id"] for q in view.chat_questions]
        body += [_row(_chat_board(view, question_id), f"c/{question_id}.html") for question_id in shown]
        body.append('</section>\n')
        body.append(f'<p class="list-note muted">{LEFT_OUT} The same words go to every app; '
                    '<a href="chatbots.html">here is how we ask</a>. '
                    'Want the plain models instead? <a href="models.html">Models</a> and <a href="trending.html">Trending</a>.</p>\n')
        return _page(view, _head("Answers Over Time", "../", versions, HOME_DESCRIPTION, _meta(view, "", track="chat")),
                     _bar(view, "../", "", versions, "front", standalone=standalone, sibling=sibling), body,
                     _foot(view, "chat", shown))
    body = [_list_head(view.tag(), "Simple questions, asked again every month.",
                       "We ask AI the same basic things again and again, and keep every answer.")]
    body += _trending_section(view, key=_key(view, False)) + _core_sections(view)
    body += [_core_note(view), _method_models(view)]
    return _page(view, _head("Answers Over Time", "../", versions, HOME_DESCRIPTION, _meta(view, "")),
                 _bar(view, "../", "", versions, "front", standalone=standalone, sibling=sibling), body,
                 _foot(view, "models", [q["id"] for q in view.questions + view.trending]))


def render_models(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                  sibling: str | None = None) -> str:
    """`models.html`: the core question list, asked of the models directly, with their newest answers."""
    view = _View(payload)
    versions = versions or asset_versions()
    body = [_list_head(view.tag(), "The same questions, straight to the models.",
                       "No app in between: we ask each model ourselves and keep every answer.")]
    body += _core_sections(view, key=_key(view, False))
    body += [_core_note(view), _method_models(view)]
    return _page(view, _head("Models · Answers Over Time", "../", versions,
                             "The core questions, asked of AI models directly, with every answer kept.", _meta(view, "")),
                 _bar(view, "../", "", versions, "models", standalone=standalone, sibling=sibling), body,
                 _foot(view, "models", [q["id"] for q in view.questions]))


def render_trending(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                    sibling: str | None = None) -> str:
    """`trending.html`: the trending questions, newest first, with the models' newest answers."""
    view = _View(payload)
    versions = versions or asset_versions()
    body = [_list_head(view.tag(), "Questions from what people search for.",
                       "Now and then we write a question from a topic lots of people looked up, then keep asking it.")]
    body += _trending_section(view, key=_key(view, False))
    body.append(_trending_note(view))
    return _page(view, _head("Trending · Answers Over Time", "../", versions,
                             "Questions written from what many people were searching for, asked of AI models ever since.",
                             _meta(view, "")),
                 _bar(view, "../", "", versions, "trending", standalone=standalone, sibling=sibling), body,
                 _foot(view, "models", [q["id"] for q in view.trending]))


# ---- The question page ------------------------------------------------------------------------------------------

class _Ids:
    """Page-unique ids for the checkbox behind each "More"."""

    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"x{self.n}"


def _more(ids: _Ids, text: str, classes: str, clamp: str, what: str, prop: str = "", lead: str = "") -> str:
    """A text that may be clamped, with a "More" control when it is long enough to be (a checkbox and
    its label: no script). The text is in the page once. `lead` is ready HTML put before the text."""
    body = f'<p class="{classes}{" " + clamp if clamp else ""}"{prop}>{lead}{esc(text)}</p>'
    if not clamp:
        return body
    box = ids.next()
    return (f'<input class="more-box {clamp[6:]}" type="checkbox" id="{box}" aria-label="{esc(what)}" />{body}'
            f'<label class="more {clamp[6:]}" for="{box}"><span class="more-open">More</span><span class="more-close">Less</span></label>')


def _author(column: dict, sample: bool) -> str:
    """The card's name with the answer's author as microdata: an app (and its maker), a model, or, for the
    Human column, an organisation called "Anonymous people" (never a personal name)."""
    name = esc(column["name"])
    if column["kind"] == "human":
        return ('<span class="acard-name mono" itemprop="author" itemscope itemtype="https://schema.org/Organization">'
                f'<meta itemprop="name" content="{"Scripted sample" if sample else "Anonymous people"}" />{name}</span>')
    maker = (f'<span itemprop="creator" itemscope itemtype="https://schema.org/Organization"><meta itemprop="name" '
             f'content="{esc(column["maker"])}" /></span>' if column["maker"] else '')
    return ('<span class="acard-name mono" itemprop="author" itemscope itemtype="https://schema.org/SoftwareApplication">'
            f'<span itemprop="name">{name}</span>{maker}</span>')


def _history(board: dict, column: dict) -> str:
    groups = _groups(board, column["key"])
    if not groups:
        return ""
    word = "different answer" if column["kind"] == "human" else "change"
    items = "".join(f'<li><span class="hist-when mono">{esc(_span(group["first"], group["last"]))} · {_plural(group["checks"], "check")}</span>'
                    f'<span class="hist-text">{esc(group["text"])}</span></li>' for group in reversed(groups))
    count = _plural(len(groups) - 1, word) if len(groups) > 1 else f"no {word} yet"
    if len(groups) < 2:
        return ""   # one answer, never changed: the card already shows all there is
    return (f'<details class="hist"><summary>History · {count}</summary>'
            f'<ol class="hist-list" reversed>{items}</ol></details>')


def _card(board: dict, column: dict, period: dict, cell: dict, ids: _Ids, newest: bool) -> str:
    """One answer of one month as a card. Visible: the name, the state, the answer, what it was before,
    the app's own attribution line, quoted / summarised for a human answer. Everything else is metadata."""
    human = column["kind"] == "human"
    rec = record(board, column, period, cell)
    tags = []
    if cell["state"] == "changed":
        tags.append(f'<span class="tag-changed mono">{"Different answer this month" if human else "Changed"}</span>')
    if cell["state"] == "mixed":
        tags.append('<span class="tag-mixed mono">Mixed answers</span>')
    tags += [f'<span class="tag-cond mono">{esc(tag)}</span>' for tag in cell["tags"]]
    if human and cell["form"]:
        tags.append(f'<span class="tag-form mono">{esc(cell["form"])}</span>')
    out = [f'    <article class="acard{" is-changed" if cell["state"] == "changed" else ""}{" is-mixed" if cell["state"] == "mixed" else ""}'
           f'{" kind-human" if human else ""}"{data_attrs(rec)} itemprop="suggestedAnswer" itemscope itemtype="https://schema.org/Answer">'
           f'<div class="acard-head">{_author(column, board["demo"])}'
           + (f'<span class="tags">{"".join(tags)}</span>' if tags else '') + '</div>',
           _more(ids, cell["text"], "ans", _clamp(cell["text"]), f"Show the whole answer of {column['name']}", ' itemprop="text"')]
    if cell["others"]:
        out.append('<ul class="others" aria-label="Other answers from the same check">'
                   + "".join(f'<li><span class="mono">{esc(other["count"])} of {esc(rec["conditions"].get("samples", ""))}</span> '
                             f'{esc(other["text"])}</li>' for other in cell["others"]) + '</ul>')
    previous = _previous(board, column["key"], cell)
    if cell["state"] == "changed" and previous:
        lead = ('<span class="before-when mono">Before · '
                f'<time datetime="{esc(previous[1]["collected"])}">{esc(previous[0]["label"])}</time></span>')
        out.append('<div class="before">'
                   + _more(ids, previous[1]["text"], "before-text", "clamp c-s c-m c-l" if len(previous[1]["text"]) > BEFORE_CLAMP else "",
                           f"Show the whole earlier answer of {column['name']}", lead=lead) + '</div>')
    if column["attribution"]:
        out.append(f'<p class="credit mono">{esc(column["attribution"])}</p>')
    if newest:
        out.append(_history(board, column))
    out.append(f'<meta itemprop="dateCreated" content="{esc(cell["collected"])}" /></article>\n')
    return "".join(out)


def _panel(board: dict, period: dict, ids: _Ids, newest: bool, number: int) -> str:
    cards, notes = [], []
    left_out = 0
    for column in board["columns"]:
        cell = board["cells"].get((column["key"], period["key"]))
        if cell is None:
            continue
        if cell["shown"]:
            cards.append(_card(board, column, period, cell, ids, newest))
        elif board["track"] == "chat":
            left_out += 1
        else:
            cards.append(f'    <article class="acard acard-none"><div class="acard-head"><span class="acard-name mono">{esc(column["name"])}</span></div>'
                         f'<p class="ans-none">{esc(cell["why_not"])}</p></article>\n')
    if left_out:
        notes.append(f"{_replies(left_out)} left out")
    count = min(max(len(cards), 1), 6)
    when = full_date(period["date"])
    body = (f'  <div class="cards n-{count}">\n{"".join(cards)}  </div>\n' if cards
            else '  <p class="muted">Nothing to show for this month.</p>\n')
    return (f'<section class="panel" id="m-{esc(period["key"])}" aria-label="{esc(period["label"])}">\n'
            f'  <h2 class="panel-when"><time datetime="{esc(period["date"])}">{esc(period["label"])}</time>'
            f'<span class="muted">asked {esc(when)}{" · " + esc(" ".join(notes)) if notes else ""}</span></h2>\n'
            + body + '</section>\n')


def _table(board: dict) -> str:
    """"What changed": months down, answerers across. Only a changed (or first) answer shows its text; an
    unchanged one says "same as before". A real table; on a phone its rows become blocks (stylesheet)."""
    if not board["periods"] or not board["columns"]:
        return ""
    head = "".join(f'<th scope="col" role="columnheader">{esc(column["name"])}</th>' for column in board["columns"])
    rows = []
    for period in reversed(board["periods"]):
        tds = []
        for column in board["columns"]:
            cell = board["cells"].get((column["key"], period["key"]))
            label = f' data-label="{esc(column["name"])}"'
            if cell is None:
                tds.append(f'<td role="cell" class="t-none"{label}>not asked</td>')
            elif not cell["shown"]:
                why = "left out" if board["track"] == "chat" else cell["why_not"]
                tds.append(f'<td role="cell" class="t-none"{label}>{esc(why)}</td>')
            elif cell["state"] == "same":
                tds.append(f'<td role="cell" class="t-same"{label}{data_attrs(record(board, column, period, cell))}>same as before</td>')
            else:
                word = {"answered": "first answer", "mixed": "mixed answers",
                        "changed": "different answer" if column["kind"] == "human" else "changed"}[cell["state"]]
                long = " t-long" if len(cell["text"]) > TABLE_CLAMP else ""
                tds.append(f'<td role="cell" class="t-{cell["state"]}"{label}{data_attrs(record(board, column, period, cell))}>'
                           f'<span class="t-word mono">{word}</span><span class="t-text{long}">{esc(cell["text"])}</span></td>')
        rows.append(f'    <tr role="row"><th scope="row" role="rowheader"><time datetime="{esc(period["date"])}">{esc(period["label"])}</time></th>'
                    + "".join(tds) + '</tr>\n')
    return ('<section class="changes">\n  <h2>What changed</h2>\n'
            '  <table class="history" role="table">\n'
            f'    <caption>Every check of {esc(board["id"])}, newest first. Only a new or different answer is written out.</caption>\n'
            f'    <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Month</th>{head}</tr></thead>\n'
            '    <tbody role="rowgroup">\n' + "".join(rows) + '    </tbody>\n  </table>\n</section>\n')


def _sources(board: dict) -> str:
    """The closed "Where each answer came from" block: per answerer, per month, everything recorded about
    how the answer got here. This is where the labels that left the cards can still be read by a person."""
    out = []
    for column in board["columns"]:
        series = _series(board, column["key"])
        if not series:
            continue
        who = column["name"] + (f" · {column['maker']}" if column["maker"] else "") + (f" · {column['meta']}" if column.get("meta") else "")
        items = "".join(f'<li><span class="mono">{esc(period["label"])}</span> {esc(" · ".join(cell["facts"]) or "nothing recorded")}</li>'
                        for period, cell in reversed(series))
        out.append(f'<div class="src"><strong>{esc(who)}</strong><ul>{items}</ul></div>')
    return "".join(out)


def _question_page(view: _View, board: dict, versions: dict[str, str], *, standalone: bool, sibling: str | None) -> str:
    chat = board["track"] == "chat"
    question_id = board["id"]
    ids = _Ids()
    title_row = (f'<span class="q-id mono">{esc(question_id)}</span>'
                 + (f'<span class="hook">{esc(board["hook"])}</span>' if board["hook"] else
                    "".join(f'<span class="{kind}">{esc(text)}</span>' for kind, text in board["chips"]))
                 + f'<span class="tag tag-dark mono">{esc(view.tag("chat" if chat else "models"))}</span>'
                 + f'<a class="share" href="../card/{esc(question_id)}.html">Share card</a>')
    body = ['<div class="q-hero">\n'
            f'  <div class="chips">{title_row}</div>\n'
            f'  <h1 class="display display-q{" q-long" if len(board["text"]) > 40 else ""}" itemprop="name">{esc(board["text"])}</h1>\n'
            f'  <meta itemprop="text" content="{esc(board["prompt"])}" />\n</div>\n']
    periods = list(reversed(board["periods"]))[:MAX_PANELS]
    if periods and board["columns"]:
        body.append('<section class="months" aria-label="Answers, month by month">\n')
        if len(periods) > 1:
            body.append("".join(f'<input class="m-radio" type="radio" name="month" id="pick-{n}"{" checked" if n == 1 else ""} />\n'
                                for n in range(1, len(periods) + 1)))
            body.append('<div class="month-strip" role="group" aria-label="Choose a month">'
                        + "".join(f'<label for="pick-{n}">{esc(period["label"])}</label>' for n, period in enumerate(periods, 1))
                        + '</div>\n')
        body += [_panel(board, period, ids, n == 1, n) for n, period in enumerate(periods, 1)]
        body.append('</section>\n')
        if chat:
            line = LEFT_OUT + (" A changed answer may come from how it was asked that day, not from the app."
                               if any(cell["tags"] for cell in board["cells"].values()) else "")
            cards = "".join(f'<a href="../card/{esc(question_id)}-{esc(column["key"])}.html">{esc(column["name"])}</a>'
                            for column in board["columns"] if _latest(board, column["key"]))
            body.append(f'<p class="under muted">{esc(line)}</p>\n'
                        f'<p class="card-links small"><span class="muted">Share cards:</span>'
                        f'<a href="../card/{esc(question_id)}.html">Everyone</a>{cards}</p>\n')
        else:
            body.append(f'<p class="card-links small"><a href="../card/{esc(question_id)}.html">Share card</a></p>\n')
        body.append(_table(board))
    else:
        body.append('<p class="muted">No answers have been recorded for this question yet.</p>\n')
    kinds = {cell["method"] for cell in board["cells"].values()}
    explain = []
    if SAMPLE in kinds:
        explain.append(f"<strong>{SAMPLE_LABEL}</strong>: the answer and its date were written by hand as a sample. Nothing was asked.")
    if METHODS["manual"] in kinds:
        explain.append("<strong>Typed in by hand</strong>: a person typed the question into the app and pasted the reply. "
                       "There's no receipt, so you're trusting whoever typed it.")
    if HUMAN_METHOD in kinds:
        explain.append("<strong>Written by people</strong>: collected by hand. <em>quoted</em> is one person's words; "
                       "<em>summarised</em> is our short summary of several replies. No names.")
    if METHODS["engine"] in kinds:
        explain.append("<strong>We asked it ourselves</strong>: this server called the model and kept the receipt. "
                       "<em>run locally</em> or <em>via</em> a name says where the model ran; that is not the same as its maker's own chat app.")
    if METHODS["api"] in kinds:
        explain.append("<strong>Sent in by someone else, not checked</strong>: an outside program sent the answer and named its own version.")
    if any(cell["state"] == "mixed" for cell in board["cells"].values()):
        explain.append("<strong>Mixed answers</strong>: we asked several times in one check and the tries did not all agree, then or the "
                       "time before. You see the answer most tries gave; it is not counted as a change.")
    body.append('<section class="more-info">\n'
                f'  <details><summary>Why we ask this</summary><p>{esc(board["why"])}</p></details>\n'
                f'  <details><summary>Exactly what we asked</summary><pre class="prompt mono">{esc(board["prompt"])}</pre>'
                f'<p class="muted small">{esc(board["prompt_note"])}</p></details>\n'
                '  <details><summary>Where each answer came from</summary>'
                + "".join(f'<p class="small">{line}</p>' for line in explain)
                + f'<div class="srcs">{_sources(board)}</div>'
                f'<p class="small"><a href="../data/{esc(question_id)}.json">The same record as data</a></p></details>\n'
                '</section>\n')
    back = {"chat": '<a class="back" href="../index.html">All chat-app questions</a>',
            "trending": '<a class="back" href="../trending.html">All trending questions</a>',
            "core": f'<a class="back" href="../models.html">All {esc(count_word(len(view.questions)))} questions</a>'}[board["track"]]
    track = "chat" if chat else "models"
    current = "front" if chat else "trending" if board["track"] == "trending" else "models"
    return _page(view, _head(f"{board['text']} · Answers Over Time", "../../", versions, board["text"],
                             _meta(view, "../", board=board, track=track)),
                 _bar(view, "../../", "../", versions, current, standalone=standalone, sibling=sibling), body,
                 _foot(view, track, [question_id], back), ' itemscope itemtype="https://schema.org/Question"')


def render_question(payload: dict, question_id: str, versions: dict[str, str] | None = None, *,
                    standalone: bool = False, sibling: str | None = None) -> str:
    """One question of any track: `q/Q01.html`, `t/T001.html` or `c/C001.html` (one layout). Raises
    KeyError for an unknown question and for one the owner left off the pages."""
    view = _View(payload)
    return _question_page(view, _board(view, question_id), versions or asset_versions(), standalone=standalone, sibling=sibling)


CHATBOTS_DESCRIPTION = "How the chat apps are asked: by hand, the same words every month, every reply kept."


def render_chatbots(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                    sibling: str | None = None) -> str:
    """`chatbots.html`: how the chat apps (and people) are asked, the conditions recorded on each app's
    latest date, and, on a real archive only, the owner's one-line aside. Names are shown as text only."""
    view = _View(payload)
    chat = view.chat
    versions = versions or asset_versions()
    lede = ("A person types each question into each app, in a new chat, and pastes the reply here. No software collects anything."
            + (" On this sample site the apps are made up and the replies are ours." if chat.demo else ""))
    body = [_list_head(view.tag("chat") if chat.dates else "No chat app asked yet", "How we ask the chat apps.", esc(lede))]
    if chat.dates and chat.columns:
        body.append('<section class="group">\n  <div class="group-head"><h2>How each one was asked</h2>'
                    '<span class="muted">As recorded the last time.</span></div>\n')
        for column in chat.columns:
            key = column["key"]
            if column["kind"] == "human":
                dates = sorted(chat.collection)
                if not dates:
                    continue
                collection = chat.collection[dates[-1]]
                how = (f'<span>{esc(column["meta"])}</span>'
                       f'<span class="muted small">From: {esc(collection["source"])} · collected {esc(full_date(collection["collected_on"]))} · '
                       f'{esc(_plural(len(dates), "collection"))} in all · each answer is marked quoted or summarised · no names</span>')
                body.append(f'  <div class="app-row"><div class="app-name"><strong>{esc(column["name"])}</strong></div>'
                            f'<div class="app-how">{how}</div></div>\n')
                continue
            dates = [d for d in chat.dates if (key, d) in chat.conditions]
            conditions = chat.conditions[(key, dates[-1])]
            body.append('  <div class="app-row"><div class="app-name"><strong>' + esc(column["name"]) + '</strong>'
                        f'<span class="muted small">{esc(column["maker"])}</span></div>'
                        f'<div class="app-how"><span>{esc(conditions_line(conditions))}</span>'
                        f'<span class="muted small">The app showed: {esc(conditions["shown_model"])} · '
                        f'asked {esc(full_date(dates[-1]))} · {esc(_plural(len(dates), "time"))} in all · '
                        f'{esc(SOURCE_LABELS["demo" if chat.demo else "manual"])}</span>'
                        + (f'<span class="muted small">{esc(conditions["note"])}</span>' if conditions["note"] else '')
                        + (f'<span class="mono small">{esc(column["attribution"])}</span>' if column["attribution"] else '')
                        + '</div></div>\n')
        body.append('</section>\n')
        if chat.real:
            from . import archive_chatbots   # lazy: only a real chat-app page reads the owner's aside
            aside = archive_chatbots.aside()
            if aside:
                body.append(f'<p class="aside"><span class="aside-mark mono">Joke</span>{esc(aside)}</p>\n')
    else:
        body.append('<section class="group">\n  <p class="muted trend-none">No chat app has been asked yet.</p>\n</section>\n')
    body.append('<section id="method" class="method">\n  <h2 class="display">The rules we keep</h2>\n  <div class="method-grid">\n'
                '    <div><strong>By hand</strong><span>A person types every question, in a new chat each time. Nothing here '
                'talks to these apps.</span></div>\n'
                '    <div><strong>Same words</strong><span>Exactly what we asked is on each question\'s page. It asks for one short '
                'sentence, so the replies are short.</span></div>\n'
                '    <div><strong>Copied, not edited</strong><span>The whole reply is pasted as it came. The page may shorten what it '
                'shows; the words are never changed.</span></div>\n'
                '    <div><strong>Only real answers shown</strong><span>If an app refuses or dodges, we keep that on file and leave it '
                'off the page.</span></div>\n'
                '    <div><strong>You\'re trusting us</strong><span>There\'s no receipt for a reply typed in by hand. We keep '
                'screenshots; you can\'t check them.</span></div>\n'
                '    <div><strong>It depends on the day</strong><span>Account, country, search on or off: all of it can move a reply. '
                'We write it down, and say so when it differs.</span></div>\n'
                '    <div><strong>Humans too</strong><span>The Human answers were written by people and picked by hand: one person\'s '
                'words (quoted) or our summary of several (summarised). No names.</span></div>\n'
                '    <div><strong>Not affiliated</strong><span>We\'re not connected to the companies named. Their names say which app '
                'was asked, nothing more.</span></div>\n'
                '  </div>\n</section>\n')
    return _page(view, _head("How it works · Answers Over Time", "../", versions, CHATBOTS_DESCRIPTION, _meta(view, "", track="chat")),
                 _bar(view, "../", "", versions, "chatbots", standalone=standalone, sibling=sibling), body, _foot(view, "chat", []))


# ---- Share cards --------------------------------------------------------------------------------------------------

def _card_size(text: str) -> str:
    """The size classes of a card's question, by its length: one for a card of up to three panels and one
    (f1-f3) for a card of four, where the question has less room. Chosen so that the question is never cut."""
    length = len(text)
    return (("xl" if length <= 26 else "l" if length <= 48 else "m" if length <= 90 else "s")
            + (" card-q-f1" if length <= 34 else " card-q-f2" if length <= 84 else " card-q-f3"))


def _card_page(view: _View, board: dict, panels: list[tuple[dict, dict, dict]], versions: dict[str, str], *, title: str,
               kicker: str, one: bool, track: str, extra: str = "", empty: str = "No answers recorded yet.") -> str:
    """A 9:16 share card. Laid out for a 1080 by 1920 image that a video app covers in part: the question,
    the names, the answers and the one honesty line sit inside x 65-940, y 270-1440 (`.card-safe`); the
    wordmark is above and the small print below. At any other window size the same page is ordinary
    flowing text that can be read and zoomed. An answer is clamped to three lines on the image; when one
    may be cut the card says "full answer on the site". Recorded text is never edited."""
    sample = board["demo"]
    question_id = board["id"]
    limit = CARD_CLAMP_CHARS[min(max(len(panels), 1), 4)]
    cut = any(len(cell["text"]) > limit for _column, _period, cell in panels)
    out = [_head(title, "../../", versions, board["text"], _meta(view, "../", board=board, track=track)).replace(
               "<body>", '<body class="card-page">'),
           f'<main id="main" class="card card-safe{" card-four" if len(panels) > 3 else ""}" data-question-id="{esc(question_id)}" '
           f'data-provenance="{SAMPLE if sample else "recorded"}"><div class="card-in">\n'
           f'  <header class="card-top"><a class="card-brand" href="../{esc(view.page(question_id))}">'
           f'<img class="card-mark" src="../../static/archive-mark.svg?v={versions["archive-mark.svg"]}" alt="" width="57" height="24" />'
           'Answers Over Time</a>'
           f'<span class="card-kicker">{esc(kicker)}</span></header>\n'
           '  <div class="card-body">\n'
           + (f'  <p class="card-hook">{esc(board["hook"])}</p>\n' if board["hook"] else '')
           + f'  <h1 class="card-q card-q-{_card_size(board["text"])}">{esc(board["text"])}</h1>\n'
           f'  <div class="card-panels {"one" if one else "n-" + str(max(len(panels), 1))}">\n']
    for column, period, cell in panels:
        human = column["kind"] == "human"
        changed = cell["state"] == "changed"
        word = "differs" if human else "changed"
        form = f" · {cell['form']}" if human and cell["form"] else ""
        credit = f"{column['attribution']} · " if column["attribution"] else ""
        previous = _previous(board, column["key"], cell) if changed else None
        before = (f'<p class="card-before"><span class="mono">Before · {esc(previous[0]["label"])}</span> {esc(previous[1]["text"])}</p>'
                  if previous and one else '')
        out.append(f'    <section class="card-panel{" card-panel-changed" if changed else ""}{" card-panel-human" if human else ""}"'
                   f'{data_attrs(record(board, column, period, cell))}>'
                   f'<div class="card-meta mono"><span class="card-label">{esc(column["name"])}</span>'
                   f'<span class="card-when">{esc(credit)}{esc(period["label"])}{esc(form)}{" · " + word if changed else ""}'
                   f'{" · mixed answers" if cell["state"] == "mixed" else ""}</span></div>'
                   f'<p class="card-answer">{esc(cell["text"])}</p>{before}</section>\n')
    if not panels:
        out.append(f'    <section class="card-panel"><p class="card-answer card-empty">{esc(empty)}</p></section>\n')
    honesty = view.tag(track)
    if not sample and track == "chat":
        honesty = CHAT_CARD_FOOT
    small = f"{question_id} · same question every month · read-only · no payments" + (f" · {CARD_FULL}" if cut else "")
    out.append('  </div>\n'
               f'  <p class="card-honest mono">{esc(honesty)}</p>\n'
               '  </div>\n'
               f'  <footer class="card-foot mono"><span>{esc(small)}</span>{extra}</footer>\n'
               '</div></main>\n</body>\n</html>\n')
    return "".join(out)


def _card_models(board: dict) -> list[tuple[dict, dict, dict]]:
    """Up to CARD_MODELS answerers for the card, each with its latest answer; those that changed most
    recently come first in the choice, the page order is kept."""
    found = []
    for order, column in enumerate(board["columns"]):
        latest = _latest(board, column["key"])
        if latest is None:
            continue
        changed = max((n for n, (_p, cell) in enumerate(_series(board, column["key"])) if cell["state"] == "changed"), default=-1)
        found.append((-changed, order, column, *latest))
    chosen = sorted(found, key=lambda item: item[:2])[:CARD_MODELS]
    return [(column, period, cell) for _c, _o, column, period, cell in sorted(chosen, key=lambda item: item[1])]


def render_card(payload: dict, question_id: str, versions: dict[str, str] | None = None) -> str:
    """`card/<ID>.html`. For a model question: the latest answer of up to three models and, for one that
    changed, the answer before. For a chat-app question: the latest answer of every app and of Human
    (the same card as `render_chatbot_card(payload, id)`). No script, no animation."""
    view = _View(payload)
    versions = versions or asset_versions()
    board = _board(view, question_id)
    if board["track"] == "chat":
        return render_chatbot_card(payload, question_id, ALL_CARD, versions)
    panels = _card_models(board)
    return _card_page(view, board, panels, versions, title=f"{board['text']} · card · Answers Over Time", kicker=board["kicker"],
                      one=False, track="models")


def render_chatbot_card(payload: dict, question_id: str, which: str = ALL_CARD, versions: dict[str, str] | None = None) -> str:
    """A share card of the chat-app track. `which` is "all" (`card/<ID>.html`: the latest answer of up to
    three apps and of Human, whose panel is outlined, not filled) or one column's key
    (`card/<ID>-<app label>.html`, `card/<ID>-human.html`: that one's latest answer at a larger size).
    Never the owner's aside. Raises KeyError for a question or a column that is not in the track."""
    view = _View(payload)
    chat = view.chat
    versions = versions or asset_versions()
    single = which != ALL_CARD
    if question_id not in chat.by_id or not view.shown(question_id) or (single and which not in chat.name):
        raise KeyError(question_id)
    board = _chat_board(view, question_id)
    columns = [_column(board, which)] if single else board["columns"]
    panels = [(column, *found) for column in columns for found in [_latest(board, column["key"])] if found]
    if not single and len(panels) > CARD_APPS:      # keep Human on the card: it is the fourth panel
        people = [panel for panel in panels if panel[0]["kind"] == "human"]
        panels = [panel for panel in panels if panel[0]["kind"] != "human"][:CARD_APPS - len(people)] + people
    name = chat.name[which] if single else "everyone"
    return _card_page(view, board, panels, versions, title=f"{board['text']} · {name} · card · Answers Over Time",
                      kicker="Chat apps", one=single, track="chat", empty="No answers recorded yet.")


# ---- Data files: the same answers for machines ------------------------------------------------------------------

README = """Answers Over Time: data files
=============================

These files hold the same answers as the pages next to them, for programs to read.
They are built from the same reviewed record the pages are built from (archive.json at the
site root) and hold nothing that is not in it.

Files
-----
answers.jsonl   One answer per line, each line a complete JSON object.
<ID>.json       One question (Q01, T004, C003 ...): the question, exactly what was asked,
                and every answer to it.
README.txt      This file.

What an answer is
-----------------
An answer with provenance "ai-generated" is the output of an AI system, recorded verbatim.
It was not edited, shortened or corrected. It may be wrong. It is not advice.

An answer with provenance "human-written" was written by people and collected by hand.
human_form says how: "quoted" is one person's words, copied exactly; "summarised" is the
archive owner's short summary of several replies. There are no names in it.

An answer with provenance "scripted-sample" is NOT a real answer. It was written as a
sample to show how the pages work. No AI and no person was asked. Never treat a
scripted-sample record as something an AI or a person said. Every record of a sample
site carries this value, in every line.

Fields
------
schema              The version of this format: %(schema)s
id                  A stable id of this answer: question / answerer / check.
question_id         Q.. (core list), T... (trending list) or C... (chat-app list).
question            The question in plain words.
prompt              Exactly what was asked, character for character.
answerer            The answerer's label. answerer_name is the name shown on the pages.
answerer_kind       chat-app | model | human
maker               The company behind a chat app ("" when not recorded).
version             The version name the app showed, or the model version asked ("" when none).
collected           The date of the check, YYYY-MM-DD. period is the label the pages use.
method              typed-by-hand       a person typed the question into a chat app and pasted the reply
                    asked-by-server     the archive's own program called the model
                    sent-in-unverified  an outside program sent the answer; nothing about it was checked
                    collected-by-hand   answers written by people, collected by a person
                    scripted-sample     a sample; nothing was asked
provenance          ai-generated | human-written | scripted-sample
human_form          quoted | summarised (human answers only; otherwise null)
state               answered   the first answer from this answerer
                    same       the same as its answer before
                    changed    the text differs from its answer before
                    mixed      asked several times in one check; the tries did not all agree
                    refused, no-answer, hidden   kept on file, not shown on the pages
shown               true when the pages show this answer as a card.
text                The answer in full (null when there is none). The pages may shorten what
                    they display; this is the whole text.
changed             true / false against the answer before; null for a first answer.
previous_id, previous_collected, previous_text
                    The answer this one is compared with (previous_text only when it changed).
conditions          What was recorded about how it was asked (account, search, country ... for a
                    chat app; source and date for human answers; endpoint name and sample counts
                    for a model). Never a web address, a key, a file path or a username.
attribution         A line the app's maker asks to be shown with its output ("" when none).
lang                The language of the text.
notice              A short notice that travels with every record.

"changed" is a plain text comparison (letter case, spacing and punctuation at the ends are
ignored). It does not say which answer is right, and a chat app's reply can change because
of how it was asked that day.

Product names belong to their owners. This archive is independent and is not affiliated
with, sponsored by or endorsed by them.
""" % {"schema": DATA_SCHEMA}


def _dump(node: object) -> str:
    return json.dumps(node, ensure_ascii=True, sort_keys=False, separators=(",", ":"))


def question_data(view: _View, question_id: str) -> dict:
    """`data/<ID>.json` as a dict: the question, exactly what was asked, and every answer as a record."""
    board = _board(view, question_id)
    answers = [record(board, column, period, board["cells"][(column["key"], period["key"])])
               for period in board["periods"] for column in board["columns"] if (column["key"], period["key"]) in board["cells"]]
    return {"schema": DATA_SCHEMA, "content": SAMPLE if board["demo"] else "recorded", "notice": DATA_NOTICE,
            "question_id": question_id, "question": board["text"], "prompt": board["prompt"], "hook": board["hook"],
            "track": board["track"],
            "answerers": [{"answerer": column["key"], "name": column["name"], "kind": column["kind"], "maker": column["maker"]}
                          for column in board["columns"]],
            "answers": answers}


def render_data(payload: dict) -> dict[str, str]:
    """The data files, keyed by their path under `archive/`: `data/<ID>.json` per shown question,
    `data/answers.jsonl` (every answer, one per line) and `data/README.txt`. ASCII-escaped JSON."""
    view = _View(payload)
    out: dict[str, str] = {}
    lines: list[str] = []
    for question in view.chat_questions + view.questions + view.trending:
        data = question_data(view, question["id"])
        out[f"data/{question['id']}.json"] = json.dumps(data, ensure_ascii=True, indent=1) + "\n"
        lines += [_dump(answer) for answer in data["answers"]]
    out["data/answers.jsonl"] = "".join(line + "\n" for line in lines)
    out["data/README.txt"] = README
    return out


def render_data_file(payload: dict, name: str) -> str:
    """One data file by its name (`C001.json`, `answers.jsonl`, `README.txt`); KeyError when there is none."""
    if not DATA_NAME.fullmatch(name):
        raise KeyError(name)
    if name == "README.txt":
        return README
    if name == "answers.jsonl":
        return render_data(payload)["data/answers.jsonl"]
    return json.dumps(question_data(_View(payload), name[:-5]), ensure_ascii=True, indent=1) + "\n"


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
    """Every archive page and data file, keyed by its path under `archive/`. A hidden question (`HIDDEN`)
    has no page, no card and no data file."""
    versions = asset_versions()
    view = _View(payload)
    options = {"standalone": standalone, "sibling": sibling if standalone else None}
    pages = {"index.html": render_index(payload, versions, **options), "models.html": render_models(payload, versions, **options),
             "trending.html": render_trending(payload, versions, **options),
             "chatbots.html": render_chatbots(payload, versions, **options)}
    for folder, questions in (("q", view.questions), ("t", view.trending), ("c", view.chat_questions)):
        for question in questions:
            board = _board(view, question["id"])
            pages[f"{folder}/{question['id']}.html"] = _question_page(view, board, versions, **options)
            pages[f"card/{question['id']}.html"] = render_card(payload, question["id"], versions)
            if folder == "c":
                for column in board["columns"]:
                    pages[f"card/{question['id']}-{column['key']}.html"] = render_chatbot_card(payload, question["id"], column["key"], versions)
    pages.update(render_data(payload))
    return pages
