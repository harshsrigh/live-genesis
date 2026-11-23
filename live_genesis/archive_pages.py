"""Static HTML for Answers Over Time: the front page, the list pages, one page per question, one
portrait share card per question, and the machine-readable data files that go with them.

Pure functions from the public payload (archive.public_payload, or a reviewed
published/archive.json) to text. No database, no model, no JavaScript, no inline style or script:
the pages work under the site's Content-Security-Policy and with scripts off. Every piece of text
that reaches a page goes through `esc`; model output is untrusted. All links are relative, so the
same pages work in a static snapshot under any sub-path and in the local web app.

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
  `card/<ID>-<app label>.html`, `card/<ID>-human.html`: 9:16 share cards. A card holds at most four bubbles:
  more answerers go to further cards (`card/<ID>-2.html` ...), and with several answerer groups every
  group has its own (`card/<ID>-<group id>.html`, `card/<ID>-<group id>-2.html`). Text is never made smaller.

The look ("Group chat", owner's choice): each question is a thread in a pane and each answerer a bubble
with a mark (a coloured shape holding a short initial) and its name; the Human answers from the right in
a dark bubble; a changed answer has the earlier one directly above it, dashed and struck through, and
the word "Changed". Nothing assumes a number of answerers: a thread renders as many as the payload
holds. An answerer's colour and the shape of its mark come from its POSITION in its thread (`_dress`),
never from its name and never from a brand; two answerers of one thread never look the same.

Groups: the chat-app track's answerers belong to groups (`chatbots.groups`; "AI chat apps" today). With
one group no group name appears anywhere. With several, the front page has a script-free group switch
(radio inputs and labels), a question page shows one thread per group and the Human in a thread of its
own, and the headline counts what the payload holds.
* `data/<ID>.json`, `data/answers.jsonl`, `data/README.txt`: the same answers for machines.

What is VISIBLE and what is metadata: the short honesty labels stay visible, once per page or card
(AI-generated; may be wrong; not affiliated; an app's own attribution line; the sample label on
scripted data; Human with quoted / summed up; Changed and the Before line; the one line that refusals
are left out). Nothing published of the chat-app track says how a reply was collected (owner decision). Everything else about an answer (version shown,
account, search, country, endpoint, digest, sample counts ...) is in `data-*` attributes and
schema.org microdata on the answer's element, in the `<meta name="aot:...">` tags of the page head,
in the data files, and for people inside the closed "The details behind each answer" block. A visible
honesty label is never moved into metadata only.

Hidden questions (`HIDDEN`, owner's decision): a question listed there has no page, no card and no data
file and is in no list; the record keeps it. Nothing is hidden now.

Chat apps and Human (the optional `chatbots` block; archive_chatbots.py): replies a person collected
by hand. Never mixed into a model's grid or timeline. A refusal, a non-answer or a reply the content
check withheld is never shown as a card, and a refused reply's words are in no published file; the
page says once, in its footer, "When an app refuses or dodges, we leave it out."
App names are plain text, never a logo, and never the largest thing on a page or card.

Standalone: `render_site(payload, standalone=True)` is the archive published on its own (snapshot.py
`--archive-only`), and it is the chat-app track ONLY (owner decision): `index.html`, `chatbots.html`,
`c/`, the chat-app cards and the chat-app data files. No `models.html`, `trending.html`, `q/`, `t/`,
model card or model data file is made, and no page links to one. The local web app
keeps every page. `sibling` (standalone only) is the relative address of the other published
snapshot as seen from the site root (`demo/` or `../`); the bar links its archive.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import quote

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
SAMPLE_LABEL = SOURCE_LABELS["demo"]    # the one visible sample label of a page or card
SAMPLE_WORD = "sample"                  # per line inside the closed "The details behind each answer" block
HIDDEN_WORDS = "Hidden: broke our content rules"
LEFT_OUT = "When an app refuses or dodges, we leave it out."   # said once per page, in the footer
ASKED_DIFFERENTLY = "Asked a bit differently"     # an answer whose recorded settings differ from that app's time before
DAY_NOTE = "A new answer can come from how we asked that day, not from the app changing its mind."
NO_ANSWERS = "No answers yet"
# How a human answer is worded on a page. The data keeps the recorded values (quoted | summarised).
FORM_WORDS = {"quoted": "quoted", "summarised": "summed up"}
DATA_SCHEMA = "answers-over-time-data/1"
DATA_NOTICE = ("Answers Over Time. AI answers are recorded verbatim and may be wrong; they are not advice. Text marked "
               "human-written was written by people. Records marked scripted-sample were written "
               "as samples: no AI and no person was asked, and they must not be treated as real answers. Product names "
               "belong to their owners; this archive is not affiliated with them.")
ALL_CARD = "all"               # render_chatbot_card: the card with every app and Human
HUMAN = "human"                # the Human column's key and its card suffix (archive_chatbots.HUMAN_LABEL)
CARD_APPS = 4                  # bubbles on one chat-app card; more answerers go to a further card, never to smaller text
CARD_NAME = re.compile(r"(C[0-9]{3})-([a-z][a-z0-9-]{1,23}|[1-9][0-9]?)")
PALETTE = 8                    # tint/ink pairs in the stylesheet (.who-1 ... .who-8)
SHAPES = ("", " av-b", " av-c")   # the mark's shape for positions 1-8, 9-16, 17-24 of one thread
WIDE_THREAD = 5                # from this many bubbles a thread reads in two columns on a wide screen, and the table in blocks
CHAT_ID = re.compile(r"C[0-9]{3}")
DATA_NAME = re.compile(r"(?:Q[0-9]{2}|T[0-9]{3}|C[0-9]{3})\.json|answers\.jsonl|README\.txt")
CHAT_CARD_FOOT = "AI-generated · not affiliated · may be wrong"
CARD_FULL = "full answer on the site"
# Whether an answer is cut on the 9:16 picture is worked out here, because a page carries no script: the text is
# wrapped as the browser would wrap it, using the bundled Inter's advance widths (weight 500, in em, measured in
# Chromium on 20 Nov 2026) and the room an answer has on a card of 1-4 panels (line width in em, lines of its clamp;
# both follow from archive.css and were measured the same way). An unknown character counts as wide and 1% of each
# line is held back, so the estimate errs towards saying "cut".
CARD_FIT = {1: (14.21, 7), 2: (17.71, 4), 3: (19.74, 3), 4: (22.28, 3)}
CARD_FIT_SLACK = 0.99
CARD_WIDE = 1.25
_W = dict(zip(
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    (0.265, 0.305, 0.495, 0.64, 0.645, 0.995, 0.655, 0.315, 0.37, 0.37, 0.655, 0.67, 0.305, 0.46, 0.305, 0.37,
     0.645, 0.415, 0.615, 0.625, 0.655, 0.605, 0.63, 0.57, 0.63, 0.63, 0.305, 0.315, 0.67, 0.67, 0.67, 0.525, 0.98,
     0.71, 0.655, 0.735, 0.72, 0.605, 0.59, 0.75, 0.745, 0.275, 0.575, 0.69, 0.565, 0.915, 0.755, 0.765, 0.64, 0.77,
     0.65, 0.645, 0.655, 0.74, 0.71, 1.005, 0.7, 0.695, 0.64, 0.37, 0.37, 0.37, 0.475, 0.505, 0.335,
     0.57, 0.62, 0.575, 0.62, 0.585, 0.38, 0.62, 0.6, 0.25, 0.25, 0.56, 0.25, 0.89, 0.6, 0.605, 0.62, 0.62, 0.4, 0.54,
     0.355, 0.6, 0.585, 0.83, 0.555, 0.585, 0.56, 0.44, 0.345, 0.44, 0.67)))
_W.update({"’": 0.275, "‘": 0.275, "–": 0.5, "—": 1.0})


def card_lines(text: str, width: float) -> int:
    """How many lines `text` takes in a card bubble `width` em wide: words are placed greedily, a line break in
    the text starts a new line (the bubble keeps them), and a word wider than a line takes the lines it needs."""
    room = width * CARD_FIT_SLACK
    lines = 0
    for paragraph in text.split("\n"):
        used, lines = 0.0, lines + 1
        for word in paragraph.split():
            size = sum(_W.get(char, CARD_WIDE) for char in word)
            if used and used + _W[" "] + size <= room:
                used += _W[" "] + size
                continue
            if used:
                lines += 1
            lines += int(size // room) if size > room else 0
            used = size % room if size > room else size
    return lines


def card_cut(text: str, panels: int) -> bool:
    """True when the picture of a card with this many panels may not show all of `text`."""
    width, clamp = CARD_FIT[min(max(panels, 1), 4)]
    return card_lines(text, width) > clamp
# Server-side thresholds for the "More" control: longer than the first is clamped on a phone, than the second also on
# a tablet, than the third everywhere. Chosen so that a shorter text always fits the clamp of that width.
CLAMP_PHONE, CLAMP_TABLET, CLAMP_DESKTOP = 140, 240, 300
# The earlier answer above a changed one is held to two lines: longer than the first is clamped on a phone, than the
# second also on a tablet, than the third everywhere (the same three classes as an answer's clamp).
BEFORE_CLAMP = (80, 170, 240)
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


def _times(n: int) -> str:
    """"asked once", "asked 3 times"."""
    return "asked once" if n == 1 else f"asked {n} times"


def _replies(n: int) -> str:
    return "1 reply" if n == 1 else f"{n} replies"


def _listed(names: list[str], last: str = "and") -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" {last} " + names[-1]


def _marks(names: list[str]) -> list[str]:
    """A short mark per name, different for every name of the list: the first letter of each name when
    those differ ("C", "G"), else the first letter of each name's last word ("Chat app A" -> "A"), else,
    name by name, the first of these that is still free: first letter, last word's letter, first two
    letters, first letter and position. The mark is a reading aid; the name is always shown as text."""
    words = [re.findall(r"[^\W_]+", name) or [name or "?"] for name in names]
    first = [word[0][0].upper() for word in words]
    last = [word[-1][0].upper() for word in words]
    for choice in (first, last):
        if len(set(choice)) == len(choice):
            return choice
    out: list[str] = []
    for position, word in enumerate(words, 1):
        options = (word[0][0].upper(), word[0][:2].capitalize(), word[-1][0].upper(), f"{word[0][0].upper()}{position}")
        out.append(next(option for option in options if option not in out))
    return out


def _dress(columns: list[dict]) -> None:
    """Give the columns of ONE thread their look: `who` (the colour pair, by position), `shape` (the
    mark's shape; it changes when the eight pairs repeat) and `mark` (a short initial). Position only:
    never the name's meaning, never a brand's colour. The Human column has its own look (dark bubble,
    outlined mark, on the right) and takes no colour of the palette."""
    apps = [column for column in columns if column["kind"] != "human"]
    # The initial comes from the answerer's own name, not from the maker shown after it.
    for slot, (column, mark) in enumerate(zip(apps, _marks([column.get("base") or column["name"] for column in apps]))):
        column.update(who=f" who-{slot % PALETTE + 1}", shape=SHAPES[slot // PALETTE % len(SHAPES)], mark=mark)
    for column in columns:
        if column["kind"] == "human":
            column.update(who="", shape="", mark=_marks([column["name"]])[0])


def _av(column: dict) -> str:
    """The mark of an answerer. Decorative (hidden from a screen reader): the name is right next to it."""
    return f'<span class="av{column["shape"]}" aria-hidden="true">{esc(column["mark"])}</span>'


class _Chat:
    """Lookups over the chat-app track of one payload (empty when the payload has none). Time is told in
    DATES here: one period per day on which the owner asked, whatever import carried it. The Human
    column is one more column with the key "human" and its own kind."""

    def __init__(self, payload: dict):
        block = payload.get("chatbots") or {"groups": [], "apps": [], "human": None, "questions": [], "runs": [], "answers": [],
                                            "human_answers": []}
        self.groups = list(block["groups"])                  # [{id, name}], in the order their first app was recorded
        order = {group["id"]: n for n, group in enumerate(self.groups)}
        self.apps = sorted(block["apps"], key=lambda app: order[app["group"]])       # group by group; the recorded order within
        self.human = block["human"]
        self.labels = [app["label"] for app in self.apps]
        # An app entry with `kind` is a model answerer (asked by software): a column of the kind "model", never "chat-app".
        self.columns = [{"key": app["label"], "name": shown_name(app), "base": shown_name({**app, "maker": ""}),
                         "kind": app.get("kind", "chat-app"), "maker": app["maker"],
                         "attribution": app.get("attribution", ""), "group": app["group"]} for app in self.apps]
        if self.human:
            self.columns.append({"key": HUMAN, "name": self.human["name"], "kind": "human", "maker": "", "attribution": "",
                                 "meta": self.human["meta"], "group": None})
        # The threads of a chat-app question: with one group, every app and the Human together; with several, one
        # thread per group and the Human in a thread of its own. `pos` is the group's place in the group switch.
        people = [column for column in self.columns if column["kind"] == "human"]
        if len(self.groups) <= 1:
            self.threads = [{"id": "", "name": "", "pos": 1, "columns": list(self.columns)}] if self.columns else []
        else:
            self.threads = [{"id": group["id"], "name": group["name"], "pos": n,
                             "columns": [column for column in self.columns if column["group"] == group["id"]]}
                            for n, group in enumerate(self.groups, 1)]
            self.threads += [{"id": HUMAN, "name": people[0]["name"], "pos": 0, "columns": people}] if people else []
        for thread in self.threads:
            _dress(thread["columns"])
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
        self.models: dict[tuple[str, str], dict] = {}        # a model answerer's record on a date, in place of conditions
        for run in runs:
            for label, facts in run.get("models", {}).items():
                self.models[(label, run["collected_on"])] = facts
                self.asked[(label, run["collected_on"])] = run["questions"]
            for label, conditions in run["conditions"].items():
                self.conditions[(label, run["collected_on"])] = conditions
                self.asked[(label, run["collected_on"])] = run["questions"]
            if run.get("human"):
                self.collection[run["collected_on"]] = run["human"]
                self.asked[(HUMAN, run["collected_on"])] = run["questions"]
        self.answers = [a for a in block["answers"] if a["source"] in ("manual", "demo", "asked")]
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


# What a chat-app-only payload (archive_chatbots.site_payload) lacks, as empty values for _View.
_NO_MODELS = {"questions": [], "groups": [], "locked": False, "locked_at": None, "runs": [], "answerers": [], "answers": [],
              "trending": {"questions": [], "locked": False}}



def shown_name(app: dict) -> str:
    """The name a page shows for an answerer. A model asked by software is shown with its maker, as in
    "Gemma 3 4B (Google)" (owner's decision, 20 Nov 2026): a trailing "(local)" in the recorded name is
    not shown, and where the model ran stays in the details behind each answer. An app's name is unchanged.
    Display only: the recorded name in the payload is never rewritten."""
    name = app["name"]
    if app.get("kind") != "model":
        return name
    if name.endswith(" (local)"):
        name = name[: -len(" (local)")]
    maker = app.get("maker") or ""
    return f"{name} ({maker})" if maker and maker.lower() not in name.lower() else name


class _View:
    """Lookups over one payload, shared by every page kind."""

    def __init__(self, payload: dict):
        from . import archive_chatbots   # lazy, as in render_chatbots
        if isinstance(payload, dict) and payload.get("schema") in archive_chatbots.SITE_SCHEMAS:
            # The chat-app track on its own (the public site): it holds no model list, run or answer.
            problem = archive_chatbots.site_problem(payload)
            payload = {**_NO_MODELS, **payload}
        else:
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
            return NO_ANSWERS
        if track == "chat":
            return SAMPLE_LABEL if self.chat.demo else "AI-generated · may be wrong"
        if self.models_demo or (self.demo and not self.answers):
            return SAMPLE_LABEL
        if not self.answers:
            return NO_ANSWERS
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
    return [f"{day} · time {n}" for n, day in enumerate(days, 1)]


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
    _dress(columns)
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
            facts = [SAMPLE_WORD if sample else SOURCE_LABELS[answer["source"]], f"version {answer['model']}"]
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
                    cell["facts"].append("the time before gave mixed answers")
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
            "threads": [{"id": "", "name": "", "pos": 1, "columns": columns}],
            "demo": view.models_demo, "kicker": question["topic"] if trending else question["group"],
            "prompt_note": "Sent on its own. The model gets no date, no other answers and no earlier replies."}


ASKED_METHOD = "asked-by-software"     # internal only: a chat-track record carries no method, and no page shows this


def _asked_model(facts: dict) -> str:
    """The name shown for a model answerer on one date: what the service reported, else the id that was requested."""
    return facts.get("reported_model") or facts["model"]


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
            asked = chat.models.get((key, date))      # a model answerer: its record takes the place of an app's conditions
            conditions = chat.collection.get(date) if human else asked or chat.conditions.get((key, date))
            cell = {"record": f"{question_id}/{key}/{date}", "answer_id": answer["id"] if answer else None, "shown": False,
                    "text": None, "state": "no-answer",
                    "version": "" if human else _asked_model(asked) if asked else (conditions or {}).get("shown_model", ""),
                    "method": SAMPLE if sample else HUMAN_METHOD if human else ASKED_METHOD if asked else METHODS["manual"],
                    "provenance": SAMPLE if sample else "human-written" if human else "ai-generated",
                    "form": None, "collected": date, "previous": None, "why_not": "left out", "tags": [], "facts": [],
                    "conditions": dict(conditions) if conditions else None, "agreement": "", "others": [], "changed": None}
            if human:
                facts = [SAMPLE_WORD if sample else "Written by people"]
                if conditions:
                    facts.append(f"from: {conditions['source']}")
                    if conditions["collected_on"] != date:
                        facts.append(f"collected {full_date(conditions['collected_on'])}")
                    if conditions["count"]:
                        facts.append(f"{_replies(conditions['count'])} read")
            elif asked:
                facts = [f"model: {cell['version']}"] + ["run locally"] * bool(asked.get("local"))
                earlier = [d for d in chat.dates if d < date and (key, d) in chat.models]
                if earlier and _asked_model(chat.models[(key, earlier[-1])]) != cell["version"]:
                    facts.append("a different model name than before")
            else:
                moved = chat.conditions_changed(key, date)
                facts = [SAMPLE_WORD] * sample + [f"the app showed: {conditions['shown_model']}",
                         conditions_line(conditions)]
                if chat.shown_change(key, date):
                    facts.append("the app showed a new version name")
                if moved:
                    facts.append("asked a bit differently this time · " + "; ".join(moved))
                    cell["tags"].append(ASKED_DIFFERENTLY)
                if conditions["note"]:
                    facts.append(conditions["note"])
                if answer and answer.get("screenshot_hash"):
                    facts.append("screenshot kept")
            cell["facts"] = facts
            if answer is None:
                pass
            elif answer.get("refused"):
                cell["state"] = "refused"       # the fact only: a refusal's words are on no page and in no data file
            elif answer["state"] == "withheld":
                cell["state"] = "hidden"
            elif answer["state"] == "answered":
                state = "answered" if previous is None or answer["changed"] is None else "changed" if answer["changed"] else "same"
                cell.update(shown=True, text=answer["text"], state=state, previous=previous, changed=answer["changed"],
                            form=answer.get("manner") if human else None)
                if human:
                    cell["facts"].append(FORM_WORDS[answer["manner"]])
                previous = date
            cells[(key, date)] = cell
    return {"id": question_id, "text": question["text"], "prompt": question["prompt"], "hook": question["hook"],
            "why": "No deep reason. We wanted to see who's funny, and whether they're still funny next month.",
            "chips": [("chip", "Chat apps")], "track": "chat", "columns": chat.columns, "periods": periods, "cells": cells,
            "threads": chat.threads,
            "demo": sample, "kicker": "Chat apps",
            "prompt_note": "A fresh chat every time. That first line is why the replies are so short."}


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
    rec = {"schema": DATA_SCHEMA, "id": cell["record"], "question_id": board["id"], "question": board["text"],
            "prompt": board["prompt"], "answerer": column["key"], "answerer_name": column["name"],
            "answerer_kind": column["kind"], "maker": column["maker"], "version": cell["version"],
            "collected": cell["collected"], "period": period["label"], "method": cell["method"],
            "provenance": cell["provenance"], "human_form": cell["form"], "state": cell["state"], "shown": cell["shown"],
            "text": cell["text"], "changed": cell["changed"],
            "previous_id": previous[1]["record"] if previous else None,
            "previous_collected": previous[1]["collected"] if previous else None,
            "previous_text": previous[1]["text"] if previous and cell["state"] == "changed" else None,
            "conditions": cell["conditions"], "attribution": column["attribution"], "lang": "en", "notice": DATA_NOTICE}
    if board["track"] == "chat":   # owner decision: nothing published of the chat-app track says how a reply was collected
        del rec["method"]
    return rec


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
             ("maker", rec["maker"]), ("version", rec["version"]), ("collected", rec["collected"]), ("method", rec.get("method")),
             ("provenance", rec["provenance"])]
    pairs = [pair for pair in pairs if pair[1] is not None]   # a chat-app record has no method
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
                f'  <link rel="alternate" type="application/json" href="{base}data/{esc(board["id"])}.json" />\n',
                _preview(board)]
    else:
        out.append(f'  <link rel="alternate" type="application/x-ndjson" href="{base}data/answers.jsonl" />\n')
    return "".join(out)


def _bar(view: _View, up: str, base: str, versions: dict[str, str], current: str = "", *, here: str, standalone: bool = False,
         sibling: str | None = None) -> str:
    """The top bar: one line. `base` leads to the archive folder ("" on its own pages, "../" one folder
    down). When a standalone build names the other published snapshot, the last link goes to that
    snapshot's archive."""
    chat = bool(view.chat.dates)
    if standalone and sibling:
        # A sample page never calls the other snapshot (or itself) "recorded".
        last = (f'    <a href="{esc(up + sibling)}archive/{FIRST_PAGE}">'
                f'{"Main archive" if view.demo else "Sample site"}</a>\n')
    else:
        last = ""
    # A site whose AI answerers are all models asked by software says so: a model is never called a chat app.
    only_models = {column["kind"] for column in view.chat.columns if column["kind"] != "human"} == {"model"}
    links = [("front", f"{base}index.html", ("AI models" if only_models else "Chat apps") if chat else "Questions")]
    if not standalone:   # a standalone archive is the chat-app track only: it has no model page to link
        links += [("trending", f"{base}trending.html", "Trending"), ("models", f"{base}models.html", "Models")]
    links.append(("chatbots", f"{base}chatbots.html", "How it works") if chat or standalone
                 else ("method", f"{base}models.html#method", "How it works"))
    nav = "".join(f'    <a href="{href}"{" aria-current=" + chr(34) + "page" + chr(34) if name == current else ""}>{text}</a>\n'
                  for name, href, text in links)
    return (f'<a class="skip" href="{esc(here)}#main">Skip to the content</a>\n'
            '<header class="bar"><div class="bar-in">\n'
            f'  <a class="wordmark" href="{base}{FIRST_PAGE}"><img class="mark" src="{up}static/archive-mark.svg?v={versions["archive-mark.svg"]}" '
            'alt="" width="57" height="24" />Answers Over Time</a>\n'
            '  <nav aria-label="Archive navigation">\n' + nav + last + '  </nav>\n'
            '</div></header>\n')


SAMPLE_FOOT = "Everything here is a sample we wrote to show how the site works. No AI and no person was asked."


def chat_disclaimer(makers: list[str]) -> str:
    """The notice shown wherever real hand-entered replies are: what they are and that the site is
    independent of the companies whose apps were asked (named from the recorded apps, in their order)."""
    names: list[str] = []
    for maker in makers:
        if maker not in names:
            names.append(maker)
    if not names:      # only model answerers without a recorded maker
        return ("These answers are AI-generated and shown exactly as they came. They can be wrong, so please don't take them as "
                "advice. We're not affiliated with, sponsored by or endorsed by the makers of the products named; product names "
                "belong to their owners.")
    return ("These answers are AI-generated and shown exactly as they came. They can be wrong, so please don't take them as "
            f"advice. We're not affiliated with, sponsored by or endorsed by {_listed(names, 'or')}; product names belong to "
            "their owners.")


def _foot(view: _View, track: str, shown_ids: list[str], back: str = "") -> str:
    """The honesty footer, once per page, each thing said once: what the answers are; on a chat-app page that
    refusals and non-answers are left out (the only place a page says it). Owner's decision, 20 Nov 2026: no
    "Read-only. No payments. Just answers." line on any page."""
    sample = view.chat.demo if track == "chat" else view.models_demo
    if sample:
        honesty = SAMPLE_FOOT
    elif track == "chat":
        honesty = chat_disclaimer([view.chat.maker[label] for label in view.chat.labels if view.chat.maker[label]]) if view.chat.labels else \
            "These answers were written by people." if view.chat.human else "No answers here yet."
    else:
        honesty = "These answers came from AI models. They can be wrong, so please don't take them as advice."
    lines = [LEFT_OUT] if track == "chat" else []
    return ('<footer class="foot">' + back + f'<span>{esc(honesty)}</span>'
            + "".join(f'<span>{esc(line)}</span>' for line in lines) + '</footer>\n')


def _square(cell: dict) -> str:
    kind = {"answered": "same", "same": "same", "changed": "changed", "mixed": "mixed"}.get(cell["state"], "none")
    return f'<span class="sq sq-{kind}{" sq-cond" if cell["tags"] else ""}"></span>'


def _timeline(board: dict, column: dict) -> str:
    """One square per period this answerer was asked, oldest on the left. The accessible name says WHEN it
    changed ("Chat app A: changed Oct 2026 and Nov 2026; no answer Sep 2026; asked 4 times")."""
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
    parts += [f"asked a bit differently {_listed(moved)}"] if moved else []
    parts.append(_times(len(series)))
    summary = f"{column['name']}: " + "; ".join(parts)
    return (f'<span class="timeline" role="img" aria-label="{esc(summary)}">'
            + "".join(_square(cell) for _period, cell in series) + '</span>')


def _key(view: _View) -> str:
    """The one-line key to the timeline squares, on the model lists (a chat-app list has no squares and so no
    key). It opens with what a square is, so that a square next to a word is read as a mark and not as a box
    to tick."""
    return ('<p class="key"><span class="key-note">One square each time we asked, oldest first:</span>'
            '<span class="key-item"><span class="sq sq-same"></span>same as last time</span>'
            '<span class="key-item"><span class="sq sq-changed"></span>changed</span>'
            + ('<span class="key-item"><span class="sq sq-mixed"></span>mixed answers</span>' if view.multi else '')
            + '<span class="key-item"><span class="sq sq-none"></span>no answer</span></p>')


def _changed_tag(human: bool) -> str:
    """The word that goes with a changed answer (colour is never the only signal). People did not change
    their mind: other people answered, so the Human's says "Different answer this month"."""
    return f'<span class="tag-changed mono">{"Different answer this month" if human else "Changed"}</span>'


def _before(previous: tuple[dict, dict], page: bool = False) -> str:
    """The earlier answer as an old bubble: dashed and struck through, with "Before" and its month. It sits
    directly above the new answer and is held to two lines by the stylesheet: on a list always, on a
    question page (`page`) at the widths where its length needs it. It has no "More" of its own: the whole
    text is in the same bubble's History, in the table "What changed" and in the data file."""
    period, cell = previous
    size = len(cell["text"])
    clamp = " clamp" if not page else " clamp c-s c-m c-l" if size > BEFORE_CLAMP[2] else " clamp c-s c-m" if size > BEFORE_CLAMP[1] else \
        " clamp c-s" if size > BEFORE_CLAMP[0] else ""
    return (f'<div class="before"><p class="before-text{clamp}"><span class="before-when mono">Before · '
            f'<time datetime="{esc(cell["collected"])}">{esc(period["label"])}</time></span>{esc(cell["text"])}</p></div>')


def _mini(board: dict, column: dict) -> str:
    """One answerer's newest answer on a list page, as a bubble: mark, name, the text clamped by the
    stylesheet. "" for an answerer that was never asked this question. A chat-app bubble carries no row of
    squares (owner's decision of 20 November 2026: they read as boxes to tick): a new answer says "Changed"
    in words, an answer that is not from the newest month says which month it is from ("From Oct 2026": the
    answerer gave none since), and every month is on the question's page. A model list keeps its squares:
    they are its only sign of mixed answers and of earlier changes."""
    found = _latest(board, column["key"])
    line = _timeline(board, column)
    asked = bool(line)
    if board["track"] == "chat":
        line = ""
    if found is None:
        if not asked:
            return ""
        return (f'<li class="mini mini-none{column["who"]}">{_av(column)}<div class="stack"><div class="bubble">'
                f'<div class="mini-top"><span class="mini-name mono">{esc(column["name"])}</span>{line}</div>'
                '<span class="mini-text muted">No answer yet</span></div></div></li>')
    period, cell = found
    human = column["kind"] == "human"
    changed = cell["state"] == "changed" and board["periods"][-1]["key"] == period["key"]
    previous = _previous(board, column["key"], cell) if changed else None
    if board["track"] == "chat" and board["periods"][-1]["key"] != period["key"]:
        line = (f'<span class="mini-when">From <time datetime="{esc(cell["collected"])}">{esc(period["label"])}</time></span>')
    return (f'<li class="mini{" is-changed" if changed else ""}{" kind-human" if human else ""}{column["who"]}"'
            f'{data_attrs(record(board, column, period, cell))}>{_av(column)}<div class="stack">'
            + (_before(previous) if previous else '')
            + f'<div class="bubble"><div class="mini-top"><span class="mini-name mono">{esc(column["name"])}</span>'
            f'{_changed_tag(human) if changed else ""}{line}</div>'
            f'<span class="mini-text">{esc(cell["text"])}</span></div></div></li>')



SITE_URL_VARIABLE = "LIVE_SITE_URL"
_SITE_URL = re.compile(r"https://[a-z0-9.-]+(?:/[A-Za-z0-9._~/-]*)?")


def site_url() -> str:
    """The public address of the site's root folder (LIVE_SITE_URL, for example the GitHub Pages address), or ""
    when it is not set or not a plain https address. Only the share links need it: every other link is relative."""
    raw = os.getenv(SITE_URL_VARIABLE, "").strip()
    return raw.rstrip("/") + "/" if _SITE_URL.fullmatch(raw) else ""


CARD_IMAGES_VARIABLE = "LIVE_CARD_IMAGES"


def card_images() -> bool:
    """True when the build also makes a PNG picture of every share card beside its page (`card/<ID>.png`,
    written by scripts/capture-cards.mjs in the publishing workflow). Set LIVE_CARD_IMAGES=YES only there:
    a page must not link to a picture that was not made."""
    return os.getenv(CARD_IMAGES_VARIABLE, "").strip().upper() == "YES"


def _preview(board: dict) -> str:
    """Link-preview tags for a chat-app question page (the Open Graph names that Facebook and others read):
    the question as the title, one line of description, and, when the site's public address is known, the
    page's address and the picture of its share card. No tag points anywhere but this site."""
    if board["track"] != "chat":
        return ""
    out = ['  <meta property="og:type" content="article" />\n',
           '  <meta property="og:site_name" content="Answers Over Time" />\n',
           f'  <meta property="og:title" content="{esc(board["text"])}" />\n',
           '  <meta property="og:description" content="Same question, different answers. See who said what, month by month." />\n']
    base = site_url()
    if base:
        out.append(f'  <meta property="og:url" content="{esc(base)}archive/c/{esc(board["id"])}.html" />\n')
        if card_images():
            out += [f'  <meta property="og:image" content="{esc(base)}archive/card/{esc(board["id"])}.png" />\n',
                    '  <meta property="og:image:width" content="1080" />\n',
                    '  <meta property="og:image:height" content="1920" />\n']
    return "".join(out)


CAPTION_SAMPLE = "Sample only: we wrote these answers ourselves."
SAVE_HINT = "Save the image, then upload it in Instagram or TikTok."
SAVE_NOTE = "How saving works depends on your browser: if nothing is saved, open the picture and touch and hold it."


def _collected(board: dict) -> list[str]:
    """The dates on which the answers now shown for this question were collected, oldest first (the dates of
    observation, never the date of an export)."""
    return sorted({cell["collected"] for cell in board.get("cells", {}).values() if cell.get("shown") and cell.get("collected")})


def card_file_name(board: dict) -> str:
    """The name a saved card picture gets: answers-over-time-<question id>-<year>-<month of the newest answers>.png."""
    dates = _collected(board)
    return f"answers-over-time-{board['id']}" + (f"-{dates[-1][:7]}" if dates else "") + ".png"


def caption(board: dict) -> str:
    """A short caption for a post, from public data only: the question, when the shown replies were collected,
    and the address of the question's page (when the site's public address is known). It says nothing about
    an answer having changed, and a sample says that it is a sample."""
    # Its own wording, so a sample page still carries its one sample label exactly once.
    lines = ([CAPTION_SAMPLE] if board.get("demo") else []) + [board["text"]]
    dates = _collected(board)
    if dates:
        lines.append("Replies collected: " + ", ".join(full_date(date) for date in dates))
    base = site_url()
    if base:
        lines.append(f"See the answers over time: {base}archive/c/{board['id']}.html")
    return "\n".join(lines)


def _share(board: dict, page: str, card: str, *, full: bool = False) -> str:
    """The share line of a chat-app question. No script, no button from another site, no logo; nothing here
    posts anywhere, and nothing is sent to another site unless the visitor follows a link.

    * Facebook: a plain link to its share page (only when the site's public address is known).
    * Save card: when the build made a picture of each card (LIVE_CARD_IMAGES=YES), a link to that PNG with a
      download name, and beside it a plain link that opens the same picture (to touch and hold, or where the
      browser ignores the download hint). Without pictures, one link to the card page.
    * `full` (the question's own page): the plain sentence on what to do with the picture, and a caption to
      select and copy.
    `page` and `card` are relative to the page the line is on."""
    if board["track"] != "chat":
        return ""
    links = []
    base = site_url()
    if base:
        target = quote(f"{base}archive/c/{board['id']}.html", safe="")
        links.append(f'<a rel="noopener noreferrer" href="https://www.facebook.com/sharer/sharer.php?u={target}" '
                     f'aria-label="Share on Facebook: {esc(board["text"])}">Facebook</a>')
    pictures = card_images() and card.endswith(".html")
    if pictures:
        picture = card[:-len(".html")] + ".png"
        links.append(f'<a href="{esc(picture)}" download="{esc(card_file_name(board))}" '
                     f'aria-label="Save the card as a picture: {esc(board["text"])}">Save card</a>')
        links.append(f'<a href="{esc(picture)}" aria-label="Open the card picture: {esc(board["text"])}">Open picture</a>')
    else:
        links.append(f'<a href="{esc(card)}" aria-label="Open the share card: {esc(board["text"])}">Open card</a>')
    line = f'<p class="row-share small"><span class="muted">Share:</span>{"".join(links)}</p>'
    if not full:
        return line
    return (line
            + (f'\n<p class="share-hint small muted">{esc(SAVE_HINT)} {esc(SAVE_NOTE)}</p>' if pictures else '')
            + '\n<details class="share-caption"><summary>Caption to copy</summary>'
              f'<pre class="caption">{esc(caption(board))}</pre>'
              '<p class="small muted">Select the text above and copy it. It goes in your post beside the picture.</p></details>')


def _row(board: dict, href: str) -> str:
    """One question on a list page, as a thread in a pane: the question as a heading with a link, then every
    answerer's newest answer as a bubble. With several answerer groups each group is its own list (the
    group switch of the page shows one at a time; without a stylesheet all of them show, each under its
    name) and the Human's answer comes last, whatever group is chosen."""
    several = len(board["threads"]) > 1
    lists, total = [], 0
    for thread in board["threads"]:
        items = [item for item in (_mini(board, column) for column in thread["columns"]) if item]
        if not items:
            continue
        total += len(items)
        if several and thread["id"] != HUMAN:
            lists.append(f'<ol class="minis minis-g g-{thread["pos"]}" aria-label="{esc(thread["name"])}">{"".join(items)}</ol>')
        else:
            lists.append(f'<ol class="minis{" minis-human" if several else ""}">{"".join(items)}</ol>')
    hook = f'<span class="row-hook">{esc(board["hook"])}</span>' if board["hook"] else ''
    body = "\n    ".join(lists) if lists else '<p class="muted small row-none">Nothing here yet.</p>'
    # A chat-app question leads with its hook, not its id: the id stays in the link, the data attributes and the
    # page's "The exact words we used" block.
    number = '' if board["track"] == "chat" else f'<span class="q-id mono">{esc(board["id"])}</span>'
    share = _share(board, href, f'card/{board["id"]}.html')
    # The whole card opens the question: the heading's link is stretched over the card in the stylesheet
    # (`.row-open::after`) and carries the arrow that says so (`.row-open::before`). No script.
    return ('  <article class="row">\n'
            f'    <div class="row-head">{number}{hook}'
            f'<h3 class="row-q"><a class="row-open" href="{esc(href)}">{esc(board["text"])}</a></h3></div>\n'
            f'    {body}\n'
            f'    <p class="row-more"><a href="{esc(href)}" aria-label="See every month of: {esc(board["text"])}">See every month</a></p>\n'
            + (f'    {share}\n' if share else '') +
            '  </article>\n')


def _list_head(tag: str, title: str | list[str], lede: str, steps: list[str] | None = None) -> str:
    """The head of a list page. `title` is the headline, or its sentences (each its own span, so that a phone
    can break between them; the last one is in the accent colour). `lede` is ready HTML."""
    if isinstance(title, str):
        heading = esc(title)
    else:
        heading = " ".join(f'<span class="hl{" hl-last" if n == len(title) and n > 1 else ""}">{esc(sentence)}</span>'
                           for n, sentence in enumerate(title, 1))
    return ('<div class="list-head">\n'
            f'  <span class="tag tag-dark mono">{esc(tag)}</span>\n'
            f'  <h1 class="display">{heading}</h1>\n'
            f'  <p class="lede">{lede}</p>\n'
            + (f'  <ol class="steps">{"".join(f"<li>{esc(step)}</li>" for step in steps)}</ol>\n' if steps else '')
            + '</div>\n')


def chat_headline(chat: _Chat) -> list[str]:
    """The front page's headline, sentence by sentence, counted from the payload: "Same question." / "Three
    AI apps." / "One human." with one group of AI apps; "N chatbots." for one group of any other kind; and
    with several groups a neutral count of all of them and of the groups. Says nothing the data does not show."""
    count = len(chat.labels)
    number = count_word(count).capitalize()
    models = any(column["kind"] == "model" for column in chat.columns)   # a model is not a chatbot: the count then says "AIs"
    if len(chat.groups) > 1:
        apps = f"{number} {'AIs' if models else 'chatbots'} in {count_word(len(chat.groups))} groups."
    elif models:
        apps = f"{number} AI model{'' if count == 1 else 's'}."
    else:
        from . import archive_chatbots   # lazy, as in render_chatbots
        ai = bool(chat.groups) and chat.groups[0]["id"] == archive_chatbots.DEFAULT_GROUP["id"]
        apps = f"{number} {'AI app' if ai else 'chatbot'}{'' if count == 1 else 's'}."
    return ["Same question."] + ([apps] if count else []) + (["One human."] if chat.human else [])


def _page(view: _View, head: str, bar: str, body: list[str], foot: str, schema: str = "") -> str:
    return "".join([head, bar, f'<main id="main" class="wrap"{schema}>\n', *body, '</main>\n',
                    '<div class="wrap wrap-foot">\n', foot, '</div>\n</body>\n</html>\n'])


NOBODY_ASKED = "We haven't asked anyone yet."
HOME_DESCRIPTION = "The same questions, asked again every month, with every answer kept."
# The chat-app comparison (the public front page and the root page that leads to it).
CHAT_DESCRIPTION = ("We ask chatbots the same silly questions every month and keep every answer. "
                    "See who's funny, who's weird and who changed their answer.")


def render_home(versions: dict[str, str] | None = None) -> str:
    """`index.html` at the root of a standalone archive: sends the visitor to `archive/index.html`
    with a meta refresh and shows a plain link for a browser that does not follow it. No script."""
    versions = versions or asset_versions()
    head = _head("Answers Over Time", "", versions, CHAT_DESCRIPTION)
    title = '  <title>'
    if head.count(title) != 1:
        raise ValueError("the page head must hold exactly one <title>")
    head = head.replace(title, f'  <meta http-equiv="refresh" content="0; url=archive/{FIRST_PAGE}" />\n' + title)
    return (head + '<main id="main" class="wrap">\n<div class="list-head">\n'
            '  <span class="tag tag-dark mono">Read-only · a saved copy</span>\n'
            '  <h1 class="display">Answers Over Time</h1>\n'
            f'  <p class="lede"><a href="archive/{FIRST_PAGE}">See the answers</a></p>\n'
            '</div>\n</main>\n<div class="wrap wrap-foot">\n'
            '<footer class="foot"><span>This is a saved copy of the site. Nothing here can be changed.</span></footer>\n'
            '</div>\n</body>\n</html>\n')


def _method_models(view: _View) -> str:
    return ('<section id="method" class="method">\n  <h2 class="display">How we keep track</h2>\n  <div class="method-grid">\n'
            '    <div><strong>Answered alone</strong><span>No model sees another model\'s answer, from this time or any time before.</span></div>\n'
            '    <div><strong>Same words every time</strong><span>The exact words are on each question\'s page. We never edit them.</span></div>\n'
            '    <div><strong>Nothing is rewritten</strong><span>We keep each answer exactly as it came. The page may fold a long one; '
            'the words never change.</span></div>\n'
            '    <div><strong>"Changed" is a plain comparison</strong><span>The text differs from that model\'s answer before, ignoring '
            'letter case, spacing and punctuation at the ends. It does not say which answer is right.</span></div>\n'
            + ('    <div><strong>Asked more than once</strong><span>Sometimes we ask each question several times in one go, each time on its own. '
               'You see the answer most tries gave; if the tries did not agree it says "Mixed answers", not "Changed".</span></div>\n'
               if view.multi else '')
            + '  </div>\n</section>\n')


def _model_rows(view: _View, questions: list[dict], folder: str) -> list[str]:
    return [_row(_model_board(view, question["id"]), f"{folder}/{question['id']}.html") for question in questions]


def _trending_section(view: _View, heading: str = "Trending", key: str = "") -> list[str]:
    ordered = sorted(view.trending, key=lambda q: (q["added"], view.place[q["id"]][1]), reverse=True)
    out = [f'<section class="group">\n  <div class="group-head"><h2>{esc(heading)}</h2>'
           f'<span class="muted">From what people were searching for. Newest first.</span>{key}</div>\n']
    if ordered:
        out += ['  <div class="qs">\n', *_model_rows(view, ordered, "t"), '  </div>\n']
    else:
        out.append('  <p class="muted trend-none">No trending questions yet.</p>\n')
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
        out += ['  <div class="qs">\n', *_model_rows(view, questions, "q"), '  </div>\n', '</section>\n']
    return out


def _core_note(view: _View) -> str:
    total = count_word(len(view.payload["questions"]))
    if view.payload["locked"]:
        note = (f'The list of {total} questions was fixed by the owner on {esc(full_date(view.payload["locked_at"] or ""))} '
                'and does not change.')
    else:
        note = f'This list of {total} questions is a draft. Once the owner fixes it and the archive starts, it does not change.'
    return f'<p class="list-note muted">{note} We ask about once a month.</p>\n'


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
    else the trending and the core questions with the models' newest answers. A standalone archive
    never lists the models: with no chat-app run its front page says that nothing was asked yet."""
    view = _View(payload)
    versions = versions or asset_versions()
    chat = view.chat
    if standalone and not chat.dates:
        body = [_list_head(view.tag("chat"), "Same question, asked again every month.", esc(NOBODY_ASKED)),
                '<section class="group">\n  <p class="muted trend-none">Nothing to read yet. Come back soon. '
                '<a href="chatbots.html">Here is how it works</a>.</p>\n</section>\n']
        return _page(view, _head("Answers Over Time", "../", versions, CHAT_DESCRIPTION, _meta(view, "", track="chat")),
                     _bar(view, "../", "", versions, "front", here="index.html", standalone=standalone, sibling=sibling), body,
                     _foot(view, "chat", []))
    if chat.dates:
        shown = [q["id"] for q in view.chat_questions]
        several = len(chat.groups) > 1
        lede = "We ask again every month and keep every reply. Tap a question to see who changed their answer."
        if several:
            lede = f"{_listed([group['name'] for group in chat.groups])}. {lede}"
        steps = [_plural(len(shown), "question"), "Same words every month", "Every reply kept"]
        body = [_list_head(view.tag("chat"), chat_headline(chat), esc(lede), steps), '<section class="group">\n']
        if several:
            # The group switch: one radio per group, the first one chosen. No script; without the stylesheet every group shows.
            body.append("".join(f'  <input class="g-radio" type="radio" name="group" id="grp-{n}"{" checked" if n == 1 else ""} />\n'
                                for n in range(1, len(chat.groups) + 1)))
        body.append(f'  <div class="group-head"><h2>The latest answers</h2></div>\n')
        if several:
            sizes = [sum(1 for app in chat.apps if app["group"] == group["id"]) for group in chat.groups]
            body.append('  <div class="group-strip" role="group" aria-label="Choose a group">'
                        + "".join(f'<label for="grp-{n}"><span class="g-name">{esc(group["name"])}</span>'
                                  f'<span class="g-count">{size} asked</span></label>'
                                  for n, (group, size) in enumerate(zip(chat.groups, sizes), 1)) + '</div>\n')
        if chat.apps:
            # One plain text link to the answerers and their "just for fun" answering styles on "How it works".
            body.append(f'  <p class="meet"><a href="chatbots.html#answerers">{esc(MEET)}</a></p>\n')
        body += ['  <div class="qs">\n', *[_row(_chat_board(view, question_id), f"c/{question_id}.html") for question_id in shown],
                 '  </div>\n', '</section>\n']
        body.append('<p class="list-note muted">Every app gets the exact same words. '
                    '<a href="chatbots.html">Here is how it works</a>.'
                    + ('' if standalone else ' Want the plain models instead? <a href="models.html">Models</a> and '
                                             '<a href="trending.html">Trending</a>.') + '</p>\n')
        return _page(view, _head("Answers Over Time", "../", versions, CHAT_DESCRIPTION, _meta(view, "", track="chat")),
                     _bar(view, "../", "", versions, "front", here="index.html", standalone=standalone, sibling=sibling), body,
                     _foot(view, "chat", shown))
    body = [_list_head(view.tag(), "Simple questions, asked again every month.",
                       "We ask AI the same basic things again and again, and keep every answer.")]
    body += _trending_section(view, key=_key(view)) + _core_sections(view)
    body += [_core_note(view), _method_models(view)]
    return _page(view, _head("Answers Over Time", "../", versions, HOME_DESCRIPTION, _meta(view, "")),
                 _bar(view, "../", "", versions, "front", here="index.html", standalone=standalone, sibling=sibling), body,
                 _foot(view, "models", [q["id"] for q in view.questions + view.trending]))


def render_models(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                  sibling: str | None = None) -> str:
    """`models.html`: the core question list, asked of the models directly, with their newest answers."""
    view = _View(payload)
    versions = versions or asset_versions()
    body = [_list_head(view.tag(), "The same questions, straight to the models.",
                       "No app in between: we ask each model ourselves and keep every answer.")]
    body += _core_sections(view, key=_key(view))
    body += [_core_note(view), _method_models(view)]
    return _page(view, _head("Models · Answers Over Time", "../", versions,
                             "The core questions, asked of AI models directly, with every answer kept.", _meta(view, "")),
                 _bar(view, "../", "", versions, "models", here="models.html", standalone=standalone, sibling=sibling), body,
                 _foot(view, "models", [q["id"] for q in view.questions]))


def render_trending(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                    sibling: str | None = None) -> str:
    """`trending.html`: the trending questions, newest first, with the models' newest answers."""
    view = _View(payload)
    versions = versions or asset_versions()
    body = [_list_head(view.tag(), "Questions from what people search for.",
                       "Now and then we write a question from a topic lots of people looked up, then keep asking it.")]
    body += _trending_section(view, key=_key(view))
    body.append(_trending_note(view))
    return _page(view, _head("Trending · Answers Over Time", "../", versions,
                             "Questions written from what many people were searching for, asked of AI models ever since.",
                             _meta(view, "")),
                 _bar(view, "../", "", versions, "trending", here="trending.html", standalone=standalone, sibling=sibling), body,
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
            f'<label class="more {clamp[6:]}" for="{box}"><span class="more-open">Read the rest</span><span class="more-close">Show less</span></label>')


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
    items = "".join(f'<li><span class="hist-when mono">{esc(_span(group["first"], group["last"]))} · {_times(group["checks"])}</span>'
                    f'<span class="hist-text">{esc(group["text"])}</span></li>' for group in reversed(groups))
    count = _plural(len(groups) - 1, word) if len(groups) > 1 else f"no {word} yet"
    if len(groups) < 2:
        return ""   # one answer, never changed: the card already shows all there is
    return (f'<details class="hist"><summary>History · {count}</summary>'
            f'<ol class="hist-list" reversed>{items}</ol></details>')


def _card(board: dict, column: dict, period: dict, cell: dict, ids: _Ids, newest: bool) -> str:
    """One answer of one month as a bubble with the answerer's mark. Visible: the name, the state, the answer,
    what it was before (an old bubble directly above), the app's own attribution line, quoted / summarised
    for a human answer. Everything else is metadata."""
    human = column["kind"] == "human"
    rec = record(board, column, period, cell)
    tags = []
    if human and cell["form"]:          # short, so it shares the name's line; the longer state tag follows
        tags.append(f'<span class="tag-form mono">{esc(FORM_WORDS[cell["form"]])}</span>')
    if cell["state"] == "changed":
        tags.append(_changed_tag(human))
    if cell["state"] == "mixed":
        tags.append('<span class="tag-mixed mono">Mixed answers</span>')
    tags += [f'<span class="tag-cond mono">{esc(tag)}</span>' for tag in cell["tags"]]
    out = [f'    <article class="acard{" is-changed" if cell["state"] == "changed" else ""}'
           f'{" is-mixed" if cell["state"] == "mixed" else ""}'
           f'{" kind-human" if human else ""}{column["who"]}"{data_attrs(rec)} itemprop="suggestedAnswer" itemscope itemtype="https://schema.org/Answer">'
           f'{_av(column)}<div class="stack">']
    previous = _previous(board, column["key"], cell)
    if cell["state"] == "changed" and previous:
        out.append(_before(previous, page=True))
    out += [f'<div class="bubble"><div class="acard-head">{_author(column, board["demo"])}'
            + (f'<span class="tags">{"".join(tags)}</span>' if tags else '') + '</div>',
            _more(ids, cell["text"], "ans", _clamp(cell["text"]), f"Show the whole answer from {column['name']}", ' itemprop="text"')]
    if cell["others"]:
        out.append('<ul class="others" aria-label="Other answers from the same time we asked">'
                   + "".join(f'<li><span class="mono">{esc(other["count"])} of {esc(rec["conditions"].get("samples", ""))}</span> '
                             f'{esc(other["text"])}</li>' for other in cell["others"]) + '</ul>')
    if column["attribution"]:
        out.append(f'<p class="credit mono">{esc(column["attribution"])}</p>')
    if newest:
        out.append(_history(board, column))
    out.append(f'</div></div><meta itemprop="dateCreated" content="{esc(cell["collected"])}" /></article>\n')
    return "".join(out)


def _panel(board: dict, period: dict, ids: _Ids, newest: bool, number: int) -> str:
    """One month: a pane of bubbles per thread (one thread, or one per answerer group under its name)."""
    panes, notes = [], []
    left_out = 0
    several = len(board["threads"]) > 1
    for thread in board["threads"]:
        cards = []
        for column in thread["columns"]:
            cell = board["cells"].get((column["key"], period["key"]))
            if cell is None:
                continue
            if cell["shown"]:
                cards.append(_card(board, column, period, cell, ids, newest))
            elif board["track"] == "chat":
                left_out += 1
            else:
                cards.append(f'    <article class="acard acard-none{column["who"]}">{_av(column)}<div class="stack"><div class="bubble">'
                             f'<div class="acard-head"><span class="acard-name mono">{esc(column["name"])}</span></div>'
                             f'<p class="ans-none">{esc(cell["why_not"])}</p></div></div></article>\n')
        if not cards:
            continue
        count = min(len(cards), 6)
        panes.append((f'  <h3 class="thread-name">{esc(thread["name"])}</h3>\n' if several else '')
                     + f'  <div class="cards n-{count}{" n-wide" if len(cards) >= WIDE_THREAD else ""}">\n{"".join(cards)}  </div>\n')
    if left_out:
        notes.append(f"{_replies(left_out)} left out")
    when = full_date(period["date"])
    body = "".join(panes) if panes else '  <p class="muted panel-none">Nothing to show for this month.</p>\n'
    return (f'<section class="panel" id="m-{esc(period["key"])}" aria-label="{esc(period["label"])}">\n'
            f'  <h2 class="panel-when"><time datetime="{esc(period["date"])}">{esc(period["label"])}</time>'
            f'<span class="muted">asked {esc(when)}{" · " + esc(" ".join(notes)) if notes else ""}</span></h2>\n'
            + body + '</section>\n')


def _month_mark(board: dict, period: dict) -> str:
    """"2 changed" for the month pill: how many answerers (the Human apart: people do not "change") gave a
    different answer that month. "" when none did."""
    changed = sum(1 for column in board["columns"] if column["kind"] != "human"
                  and board["cells"].get((column["key"], period["key"]), {}).get("state") == "changed")
    return f'<span class="m-mark">{changed} changed</span>' if changed else ''


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
                why = "no answer, left out" if board["track"] == "chat" else cell["why_not"]
                tds.append(f'<td role="cell" class="t-none"{label}>{esc(why)}</td>')
            elif cell["state"] == "same":
                tds.append(f'<td role="cell" class="t-same"{label}{data_attrs(record(board, column, period, cell))}>same again</td>')
            else:
                word = {"answered": "first answer", "mixed": "mixed answers",
                        "changed": "different answer" if column["kind"] == "human" else "changed"}[cell["state"]]
                long = " t-long" if len(cell["text"]) > TABLE_CLAMP else ""
                tds.append(f'<td role="cell" class="t-{cell["state"]}"{label}{data_attrs(record(board, column, period, cell))}>'
                           f'<span class="t-word mono">{word}</span><span class="t-text{long}">{esc(cell["text"])}</span></td>')
        rows.append(f'    <tr role="row"><th scope="row" role="rowheader"><time datetime="{esc(period["date"])}">{esc(period["label"])}</time></th>'
                    + "".join(tds) + '</tr>\n')
    tall = " history-tall" if len(board["columns"]) > WIDE_THREAD else ""    # many answerers: rows as blocks at every width
    return ('<section class="changes">\n  <h2>What changed</h2>\n'
            f'  <table class="history{tall}" role="table">\n'
            '    <caption>Every time we asked, newest first. An answer is written out only when it is new or different.</caption>\n'
            f'    <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Month</th>{head}</tr></thead>\n'
            '    <tbody role="rowgroup">\n' + "".join(rows) + '    </tbody>\n  </table>\n</section>\n')


def _sources(board: dict) -> str:
    """The closed "The details behind each answer" block: per answerer, per month, everything recorded about
    how the answer got here. This is where the labels that left the cards can still be read by a person."""
    out = []
    for column in board["columns"]:
        series = _series(board, column["key"])
        if not series:
            continue
        who = (column["name"] + (f" · {column['maker']}" if column["maker"] and column["kind"] != "model" else "")
               + (f" · {column['meta']}" if column.get("meta") else ""))
        items = "".join(f'<li><span class="mono">{esc(period["label"])}</span> {esc(" · ".join(cell["facts"]) or "nothing noted")}</li>'
                        for period, cell in reversed(series))
        out.append(f'<div class="src"><strong>{esc(who)}</strong><ul>{items}</ul></div>')
    return "".join(out)


def _question_page(view: _View, board: dict, versions: dict[str, str], *, standalone: bool, sibling: str | None) -> str:
    chat = board["track"] == "chat"
    question_id = board["id"]
    ids = _Ids()
    # A chat-app page opens with its hook; its id is in "The exact words we used" (and in the address and the data).
    title_row = (('' if chat else f'<span class="q-id mono">{esc(question_id)}</span>')
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
            # Month pills, newest first. The newest says how many changed (words, not colour alone).
            body.append('<div class="month-strip" role="group" aria-label="Choose a month">'
                        + "".join(f'<label for="pick-{n}"><span class="m-name">{esc(period["label"])}</span>'
                                  f'{_month_mark(board, period) if n == 1 else ""}</label>' for n, period in enumerate(periods, 1))
                        + '</div>\n')
        body += [_panel(board, period, ids, n == 1, n) for n, period in enumerate(periods, 1)]
        body.append('</section>\n')
        if chat:
            line = DAY_NOTE if any(cell["tags"] for cell in board["cells"].values()) else ""
            cards = "".join(f'<a href="../card/{esc(question_id)}{"-" if suffix else ""}{esc(suffix)}.html">{esc(name)}</a>'
                            for suffix, (_panels, _kicker, name) in _chat_cards(board).items()
                            if _panels or not suffix)
            body.append((f'<p class="under muted">{esc(line)}</p>\n' if line else '')
                        + f'<p class="card-links small"><span class="muted">Share cards:</span>{cards}</p>\n'
                        + (_share(board, f"{question_id}.html", f"../card/{question_id}.html", full=True) + "\n"))
        else:
            body.append(f'<p class="card-links card-links-one small"><a href="../card/{esc(question_id)}.html">Share card</a></p>\n')
        body.append(_table(board))
    else:
        body.append('<p class="muted">No answers for this one yet. Come back next month.</p>\n')
    kinds = {cell["method"] for cell in board["cells"].values()}
    explain = []
    if SAMPLE in kinds:
        explain.append("<strong>Sample</strong>: we wrote these answers and their dates ourselves to show how the page works. No AI and no person was asked.")
    if HUMAN_METHOD in kinds:
        explain.append("<strong>Written by people</strong>: <em>quoted</em> means one person's exact words; "
                       "<em>summed up</em> means we boiled several replies down to one line. No names, ever.")
    if METHODS["engine"] in kinds:
        explain.append("<strong>We asked it ourselves</strong>: this server called the model and kept the receipt. "
                       "<em>run locally</em> or <em>via</em> a name says where the model ran; that is not the same as its maker's own chat app.")
    if METHODS["api"] in kinds:
        explain.append("<strong>Sent in by someone else, not checked</strong>: an outside program sent the answer and named its own version.")
    if any(cell["state"] == "mixed" for cell in board["cells"].values()):
        explain.append("<strong>Mixed answers</strong>: we asked several times in one go and the tries did not all agree, then or the "
                       "time before. You see the answer most tries gave; it is not counted as a change.")
    # Owner's decision, 20 Nov 2026: the public site (standalone) does not carry the two blocks "The exact words
    # we used" and "The details behind each answer". They stay in the local app, and the same facts stay in the
    # public data files (`data/<ID>.json`).
    behind = ('' if standalone else
              f'  <details><summary>The exact words we used</summary><pre class="prompt mono">{esc(board["prompt"])}</pre>'
              f'<p class="muted small">{esc(board["prompt_note"])} This is question {esc(question_id)} on our list.</p></details>\n'
              '  <details><summary>The details behind each answer</summary>'
              + "".join(f'<p class="small">{line}</p>' for line in explain)
              + f'<div class="srcs">{_sources(board)}</div>'
              f'<p class="small"><a href="../data/{esc(question_id)}.json">The same answers as a data file</a></p></details>\n')
    body.append('<section class="more-info">\n'
                f'  <details><summary>Why we ask this</summary><p>{esc(board["why"])}</p></details>\n'
                + behind +
                '</section>\n')
    back = {"chat": '<a class="back" href="../index.html">Back to all the questions</a>',
            "trending": '<a class="back" href="../trending.html">All trending questions</a>',
            "core": f'<a class="back" href="../models.html">All {esc(count_word(len(view.questions)))} questions</a>'}[board["track"]]
    track = "chat" if chat else "models"
    current = "front" if chat else "trending" if board["track"] == "trending" else "models"
    description = f"{board['text']} Every answer, month by month." if chat else board["text"]
    return _page(view, _head(f"{board['text']} · Answers Over Time", "../../", versions, description,
                             _meta(view, "../", board=board, track=track)),
                 _bar(view, "../../", "../", versions, current, here=f"{question_id}.html", standalone=standalone, sibling=sibling), body,
                 _foot(view, track, [question_id], back), ' itemscope itemtype="https://schema.org/Question"')


def render_question(payload: dict, question_id: str, versions: dict[str, str] | None = None, *,
                    standalone: bool = False, sibling: str | None = None) -> str:
    """One question of any track: `q/Q01.html`, `t/T001.html` or `c/C001.html` (one layout). Raises
    KeyError for an unknown question and for one the owner left off the pages."""
    view = _View(payload)
    return _question_page(view, _board(view, question_id), versions or asset_versions(), standalone=standalone, sibling=sibling)


CHATBOTS_DESCRIPTION = "How it works: the same words for every app, a fresh chat each time, and every reply kept."
MEET = "Meet the answerers"        # the front page's one text link to the answerers and their answering styles


def _styles(view: _View) -> dict[str, tuple[dict, list[tuple[dict, dict]]]]:
    """The "just for fun" answering style of every AI answerer (chat app or model; never the Human column):
    `profiles.profile` over the texts of ALL its shown answers, oldest first (a refused, skipped or hidden
    reply is not shown, so it is not counted). Worked out at render time from the payload and kept nowhere:
    no data file, export or data attribute carries it. Per column key: the profile and, answer by answer,
    the board and cell it came from."""
    from . import profiles   # lazy: only the "How it works" page reads it
    boards = [_chat_board(view, q["id"]) for q in view.chat_questions]
    out = {}
    for column in view.chat.columns:
        if column["kind"] == "human":
            continue
        shown = sorted(((cell["collected"], board["id"], board, cell) for board in boards
                        for (key, _), cell in board["cells"].items() if key == column["key"] and cell["shown"]),
                       key=lambda item: item[:2])
        out[column["key"]] = (profiles.profile([cell["text"] for *_, cell in shown]), [(board, cell) for *_, board, cell in shown])
    return out


def _style(found: tuple[dict, list[tuple[dict, dict]]], sample: bool) -> str:
    """One answerer's answering style inside its row of "How it works": our commentary, set apart from the
    recorded facts above it and never dressed as an answer bubble. The meter is five plain boxes with a
    text alternative, and its place is also said in words. An answer shown as "most in character" is a
    whole recorded answer, word for word, with a link to its question."""
    from . import profiles
    style, answers = found
    based = f'from {_plural(style["count"], ("sample " if sample else "") + "answer")} so far'
    head = f'<span class="fun-kicker mono">{esc(profiles.KICKER)}</span>'
    if style["step"] is None:
        return (f'<div class="app-fun">{head}<strong class="fun-name fun-early">{esc(profiles.TOO_EARLY)}</strong>'
                f'<span class="muted small">{esc(based[0].upper() + based[1:] + ". " + profiles.NEED)}</span></div>')
    place = profiles.position(style["step"])
    boxes = "".join(f'<span class="fun-box{" fun-on" if n == style["step"] else ""}"></span>' for n in range(1, len(profiles.STEPS) + 1))
    meter = (f'<span class="fun-scale"><span class="fun-end">{esc(profiles.ENDS[0])}</span>'
             f'<span class="fun-meter" role="img" aria-label="{esc("Step " + place)}">{boxes}</span>'
             f'<span class="fun-end">{esc(profiles.ENDS[1])}</span></span>')
    because = "; ".join(style["because"])
    quote = ""
    if style["pick"] is not None:
        board, cell = answers[style["pick"]]
        quote = (f'<span class="fun-quote small"><span class="muted">Most in character, on '
                 f'<a href="c/{esc(board["id"])}.html">{esc(board["text"])}</a></span> '
                 f'<span class="fun-words">{esc(cell["text"])}</span></span>')
    return (f'<div class="app-fun">{head}<strong class="fun-name">{esc(style["name"])}</strong>{meter}'
            f'<span class="muted small">{esc(place[0].upper() + place[1:])} · {esc(based)}</span>'
            + (f'<span class="small">{esc("Because: " + because + ".")}</span>' if because else '')
            + quote + '</div>')


def render_chatbots(payload: dict, versions: dict[str, str] | None = None, *, standalone: bool = False,
                    sibling: str | None = None) -> str:
    """`chatbots.html`: how the chat apps (and people) are asked, the conditions recorded on each app's
    latest date, and, on a real archive only, the owner's one-line aside. Names are shown as text only."""
    view = _View(payload)
    chat = view.chat
    versions = versions or asset_versions()
    lede = ("Every month, each app gets the same questions in the same words, in a fresh chat. We keep every reply."
            + (" This is a sample site: the apps are made up and we wrote the replies." if chat.demo else ""))
    body = [_list_head(view.tag("chat") if chat.dates else NO_ANSWERS, "How this works.", esc(lede))]
    if chat.dates and chat.columns:
        from . import profiles   # lazy: the wording of the "just for fun" line
        styles = _styles(view)
        body.append('<section class="group" id="answerers">\n  <div class="group-head"><h2>How we asked each one</h2>'
                    '<span class="muted">The settings from the last time we asked.</span></div>\n')
        if styles:
            # Said once per page: the answering styles are our own commentary, counted from the answers, and a joke.
            body.append(f'  <p class="fun-note">{esc(profiles.LABEL + (profiles.LABEL_SAMPLE if chat.demo else ""))}</p>\n')
        for thread in chat.threads:
            rows = []
            for column in thread["columns"]:
                key = column["key"]
                if column["kind"] == "human":
                    dates = sorted(chat.collection)
                    if not dates:
                        continue
                    collection = chat.collection[dates[-1]]
                    how = (f'<span>{esc(column["meta"])}</span>'
                           f'<span class="muted small">From: {esc(collection["source"])} · gathered {esc(full_date(collection["collected_on"]))} · '
                           f'{esc(_plural(len(dates), "time"))} so far · each answer says if it is quoted or summed up · no names</span>')
                    rows.append(f'  <div class="app-row kind-human">{_av(column)}<div class="app-name"><strong>{esc(column["name"])}</strong></div>'
                                f'<div class="app-how">{how}</div></div>\n')
                    continue
                if column["kind"] == "model":
                    dates = [d for d in chat.dates if (key, d) in chat.models]
                    rows.append(f'  <div class="app-row{column["who"]}">{_av(column)}<div class="app-name"><strong>' + esc(column["name"]) + '</strong>'
                                + '</div>'
                                f'<div class="app-how"><span>model: {esc(_asked_model(chat.models[(key, dates[-1])]))}</span>'
                                f'<span class="muted small">asked {esc(full_date(dates[-1]))} · {esc(_plural(len(dates), "time"))} in all'
                                f'</span></div>{_style(styles[key], chat.demo)}</div>\n')
                    continue
                dates = [d for d in chat.dates if (key, d) in chat.conditions]
                conditions = chat.conditions[(key, dates[-1])]
                rows.append(f'  <div class="app-row{column["who"]}">{_av(column)}<div class="app-name"><strong>' + esc(column["name"]) + '</strong>'
                            f'<span class="muted small">{esc(column["maker"])}</span></div>'
                            f'<div class="app-how"><span>{esc(conditions_line(conditions))}</span>'
                            f'<span class="muted small">Version the app showed: {esc(conditions["shown_model"])} · '
                            f'last asked {esc(full_date(dates[-1]))} · {esc(_plural(len(dates), "time"))} so far'
                            f'{esc(" · " + SAMPLE_WORD if chat.demo else "")}</span>'
                            + (f'<span class="muted small">{esc(conditions["note"])}</span>' if conditions["note"] else '')
                            + (f'<span class="mono small">{esc(column["attribution"])}</span>' if column["attribution"] else '')
                            + f'</div>{_style(styles[key], chat.demo)}</div>\n')
            if rows:
                # A group's name appears only when there are several groups.
                body.append((f'  <h3 class="apps-name">{esc(thread["name"])}</h3>\n' if len(chat.threads) > 1 else '')
                            + '  <div class="apps">\n' + "".join(rows) + '  </div>\n')
        body.append('</section>\n')
        if chat.real:
            from . import archive_chatbots   # lazy: only a real chat-app page reads the owner's aside
            aside = archive_chatbots.aside()
            if aside:
                body.append(f'<p class="aside"><span class="aside-mark mono">Joke</span>{esc(aside)}</p>\n')
    else:
        body.append(f'<section class="group">\n  <p class="muted trend-none">{esc(NOBODY_ASKED)} The first answers are on their way.</p>\n</section>\n')
    body.append('<section id="method" class="method">\n  <h2 class="display">Our house rules</h2>\n  <div class="method-grid">\n'
                '    <div><strong>Same words</strong><span>Every app gets the exact same words, every month. You can read them on '
                'each question\'s page. We ask for one short sentence, which is why the replies are short.</span></div>\n'
                '    <div><strong>Not a word changed</strong><span>We keep each reply exactly as it came. The page may fold a long '
                'one, but the words never change.</span></div>\n'
                '    <div><strong>No answer? We leave it out</strong><span>When an app refuses or dodges, we make a note and leave it '
                'off the page.</span></div>\n'
                '    <div><strong>It depends on the day</strong><span>Account, country, search on or off: any of these can nudge a '
                'reply. We write them down and tell you when something was different. So a new answer does not always mean the '
                'app changed its mind.</span></div>\n'
                '    <div><strong>Humans too</strong><span>The Human answers were written by people. Quoted means one person\'s exact '
                'words. Summed up means we boiled several replies down to one line. No names.</span></div>\n'
                '    <div><strong>Not affiliated</strong><span>We\'re independent, with no ties to the companies named. Their names '
                'only tell you which app answered.</span></div>\n'
                '  </div>\n</section>\n')
    return _page(view, _head("How it works · Answers Over Time", "../", versions, CHATBOTS_DESCRIPTION, _meta(view, "", track="chat")),
                 _bar(view, "../", "", versions, "chatbots", here="chatbots.html", standalone=standalone, sibling=sibling), body, _foot(view, "chat", []))


# ---- Share cards --------------------------------------------------------------------------------------------------

def _card_size(text: str) -> str:
    """The size classes of a card's question, by its length: one for a card of up to three panels and one
    (f1-f3) for a card of four, where the question has less room. Chosen so that the question is never cut."""
    length = len(text)
    return (("xl" if length <= 26 else "l" if length <= 48 else "m" if length <= 90 else "s")
            + (" card-q-f1" if length <= 34 else " card-q-f2" if length <= 84 else " card-q-f3"))


# The fitted card in cqw (a hundredth of its width), as the stylesheet sets it: the height of the safe box's body, the
# width of a bubble's text, the answer's font size by the number of bubbles and the lines its clamp allows.
CARD_BODY, CARD_TEXT_WIDTH = 108.33, 69.0
CARD_ANSWER = {1: (4.6, 7), 2: (3.9, 4), 3: (3.5, 3), 4: (3.1, 3)}
CARD_BEFORE_ROOM = 105.0       # show the earlier answers on a card of several only if this estimate is not passed


def _lines(text: str, size: float, most: int, width: float = CARD_TEXT_WIDTH) -> int:
    """A cautious count of the lines a text takes at a font size (cqw): 0.5 em per character is wider than
    the body face runs on average, so the count errs on the high side."""
    return min(most, max(1, math.ceil(len(text) * 0.5 * size / width)))


def _card_room(board: dict, panels: list[tuple[dict, dict, dict]]) -> bool:
    """Whether a card of several bubbles surely has room for the earlier answer above every changed one.
    An estimate from text lengths, on the high side; when in doubt the card stays as it is and says
    "changed" in words only (the earlier answer is on the single-answerer card and on the page)."""
    count = min(max(len(panels), 1), 4)
    size, most = CARD_ANSWER[count]
    length = len(board["text"])
    if count > 3:
        font, most_lines = (7.0, 2) if length <= 34 else (5.6, 3) if length <= 84 else (3.9, 4)
    else:
        font, most_lines = (10.0, 2) if length <= 26 else (8.2, 3) if length <= 48 else (6.0, 4) if length <= 90 else (5.0, 6)
    used = 3.6 + _lines(board["text"], font, most_lines, 80.9) * font * 1.08 + 5.0 + 3 * 1.8 + 1.3 * (count - 1)
    for column, _period, cell in panels:
        used += 5.6 + _lines(cell["text"], size, most) * size * 1.26
        previous = _previous(board, column["key"], cell) if cell["state"] == "changed" else None
        if previous:
            used += 3.6 + _lines("Before · " + previous[0]["label"] + " " + previous[1]["text"], 2.6, 2) * 2.6 * 1.3
    return used <= CARD_BEFORE_ROOM


def _card_page(view: _View, board: dict, panels: list[tuple[dict, dict, dict]], versions: dict[str, str], *, title: str,
               kicker: str, one: bool, track: str, extra: str = "", empty: str = "No answers yet.",
               group: str = "", part: str = "") -> str:
    """A 9:16 share card. Laid out for a 1080 by 1920 image that a video app covers in part: the question,
    the names, the answers and the one honesty line sit inside x 65-940, y 270-1440 (`.card-safe`); the
    wordmark is above and the small print below. At any other window size the same page is ordinary
    flowing text that can be read and zoomed. An answer is clamped to three lines on the image; when one
    may be cut the card says "full answer on the site". Recorded text is never edited. `group` (only when
    there are several answerer groups) is that group's name, shown before the hook inside the safe box;
    `part` ("card 2 of 3") goes to the small print."""
    sample = board["demo"]
    question_id = board["id"]
    earlier = one or _card_room(board, panels)
    cut = any(card_cut(cell["text"], len(panels)) for _column, _period, cell in panels)
    out = [_head(title, "../../", versions, board["text"], _meta(view, "../", board=board, track=track)).replace(
               "<body>", '<body class="card-page">'),
           f'<main id="main" class="card card-safe{" card-four" if len(panels) > 3 else ""}" data-question-id="{esc(question_id)}" '
           f'data-provenance="{SAMPLE if sample else "recorded"}"><div class="card-in">\n'
           f'  <header class="card-top"><a class="card-brand" href="../{esc(view.page(question_id))}">'
           f'<img class="card-mark" src="../../static/archive-mark.svg?v={versions["archive-mark.svg"]}" alt="" width="57" height="24" />'
           'Answers Over Time</a>'
           f'<span class="card-kicker">{esc(kicker)}</span></header>\n'
           '  <div class="card-body">\n'
           + (f'  <p class="card-hook">{esc(" · ".join(text for text in (group, board["hook"]) if text))}</p>\n'
              if board["hook"] or group else '')
           + f'  <h1 class="card-q card-q-{_card_size(board["text"])}">{esc(board["text"])}</h1>\n'
           f'  <div class="card-panels {"one" if one else "n-" + str(max(len(panels), 1))}">\n']
    for column, period, cell in panels:
        human = column["kind"] == "human"
        changed = cell["state"] == "changed"
        word = "new answer" if human else "changed"
        form = f" · {FORM_WORDS[cell['form']]}" if human and cell["form"] else ""
        credit = f"{column['attribution']} · " if column["attribution"] else ""
        previous = _previous(board, column["key"], cell) if changed else None
        before = (f'<p class="card-before"><span class="mono">Before · {esc(previous[0]["label"])}</span> {esc(previous[1]["text"])}</p>'
                  if previous and earlier else '')
        out.append(f'    <section class="card-panel{" card-panel-changed" if changed else ""}'
                   f'{" card-panel-human" if human else ""}{column["who"]}"{data_attrs(record(board, column, period, cell))}>'
                   f'{_av(column)}<div class="stack">{before}<div class="bubble">'
                   f'<div class="card-meta mono"><span class="card-label">{esc(column["name"])}</span>'
                   f'<span class="card-when">{esc(credit)}{esc(period["label"])}{esc(form)}{" · " + word if changed else ""}'
                   f'{" · mixed answers" if cell["state"] == "mixed" else ""}</span></div>'
                   f'<p class="card-answer">{esc(cell["text"])}</p></div></div></section>\n')
    if not panels:
        out.append(f'    <section class="card-none"><p class="card-answer card-empty">{esc(empty)}</p></section>\n')
    honesty = view.tag(track)
    if not sample and track == "chat":
        honesty = CHAT_CARD_FOOT
    # Owner's decision, 20 Nov 2026: a card carries no "read-only · no payments · id" line. What stays below the
    # safe box is only what a reader needs: which card of a set this is, and that a long answer goes on.
    small = " · ".join(([part] if part else []) + ([CARD_FULL] if cut else []))
    out.append('  </div>\n'
               f'  <p class="card-honest mono">{esc(honesty)}</p>\n'
               '  </div>\n'
               + (f'  <footer class="card-foot mono"><span>{esc(small)}</span>{extra}</footer>\n' if small or extra else '') +
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


def _chat_cards(board: dict) -> dict[str, tuple[list, str, str]]:
    """Every share card of one chat-app question: {name suffix: (panels, part, name)}. The suffix is what
    follows `card/<ID>` in the file name: "" (the first card), "2", "3" (further cards of the first group),
    "<group id>", "<group id>-2" (the cards of a later group), "<app label>", "human" (one answerer).

    A card holds at most CARD_APPS bubbles. A group with more answerers than that is spread over several
    cards, in the thread's order, rather than set in smaller text. The Human's answer, when there is one,
    is the last bubble of the FIRST card of every group. `part` is "" or "card 2 of 3"."""
    out: dict[str, tuple[list, str, str]] = {}
    people = [(column, *found) for column in board["columns"] if column["kind"] == "human"
              for found in [_latest(board, column["key"])] if found]
    groups = [thread for thread in board["threads"] if thread["id"] != HUMAN]
    for place, thread in enumerate(groups):
        apps = [(column, *found) for column in thread["columns"] if column["kind"] != "human"
                for found in [_latest(board, column["key"])] if found]
        first = CARD_APPS - len(people) if len(apps) + len(people) > CARD_APPS else len(apps)
        pages = [apps[:first] + people] + [apps[n:n + CARD_APPS] for n in range(first, len(apps), CARD_APPS)]
        stem = "" if place == 0 else thread["id"]
        label = thread["name"] or "All answers"
        for number, panels in enumerate(pages, 1):
            suffix = stem if number == 1 else f"{stem}-{number}" if stem else str(number)
            part = f"card {number} of {len(pages)}" if len(pages) > 1 else ""
            out[suffix] = (panels, part, label if number == 1 else f"{label} {number}")
    if not groups:
        out[""] = (people, "", "All answers")
    for column in board["columns"]:
        found = _latest(board, column["key"])
        out[column["key"]] = ([(column, *found)] if found else [], "", column["name"])
    return out


def render_chatbot_card(payload: dict, question_id: str, which: str = ALL_CARD, versions: dict[str, str] | None = None) -> str:
    """A share card of the chat-app track. `which` is "all" (`card/<ID>.html`: the latest answers of up to
    four answerers of the first group, the Human's among them) or the part of a card's file name after
    `<ID>-`: one column's key (`card/<ID>-<app label>.html`, `card/<ID>-human.html`: that one's latest
    answer at a larger size), a number (`card/<ID>-2.html`: a further card of the first group), a group's
    id or a group's id and a number (`_chat_cards`). Never the owner's aside. Raises KeyError for a
    question or a card that is not in the track."""
    view = _View(payload)
    chat = view.chat
    versions = versions or asset_versions()
    if question_id not in chat.by_id or not view.shown(question_id):
        raise KeyError(question_id)
    board = _chat_board(view, question_id)
    cards = _chat_cards(board)
    suffix = "" if which == ALL_CARD else which
    if suffix not in cards or which == "":
        raise KeyError(which)
    panels, part, name = cards[suffix]
    single = suffix in chat.name
    several = len(chat.groups) > 1
    group = next((thread["name"] for thread in board["threads"] if panels and panels[0][0] in thread["columns"]), "") \
        if several and not single else ""
    # A card whose AI answerers are all models asked by software says so: a model is never called a chat app.
    kinds = {panel[0]["kind"] for panel in panels if panel[0]["kind"] != "human"}
    kicker = "AI models" if kinds == {"model"} else "Chatbots" if several else "Chat apps"
    return _card_page(view, board, panels, versions,
                      title=f"{board['text']} · {name if single or several or part else 'everyone'} · card · Answers Over Time",
                      kicker=kicker, one=single, track="chat", empty="No answers yet.",
                      group=group, part=part)


# ---- Data files: the same answers for machines ------------------------------------------------------------------

README = """Answers Over Time: data files
=============================

These files hold the same answers as the pages next to them, in a form a program
can read. They are built from the same reviewed record as the pages (archive.json at the
site root) and hold nothing that is not in it. Read-only; nothing here costs anything.

Files
-----
answers.jsonl   One answer per line, each line a complete JSON object.
<ID>.json       One question (Q01, T002, C003 ...): the question, exactly what was asked,
                and every answer to it.
README.txt      This file.

What an answer is
-----------------
An answer with provenance "ai-generated" is the output of an AI system, recorded verbatim.
It was not edited, shortened or corrected. It may be wrong. It is not advice.

An answer with provenance "human-written" was written by people.
human_form says how: "quoted" is one person's words, copied exactly; "summarised" (the
pages say "summed up") is our short summary of several replies. There are no names in it.

An answer with provenance "scripted-sample" is NOT a real answer. It was written as a
sample to show how the pages work. No AI and no person was asked. Never treat a
scripted-sample record as something an AI or a person said. Every record of a sample
site carries this value, in every line.

Fields
------
schema              The version of this format: %(schema)s
id                  A stable id of this answer: question / answerer / the time it was asked.
question_id         Q.. (core list), T... (trending list) or C... (chat-app list).
question            The question in plain words.
prompt              Exactly what was asked, character for character.
answerer            The answerer's label. answerer_name is the name shown on the pages.
answerer_kind       chat-app | model | human
maker               The company behind a chat app ("" when not recorded).
version             The version name the app showed, or the model version asked ("" when none).
collected           The date it was asked, YYYY-MM-DD. period is the label the pages use.
method              Model answers only; a chat-app or human record has no such field.
                    asked-by-server     the archive's own program called the model
                    sent-in-unverified  an outside program sent the answer; nothing about it was checked
                    scripted-sample     a sample; nothing was asked
provenance          ai-generated | human-written | scripted-sample
human_form          quoted | summarised (human answers only; otherwise null)
state               answered   the first answer from this answerer
                    same       the same as its answer before
                    changed    the text differs from its answer before
                    mixed      asked several times in one check; the tries did not all agree
                    refused, no-answer, hidden   not shown on the pages; text is always null
                               (a refusal's words are not published anywhere)
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

"changed" only means the words are different (letter case, spacing and punctuation at the
ends are ignored). It does not say which answer is right, and a chat app's reply can change
because of how it was asked that day, not because the app changed its mind.

Product names belong to their owners. This archive is independent and is not affiliated
with, sponsored by or endorsed by them.
""" % {"schema": DATA_SCHEMA}

# The same text for a standalone archive, which holds the chat-app track only: the lines about model
# questions and model answers are left out or shortened. Each pair is (in README, in README_CHAT).
_README_CHAT_ONLY = (
    ("One question (Q01, T002, C003 ...): the question,", "One question (C001, C002 ...): the question,"),
    ("Q.. (core list), T... (trending list) or C... (chat-app list).", "C... (the chat-app list)."),
    ("chat-app | model | human", "chat-app | human"),
    ("The version name the app showed, or the model version asked (", "The version name the app showed ("),
    ("method              Model answers only; a chat-app or human record has no such field.\n"
     "                    asked-by-server     the archive's own program called the model\n"
     "                    sent-in-unverified  an outside program sent the answer; nothing about it was checked\n"
     "                    scripted-sample     a sample; nothing was asked\n", ""),
    ("                    mixed      asked several times in one check; the tries did not all agree\n", ""),
    ("chat app; source and date for human answers; endpoint name and sample counts\n"
     "                    for a model). Never a web address, a key, a file path or a username.",
     "chat app; source and date for human answers). Never a web address, a key, a file path\n"
     "                    or a username."),
)


def _readme_chat() -> str:
    text = README
    for full, short in _README_CHAT_ONLY:
        if text.count(full) != 1:
            raise ValueError("the data README no longer holds a line its chat-app-only form replaces")
        text = text.replace(full, short)
    return text


README_CHAT = _readme_chat()
# And for a standalone archive that holds a model answerer: the three lines that then also speak of a model.
_README_CHAT_MODELS = (
    ("chat-app | human", "chat-app | model | human"),
    ("The version name the app showed (", "The version name the app showed, or the model's name ("),
    ("chat app; source and date for human answers). Never a web address, a key, a file path\n"
     "                    or a username.",
     "chat app; the model's name and where it ran for a model; source and date for human\n"
     "                    answers). Never a web address, a key, a file path or a username."),
)
README_CHAT_MODELS = README_CHAT
for _full, _short in _README_CHAT_MODELS:
    if README_CHAT_MODELS.count(_full) != 1:
        raise ValueError("the chat-app data README no longer holds a line its form with models replaces")
    README_CHAT_MODELS = README_CHAT_MODELS.replace(_full, _short)


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


def render_data(payload: dict, *, chat_only: bool = False) -> dict[str, str]:
    """The data files, keyed by their path under `archive/`: `data/<ID>.json` per shown question,
    `data/answers.jsonl` (every answer, one per line) and `data/README.txt`. ASCII-escaped JSON.
    `chat_only` (a standalone archive): the README describes the chat-app track only."""
    view = _View(payload)
    out: dict[str, str] = {}
    lines: list[str] = []
    for question in view.chat_questions + view.questions + view.trending:
        data = question_data(view, question["id"])
        out[f"data/{question['id']}.json"] = json.dumps(data, ensure_ascii=True, indent=1) + "\n"
        lines += [_dump(answer) for answer in data["answers"]]
    out["data/answers.jsonl"] = "".join(line + "\n" for line in lines)
    models = any(column["kind"] == "model" for column in view.chat.columns)
    out["data/README.txt"] = (README_CHAT_MODELS if models else README_CHAT) if chat_only else README
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
    fonts the stylesheet names and each font's licence text."""
    names = list(ASSETS)
    for font in sorted(set(re.findall(r'url\("(fonts/[A-Za-z0-9._-]+)"\)', (STATIC / "archive.css").read_text(encoding="utf-8")))):
        names.append(font)
        licence = f"{font.rsplit('.', 1)[0]}-OFL.txt"
        if (STATIC / licence).is_file():
            names.append(licence)
    return names


def render_site(payload: dict, *, standalone: bool = False, sibling: str | None = None) -> dict[str, str]:
    """Every archive page and data file, keyed by its path under `archive/`. A hidden question (`HIDDEN`)
    has no page, no card and no data file. Standalone (the public site): the chat-app track only. The
    payload is reduced to it first (archive_chatbots.site_payload), so no model page, card or data file
    is made and no page links to one."""
    versions = asset_versions()
    if standalone:
        if not isinstance(payload, dict):
            raise ValueError("archive payload is malformed: top level is not an object")
        from . import archive_chatbots   # lazy, as in render_chatbots
        payload = archive_chatbots.site_payload(payload)
    view = _View(payload)
    options = {"standalone": standalone, "sibling": sibling if standalone else None}
    pages = {"index.html": render_index(payload, versions, **options), "chatbots.html": render_chatbots(payload, versions, **options)}
    if not standalone:
        pages["models.html"] = render_models(payload, versions, **options)
        pages["trending.html"] = render_trending(payload, versions, **options)
    for folder, questions in (("q", view.questions), ("t", view.trending), ("c", view.chat_questions)):
        for question in questions:
            board = _board(view, question["id"])
            pages[f"{folder}/{question['id']}.html"] = _question_page(view, board, versions, **options)
            pages[f"card/{question['id']}.html"] = render_card(payload, question["id"], versions)
            if folder == "c":
                for suffix in _chat_cards(board):
                    if suffix:
                        pages[f"card/{question['id']}-{suffix}.html"] = render_chatbot_card(payload, question["id"], suffix, versions)
    pages.update(render_data(payload, chat_only=standalone))
    return pages
