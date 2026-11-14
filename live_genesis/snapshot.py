"""Export the read-only observatory as a static folder (no server, no API, no database).

    python -m live_genesis.snapshot OUTDIR

The result can be opened from any static host. It is a frozen copy: the page labels itself
"STATIC SNAPSHOT · NOT RUNNING" and never polls. Besides the front page and `stats.html` it holds
one page per post (`post/N.html`) and per agent (`profile/N.html`), written by pages.py from the
exported `overview.json` itself; every address is relative, so the folder works under a sub-path. By default only a database that holds
nothing but scripted demo data may be exported, so a public snapshot cannot quietly
contain real agent output; pass --allow-non-demo only after reviewing that content.
Reads the database through a read-only connection and calls no model.

Next to the page it writes the onboarding kit for agent developers (see agentkit.py):
`llms.txt`, `agent-guide.md`, `openapi.json` and the reference client `agent_client.py`.
They state no address for the API unless the operator passes `--api-base URL`.

Publishing recorded (non-demo) data goes through reviewed JSON committed to the repository:

    python -m live_genesis.snapshot --export              # database -> published/ (guards run first)
    python -m live_genesis.snapshot --approve --i-have-read-it
    python -m live_genesis.snapshot --check               # what CI runs: hash intact and reviewed
    python -m live_genesis.snapshot OUTDIR --from-data published   # render without a database

The export also carries `archive.json` (Answers Over Time: questions, runs and answers; mock
answers are never in it) under the same hash, manifest, guard and ASCII rules, and every
snapshot holds the archive pages under `archive/` (archive_pages.py; plain HTML, no script).

Replies the owner collected from chat apps BY HAND (archive_chatbots.py) are part of `archive.json`
(its optional `chatbots` block, payload schema /5): they pass the same guards, the closed key sets and
`--check`, which also refuses a chat app that is not in `archive_chatbots.json`, an answer to a question
outside its run's recorded id list and an answer without recorded conditions. A screenshot is exported
as its sha256 only. An archive that holds only such replies can be exported.

`--export --archive-only` writes `archive.json` and `MANIFEST.json` and nothing else: no society
table is read (no post, document, proposal, game or profile) and no `overview.json` exists. Its
`archive.json` is the chat-app track ALONE (owner decision: the public site publishes nothing of the
model track): a schema marker (`archive_chatbots.SITE_SCHEMA`) and the `chatbots` block. Model runs
and answers in the database are not exported by it, and with no reply entered by hand it is refused.
`--check` holds such a file to the same rules as before (hash, ASCII, strict JSON, closed key sets,
guards, counts, `reviewed`) and refuses one that carries the model track.
The manifest says which kind it is (`"contents": "full"` or `"archive"`); `--approve`, `--check`
and `--from-data` read it there. An archive export renders with `--from-data ... --archive-only` only.

A full `--export` (unchanged) carries the whole archive with every model answer: an answer an outside
agent submitted over the API is exported with that agent's name and self-declared model, and the
export summary says how many there are. It prints each recorded run's stored status and refuses
while a run is still marked `running` (an archive-run in progress, or one that was killed and so
never closed); `--include-unfinished` exports such a run as it stands.

`--archive-only` (with a database or with `--from-data`; what the public site is built with) writes
the chat-app comparison of Answers Over Time and nothing else: `archive/` (`index.html`,
`chatbots.html`, `c/`, the chat-app cards and data files), an `archive.json` reduced to the chat-app
track, the stylesheet, mark, icon and fonts those pages use, and a root `index.html` that sends the
visitor to `archive/index.html`. No model page (`models.html`, `trending.html`, `q/`, `t/`), no model
card or data file, no overview, no post, profile or stats page, no script, no agent kit.

See docs/FIRST-REAL-ROUND.md. Publishing is not reversible in practice: git history keeps the data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from . import agentkit, archive, archive_chatbots, archive_pages, guards, pages, tasks
from .core import overview, utcnow
from .web import CONTENT_SECURITY_POLICY

STATIC = Path(__file__).resolve().parent / "static"
PUBLISHED = Path(__file__).resolve().parent.parent / "published"   # reviewed data, committed by the owner
SCHEMA = "live-genesis-published/5"   # /2: the folder also holds archive.json; /3: archive.json carries the trending track; /4: per answer, endpoint name, reported model, digest, pricing basis; /5: the manifest says what the folder holds ("contents": "full" or "archive")
DATA_FILE, ARCHIVE_FILE, MANIFEST_FILE = "overview.json", "archive.json", "MANIFEST.json"
FILES = (DATA_FILE, ARCHIVE_FILE, MANIFEST_FILE)
# What an export folder holds, by the manifest's "contents": the whole observatory plus the answer
# archive, or the answer archive alone (no society data was read to make it).
FULL, ARCHIVE = "full", "archive"
KINDS = (FULL, ARCHIVE)
ARCHIVE_MANIFEST_KEYS = {"schema", "contents", "file", "exported_at", "archive", "reviewed", "reviewed_at"}
_EXPORTED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+Z-]{0,26}")   # [0-9], not \d: \d also matches non-ASCII digits
NO_ANSWERS = "it holds no answers, so there is nothing to publish"
SOURCES = ("demo", "api", "engine", "unknown")
# Relative address of the other published snapshot (demo <-> recorded); nothing else is accepted.
SIBLING = re.compile(r"(?:\.\./|[a-z0-9-]+/)")
_HASH_VALUE = re.compile(r"[0-9a-f]{64}")


# References for automated readers, added to <head> only: nothing visible changes, nothing
# is fetched by the browser, and the Content-Security-Policy needs no new source.
KIT_LINKS = (
    '<link rel="alternate" type="text/plain" href="llms.txt" title="Index for LLM readers" />',
    '<link rel="alternate" type="text/markdown" href="agent-guide.md" title="Agent API guide" />',
    '<link rel="service-doc" type="text/markdown" href="agent-guide.md" />',
    '<link rel="service-desc" type="application/vnd.oai.openapi+json" href="openapi.json" />',
)


def _capture() -> dict:
    """The public overview plus the snapshot note; read-only, no model call."""
    data = overview(readonly=True)
    metrics = data.get("metrics", {})
    # A task the operator added by hand is not scripted demo data, so it ends "demo only" too.
    demo_only = (data.get("mode") == "DEMO" and not metrics.get("tokens") and not metrics.get("cost_usd")
                 and all(task.get("demo") for task in data.get("tasks") or []))
    data["snapshot"] = {"generated_at": utcnow(), "demo_only": demo_only,
                        "note": "Static copy of recorded data. Nothing is running behind this page."}
    return data


def _capture_archive() -> dict:
    """The public Answers Over Time payload (never a mock row); read-only, no model call."""
    try:
        return archive.public_payload(readonly=True)
    except archive.ArchiveRefused as err:
        raise RuntimeError(str(err)) from None


def _encode(data: dict) -> bytes:
    """ASCII only: every non-ASCII character is written as a \\uXXXX escape, so nothing invisible
    (zero-width, tag, bidirectional ...) can hide from someone reading the file. The page parses
    the escapes back to the same text. allow_nan=False: NaN/Infinity are not JSON."""
    return json.dumps(data, ensure_ascii=True, allow_nan=False, indent=1).encode("ascii")


class _Malformed(ValueError):
    pass


def _strict_pairs(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise _Malformed("duplicate key")
        out[key] = value
    return out


def _strict_constant(name: str):
    raise _Malformed("NaN or Infinity")


def _strict_float(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise _Malformed("number out of range")
    return value


def _loads(payload: bytes) -> object:
    """Strict JSON: no NaN/Infinity literal, no number that overflows to infinity, no duplicate
    key (a browser keeps the last one, a reviewer may read the first)."""
    return json.loads(payload.decode("utf-8"), parse_constant=_strict_constant, parse_float=_strict_float,
                      object_pairs_hook=_strict_pairs)


# Top-level keys app.js reads, and the JSON type each must have.
SHAPE = {"mode": str, "agents": list, "posts": list, "proposals": list, "events": list, "pulse": list, "games": list,
         "documents": list, "ledger": dict, "governance": dict, "guide": dict, "metrics": dict, "snapshot": dict}


# Keys added after the first exports. Rule: a file WITHOUT the key is an older export and is read
# as "no tasks" (its hash, counts and review stay valid); a file WITH the key must hold a JSON
# array of objects, and its manifest then counts the tasks too (_counts).
OPTIONAL_SHAPE = {"tasks": list}


def _shape_problem(data: object) -> str | None:
    if not isinstance(data, dict):
        return "top level is not an object"
    for key, kind in SHAPE.items():
        if not isinstance(data.get(key), kind):
            return f"key {key} is missing or not a JSON {'string' if kind is str else 'array' if kind is list else 'object'}"
    for key, kind in OPTIONAL_SHAPE.items():
        if key in data and not isinstance(data[key], kind):
            return f"key {key} is not a JSON array"
    for key in ("agents", "posts", "proposals", "events", "pulse", "games", "documents", *OPTIONAL_SHAPE):
        if not all(isinstance(item, dict) for item in data.get(key, ())):
            return f"key {key} holds an entry that is not an object"
    return None


def _kit(api_base: str | None) -> tuple[str | None, dict[str, str], bool]:
    try:
        api_base = agentkit.clean_api_base(api_base)
    except ValueError as err:
        raise RuntimeError(str(err)) from None
    include_client = agentkit.REFERENCE_CLIENT.is_file()
    return api_base, {"llms.txt": agentkit.llms_txt(api_base, include_client=include_client),
                      "agent-guide.md": agentkit.agent_guide(api_base, include_client=include_client),
                      "openapi.json": agentkit.openapi_json(api_base)}, include_client


def _check_outdir(out: Path, sibling: str | None) -> None:
    if sibling is not None and not SIBLING.fullmatch(sibling):
        raise RuntimeError("--sibling must be a relative folder such as demo/ or ../")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise RuntimeError("Output folder must be empty or not exist yet")


# Pages of the site root that app.js fills from overview.json. The first is the front page.
SHELL_PAGES = ("index.html", "stats.html")
# A static host sends no headers of ours, so the policy travels in every page itself.
CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}" />\n  '


def _versioned(name: str) -> str:
    """`static/NAME?v=HASH`. Static hosts let browsers cache files for a while under the same
    address; without the content hash a changed emblem, script or stylesheet keeps showing its
    old version to returning visitors."""
    asset = STATIC / name
    if not asset.is_file():
        raise RuntimeError(f"a page refers to a missing static file: {name}")
    return f"static/{name}?v={hashlib.sha256(asset.read_bytes()).hexdigest()[:12]}"


def _shell(name: str, sibling: str | None) -> str:
    """One page of static/ rewritten for a static host: relative addresses, versioned assets,
    the policy as a <meta>, and (front page only) the references to the agent kit."""
    html = (STATIC / name).read_text(encoding="utf-8")
    attrs = 'data-overview="overview.json"' + (f' data-sibling="{sibling}"' if sibling else "")
    for old, new in (('<html lang="en">', f'<html lang="en" {attrs}>'),
                     ('"/static/', '"static/'), ('href="/"', 'href="./"')):
        if old not in html:
            raise RuntimeError(f"{name} no longer contains {old!r}; update snapshot.py")
        html = html.replace(old, new)
    html = re.sub(r'"static/([^"?#]+)"', lambda match: f'"{_versioned(match.group(1))}"', html)
    marker = '<meta charset='
    if html.count(marker) != 1:
        raise RuntimeError(f"{name} must contain exactly one <meta charset>; update snapshot.py")
    html = html.replace(marker, CSP_META + marker)
    stylesheet = '<link rel="stylesheet"'
    if html.count(stylesheet) != 1 or html.index(stylesheet) > html.index("</head>"):
        raise RuntimeError(f"{name} must link exactly one stylesheet inside <head>; update snapshot.py")
    if name == SHELL_PAGES[0]:
        html = html.replace(stylesheet, "".join(link + "\n  " for link in KIT_LINKS) + stylesheet)
    return html


def item_pages(payload: bytes) -> dict[str, str]:
    """`post/N.html` and `profile/N.html` for everything in the exported file. Built from the
    bytes that are published (in --from-data mode: the reviewed file), never from a database."""
    data = _loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("the exported overview is not a JSON object")
    post_ids, agent_ids = pages.item_ids(data)
    out: dict[str, str] = {}
    for kind_ids, path, render in ((post_ids, pages.post_path, pages.render_post), (agent_ids, pages.agent_path, pages.render_agent)):
        for item_id in kind_ids:
            html = render(data, item_id, root="../", asset=_versioned, head_extra=CSP_META)
            if html is not None:
                out[path(item_id)] = html
    return out


def _render(out: Path, payload: bytes, kit: dict[str, str], include_client: bool, sibling: str | None,
            archive_payload: bytes) -> None:
    # Everything is generated before anything is written, so a failure leaves no half-built folder.
    try:
        archive_html = archive_pages.render_site(_loads(archive_payload))
    except ValueError as err:
        raise RuntimeError(str(err)) from None
    shells = {name: _shell(name, sibling) for name in SHELL_PAGES}
    items = item_pages(payload)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, out / "static", ignore=shutil.ignore_patterns("*.html"))
    for name, html in {**shells, **items}.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(html, encoding="utf-8")
    (out / "overview.json").write_bytes(payload)
    (out / ARCHIVE_FILE).write_bytes(archive_payload)   # the data behind archive/, byte for byte
    for name, text in archive_html.items():
        target = out / "archive" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages
    for name, text in kit.items():
        (out / name).write_text(text, encoding="utf-8")
    if include_client:
        shutil.copyfile(agentkit.REFERENCE_CLIENT, out / agentkit.REFERENCE_CLIENT_NAME)


def _render_archive_only(out: Path, sibling: str | None, archive_payload: bytes) -> None:
    """Answers Over Time on its own, and of it the chat-app track only: its pages, the static files
    they use, the data behind them (archive.json holds the `chatbots` block and a schema marker) and a
    root page that leads to them. No page or overview of the society and nothing of the model track."""
    try:
        data = _loads(archive_payload)
        if not isinstance(data, dict):
            raise ValueError("archive payload is malformed: top level is not an object")
        # The public site is the chat-app track only: the model track is neither rendered nor copied.
        chat = archive_chatbots.site_payload(data)
        archive_html = archive_pages.render_site(chat, standalone=True, sibling=sibling)
        home = archive_pages.render_home()
    except ValueError as err:
        raise RuntimeError(str(err)) from None
    if chat is not data:   # a full payload: write its chat-app part; an archive export is copied byte for byte
        archive_payload = _encode(chat)
    assets = archive_pages.static_files()
    missing = [name for name in assets if not (STATIC / name).is_file()]
    if missing:
        raise RuntimeError(f"the archive pages refer to a missing static file: {missing[0]}")
    out.mkdir(parents=True, exist_ok=True)
    for name in assets:
        target = out / "static" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STATIC / name, target)
    (out / "index.html").write_text(home, encoding="utf-8")
    (out / ARCHIVE_FILE).write_bytes(archive_payload)   # the data behind archive/, byte for byte
    for name, text in archive_html.items():
        target = out / "archive" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages


def _no_kit(api_base: str | None) -> None:
    if api_base is not None:
        raise RuntimeError("--api-base cannot be used with --archive-only: the agent kit is not written there")


def build(outdir: str | Path, *, allow_non_demo: bool = False, api_base: str | None = None,
          sibling: str | None = None, archive_only: bool = False) -> dict:
    out = Path(outdir)
    # Generated before anything is written, so a failure leaves no half-built folder.
    if archive_only:
        _no_kit(api_base)
    else:
        api_base, kit, include_client = _kit(api_base)
    _check_outdir(out, sibling)
    data = _capture()
    answers = _capture_archive()
    # A recorded archive answer makes the database non-demo, whatever the world's own mode is.
    data["snapshot"]["demo_only"] = data["snapshot"]["demo_only"] and answers["demo_only"]
    if not data["snapshot"]["demo_only"] and not allow_non_demo:
        raise RuntimeError("Database is not scripted-demo-only (mode %s). Review the content, then pass "
                           "--allow-non-demo to export it anyway." % data.get("mode"))
    if archive_only:   # the same gate as above, although the overview itself is not written
        _render_archive_only(out, sibling, _encode(answers))
    else:
        _render(out, _encode(data), kit, include_client, sibling, _encode(answers))
    return {**data["snapshot"], "api_base": api_base}


# ---- reviewed data: export -> approve -> check -> render ------------------------------------

def guard_hits(node: object, kind: str = "overview", ref: object = None, field: str = "") -> list[str]:
    """Run the content guards over every string in the export. Names record and category only,
    never the matched text. Scans everything rather than a list of known fields, so a field
    added to the overview later is covered; the one exemption is a well-formed `*_hash` value."""
    hits: list[str] = []
    if isinstance(node, dict):
        ref = node.get("id", ref)
        for key, value in node.items():
            if isinstance(key, str) and guards.has_forbidden_character(key):
                hits.append(f"{kind} id {ref if ref is not None else '-'}: a field name {guards.CHARACTER_RULE}")
            hits += guard_hits(value, kind, ref, str(key))
    elif isinstance(node, list):
        for item in node:
            hits += guard_hits(item, kind, ref, field)
    elif isinstance(node, str) and not (field.endswith("_hash") and _HASH_VALUE.fullmatch(node)):
        # The character rule of the write path, applied again here: rows written before that rule
        # existed are not rewritten, so this is what keeps them out of a published file.
        reason = (guards.CHARACTER_RULE if guards.has_forbidden_character(node, multiline=True)
                  else guards.content_reason(node))
        if reason:
            hits.append(f"{kind} id {ref if ref is not None else '-'}, field {field}: {reason}")
    return hits


def _scan(data: dict) -> list[str]:
    hits: list[str] = []
    for kind, value in data.items():
        hits += guard_hits(value, kind, None, kind)
    # Operator task text: the same guards ran above; the two task-only rules of the write path
    # (no reward wording, no link) are applied again, as the character rule is for agent text.
    for task in data.get("tasks") or []:
        for field in ("title", "body", "close_note"):
            text = task.get(field) if isinstance(task, dict) else None
            reason = tasks.text_problem(text) if isinstance(text, str) else None
            if reason and not guards.content_reason(text):
                hits.append(f"tasks id {task.get('id', '-')}, field {field}: {reason}")
    return hits


SERVER_MARKS = "\u00b7\u2192"   # the separator and arrow the server itself writes into summaries


def non_ascii_report(node: object, kind: str = "overview", ref: object = None, field: str = "") -> list[str]:
    """One line per string that holds non-ASCII characters: record, field and a count per Unicode
    general category. Never the text. For the reviewer: these are the \\uXXXX escapes in the file."""
    lines: list[str] = []
    if isinstance(node, dict):
        ref = node.get("id", ref)
        for key, value in node.items():
            lines += non_ascii_report(value, kind, ref, str(key))
    elif isinstance(node, list):
        for item in node:
            lines += non_ascii_report(item, kind, ref, field)
    elif isinstance(node, str):
        counts = guards.non_ascii_categories("".join(ch for ch in node if ch not in SERVER_MARKS))
        if counts:
            lines.append(f"{kind} id {ref if ref is not None else '-'}, field {field}: "
                         + ", ".join(f"{name} {n}" for name, n in sorted(counts.items())))
    return lines


def published_non_ascii(target: str | Path | None = None) -> list[str]:
    """non_ascii_report for an export folder that already passed check_published."""
    folder = Path(target) if target is not None else PUBLISHED
    lines: list[str] = []
    for name in (DATA_FILE, ARCHIVE_FILE):
        if name == DATA_FILE and not (folder / name).exists():   # an archive export has no overview.json
            continue
        data = _loads((folder / name).read_bytes())
        prefix = "" if name == DATA_FILE else "archive "
        lines += [prefix + line for kind, value in data.items() for line in non_ascii_report(value, kind, None, kind)]
    return lines


def _counts(data: dict) -> tuple[dict, dict]:
    by_source = (data.get("metrics") or {}).get("actions_by_source") or {}
    return ({name: int(by_source.get(name, 0)) for name in SOURCES},
            {name: len(data.get(name) or []) for name in ("agents", "posts", "proposals", "events", "games", "documents",
                                                          *(key for key in OPTIONAL_SHAPE if key in data))})


def _chat_block_counts(chat: dict) -> dict:
    return {"runs": len(chat["runs"]), "answers": len(chat["answers"]), "apps": [app["label"] for app in chat["apps"]],
            "human_answers": len(chat["human_answers"]), "questions": len(chat["questions"])}


def _chat_counts(answers: dict) -> dict:
    """The manifest's "archive" block (without the hash) of an export of Answers Over Time only."""
    return {"file": ARCHIVE_FILE, "chatbots": _chat_block_counts(answers["chatbots"])}


def _chat_problem(answers: object) -> str | None:
    """Why the archive.json of an export of Answers Over Time only may not be published, or None. It is
    the chat-app track alone: the schema marker and the `chatbots` block, hand-entered replies only,
    the apps and questions of the checked-in configuration, closed keys."""
    if isinstance(answers, dict) and answers.get("schema") in (archive.PAYLOAD_SCHEMA, archive.PAYLOAD_SCHEMA_CHATBOTS):
        return ("it holds the model track; an export of Answers Over Time only holds the chat-app track alone "
                "(an export made by an earlier version must be exported and reviewed again)")
    return archive_chatbots.site_problem(answers) or archive_chatbots.export_problem(answers["chatbots"])


def _archive_counts(answers: dict) -> dict:
    by_source = {name: sum(1 for a in answers["answers"] if a.get("source") == name) for name in archive.SOURCES}
    trending = answers["trending"]
    counts = {"file": ARCHIVE_FILE, "answers_by_source": by_source, "runs": len(answers["runs"]),
              "questions": len(answers["questions"]), "question_set": answers["question_set"], "set_hash": answers["set_hash"],
              "trending": {"questions": len(trending["questions"]), "question_set": trending["question_set"],
                           "set_hash": trending["set_hash"], "version": trending["version"]}}
    if "chatbots" in answers:   # only for a file that holds the hand-entered chat-app track: any other manifest is unchanged
        counts["chatbots"] = _chat_block_counts(answers["chatbots"])
    return counts


def _chat_answers(answers: dict) -> list:
    chat = answers.get("chatbots", {})
    return chat.get("answers", []) + chat.get("human_answers", [])


def _archive_problem(answers: object) -> str | None:
    """Why an archive.json may not be published, or None. Mock answers never leave the database, and
    scripted demo answers are not reviewed data (the demo site is built from the script itself)."""
    problem = archive.payload_problem(answers)
    if problem:
        return problem
    if any(a["source"] == "mock" for a in answers["answers"]):
        return "it holds mock answers, which are never published"
    problem = archive.export_problem(answers)   # the endpoint fields: present on every answer, set on server calls only
    if problem:
        return problem
    if any(a["source"] == "demo" for a in answers["answers"]) or any(run["demo"] for run in answers["runs"]):
        return "it holds scripted demo answers; the demo is published without review"
    if "chatbots" in answers:
        # Hand-entered chat-app replies and human answers: never scripted ones, only apps and questions of the checked-in
        # configuration, closed keys. The track has its own questions: the core and trending lists need not be locked.
        problem = archive_chatbots.export_problem(answers["chatbots"])
        if problem:
            return problem
    if answers["answers"] and not answers["locked"] and not answers["trending"]["locked"]:
        return "it holds answers but the question list is not locked"
    for track, ids in (("core", {q["id"] for q in answers["questions"]}), ("trending", {q["id"] for q in answers["trending"]["questions"]})):
        holder = answers if track == "core" else answers["trending"]
        if not holder["locked"] and (any(a["question"] in ids for a in answers["answers"]) or any(run["asked"][track] for run in answers["runs"])):
            return ("it holds answers but the question list is not locked" if track == "core"
                    else "it holds answers to trending questions that are not locked")
    return archive.closed_problem(answers)   # exactly the keys an export writes, and the values the code derives


def _write_manifest(folder: Path, manifest: dict) -> None:
    (folder / MANIFEST_FILE).write_bytes((json.dumps(manifest, indent=1) + "\n").encode("utf-8"))


def _is_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_first(folder: Path, kind: str) -> str:
    """The files of an export the owner has to read, by kind."""
    return f"{folder / ARCHIVE_FILE}" if kind == ARCHIVE else f"{folder / DATA_FILE} and {folder / ARCHIVE_FILE}"


def _run_lines(states: list[dict]) -> list[str]:
    """One line per recorded run for the export summary: number, stored status, answers recorded."""
    return [f"  Answers Over Time run {s['number']}: {s['status']}, {s['answers']} answer(s) recorded"
            + (" (UNFINISHED: exported as it stands)" if s["status"] == "running" else "") for s in states]


def export_published(target: str | Path | None = None, *, replace: bool = False, archive_only: bool = False,
                     include_unfinished: bool = False, notes: list[str] | None = None) -> dict:
    """Database (read-only) -> published/overview.json + archive.json + MANIFEST.json, unreviewed.
    With archive_only: archive.json + MANIFEST.json, no society table is read, and archive.json holds
    the chat-app track only (replies entered by hand); model runs in the database are not exported.
    A full export is refused while a recorded run is still marked running, unless include_unfinished.
    `notes`, when given, receives one line per recorded model run of a full export (number, stored
    status, answers recorded)."""
    out = Path(target) if target is not None else PUBLISHED
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise RuntimeError(f"{out} exists and is not a plain folder")
    if out.exists() and any(out.iterdir()) and not replace:
        raise RuntimeError(f"{out} already holds an export. Pass --replace to overwrite it "
                           "(the new export starts unreviewed).")
    # Read before the answers: a run that is closed here is whole in the payload. Model runs are not part of an
    # archive-only export, so their state is not read for it.
    states = [] if archive_only else archive.run_states()
    data = None if archive_only else _capture()   # archive only: the society tables are not read at all
    answers = _capture_archive()
    if archive_only:
        answers = archive_chatbots.site_payload(answers)   # the chat-app track alone: no model answer is exported
        if not any(not run["demo"] for run in answers["chatbots"]["runs"]):
            raise RuntimeError("Export refused; nothing was written. Answers Over Time holds no chat-app reply entered by "
                               "hand (scripted answers are never exported, and --archive-only leaves the model track out), "
                               "so there is nothing to review: the scripted demo is already the public site.")
    else:
        data["snapshot"]["demo_only"] = data["snapshot"]["demo_only"] and answers["demo_only"]
        if data["snapshot"]["demo_only"]:
            raise RuntimeError("Export refused; nothing was written. This database holds scripted demo data only, "
                               "so there is nothing to review: the scripted demo is already the public site.")
    if archive_only:
        problem = _chat_problem(answers) or (NO_ANSWERS if not _chat_answers(answers) else None)
    else:
        problem = _archive_problem(answers)
    if problem:
        raise RuntimeError(f"Export refused; nothing was written. Answers Over Time: {problem}.")
    hits = ([] if archive_only else _scan(data)) + [f"archive {hit}" for hit in _scan(answers)]
    if hits:
        raise RuntimeError("Export refused; nothing was written. Content guards matched:\n  " + "\n  ".join(hits))
    problem = None if archive_only else archive.set_hash_problem(answers)   # the chat-app questions are checked against their file above
    if problem:
        raise RuntimeError(f"Export refused; nothing was written. Answers Over Time: {problem}.")
    if not archive_only and archive.run_states() != states:
        raise RuntimeError("Export refused; nothing was written. Answers Over Time changed while it was being read "
                           "(a run is recording answers); run the export again.")
    running = [str(s["number"]) for s in states if s["status"] == "running"]
    if running and not include_unfinished:
        raise RuntimeError(f"Export refused; nothing was written. Answers Over Time run {', '.join(running)} is still marked "
                           "running: an archive-run is in progress, or it was killed before it could close the run (such a "
                           "run stays \"running\" for good). Wait for it to finish, or pass --include-unfinished to export "
                           "it as it stands.")
    archive_payload = _encode(answers)
    listed = {**(_chat_counts(answers) if archive_only else _archive_counts(answers)),
              "sha256": hashlib.sha256(archive_payload).hexdigest()}
    if archive_only:
        # No top-level sha256: the one data file is archive.json and its hash is in the "archive" block.
        # archive.json carries no export time of its own, so exported_at is a note here, not a checked value.
        manifest = {"schema": SCHEMA, "contents": ARCHIVE, "file": ARCHIVE_FILE, "exported_at": utcnow(),
                    "archive": listed, "reviewed": False, "reviewed_at": None}
    else:
        payload = _encode(data)
        by_source, records = _counts(data)
        manifest = {"schema": SCHEMA, "contents": FULL, "file": DATA_FILE, "sha256": hashlib.sha256(payload).hexdigest(),
                    "exported_at": data["snapshot"]["generated_at"], "mode": data.get("mode"),
                    "actions_by_source": by_source, "records": records, "archive": listed,
                    "reviewed": False, "reviewed_at": None}
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.iterdir()):   # only reached with --replace: an earlier export of either kind is removed whole
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    if not archive_only:
        (out / DATA_FILE).write_bytes(payload)
    (out / ARCHIVE_FILE).write_bytes(archive_payload)
    _write_manifest(out, manifest)
    if notes is not None:
        notes += _run_lines(states)
    return manifest


def _check_overview(folder: Path, manifest: dict, payload: bytes) -> dict:
    """overview.json of a full export against its manifest: hash, ASCII, strict JSON, shape, counts."""
    if hashlib.sha256(payload).hexdigest() != manifest.get("sha256"):
        raise RuntimeError(f"{folder}: {DATA_FILE} does not match the sha256 in the manifest. "
                           "It changed after export; export and review again.")
    if not payload.isascii():
        raise RuntimeError(f"{folder}: {DATA_FILE} holds a non-ASCII byte. An export writes every non-ASCII "
                           "character as a \\uXXXX escape so that none can hide from the reviewer; export again.")
    try:
        data = _loads(payload)
    except _Malformed as err:
        raise RuntimeError(f"{folder}: {DATA_FILE} is not strict JSON ({err})") from None
    except ValueError:
        raise RuntimeError(f"{folder}: {DATA_FILE} is not an exported overview") from None
    problem = _shape_problem(data)
    if problem:
        raise RuntimeError(f"{folder}: {DATA_FILE} is not an exported overview ({problem})")
    try:
        captured, demo_only = data["snapshot"]["generated_at"], data["snapshot"]["demo_only"]
    except (KeyError, TypeError):
        raise RuntimeError(f"{folder}: {DATA_FILE} is not an exported overview") from None
    if captured != manifest.get("exported_at") or data.get("mode") != manifest.get("mode"):
        raise RuntimeError(f"{folder}: manifest and {DATA_FILE} disagree on export time or data mode")
    if demo_only is not False:
        raise RuntimeError(f"{folder}: {DATA_FILE} is scripted demo data only; the demo is published without review")
    try:
        by_source, records = _counts(data)
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError(f"{folder}: {DATA_FILE} is not an exported overview") from None
    if manifest.get("actions_by_source") != by_source or manifest.get("records") != records:
        raise RuntimeError(f"{folder}: the counts in the manifest do not match {DATA_FILE}")
    return data


def check_published(target: str | Path | None = None, *, require_reviewed: bool = True) -> dict:
    """The one gate used by `--check` (CI), `--approve` and `--from-data`. Raises RuntimeError.

    Hash, folder contents, counts and the content guards are all re-checked here, so a
    hand-edited manifest cannot carry text past the regex floor. The manifest's "contents" picks
    the rules: "full" needs overview.json, "archive" refuses it, so changing the word by hand fails
    either way and every file in the folder is always checked."""
    folder = Path(target) if target is not None else PUBLISHED
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError(f"{folder}: the export folder is missing or unreadable (not a plain folder)")
    names = sorted(entry.name for entry in folder.iterdir())
    extra = [name for name in names if name not in FILES]
    if extra:   # the kind is not known yet, so the files are not named
        raise RuntimeError(f"{folder}: unexpected entries ({len(extra)}); an export folder must hold only its data "
                           f"file(s) and {MANIFEST_FILE}")
    if not _is_file(folder / MANIFEST_FILE):
        raise RuntimeError(f"{folder}: {MANIFEST_FILE} is missing or unreadable (must be a regular file)")
    try:
        manifest = _loads((folder / MANIFEST_FILE).read_bytes())
    except (OSError, ValueError) as err:
        raise RuntimeError(f"{folder}: {MANIFEST_FILE} is missing or unreadable ({type(err).__name__})") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise RuntimeError(f"{folder}: manifest is not a {SCHEMA} manifest (an export made by an earlier version "
                           "must be exported and reviewed again)")
    kind = manifest.get("contents")
    if not isinstance(kind, str) or kind not in KINDS:
        raise RuntimeError(f"{folder}: the manifest does not say what the export holds (\"contents\" must be "
                           f"\"{FULL}\" or \"{ARCHIVE}\")")
    full = kind == FULL
    if manifest.get("file") != (DATA_FILE if full else ARCHIVE_FILE):
        raise RuntimeError(f"{folder}: manifest is not a {SCHEMA} manifest")
    if not full:
        if DATA_FILE in names:   # whatever it is (file, folder, link): an archive export never holds one
            raise RuntimeError(f"{folder}: unexpected entries (1); an export of Answers Over Time only must hold only "
                               f"{ARCHIVE_FILE} and {MANIFEST_FILE}, and {DATA_FILE} is there")
        if set(manifest) != ARCHIVE_MANIFEST_KEYS:
            raise RuntimeError(f"{folder}: the manifest of an export of Answers Over Time only must hold exactly "
                               + ", ".join(sorted(ARCHIVE_MANIFEST_KEYS)))
    elif not _is_file(folder / DATA_FILE):
        raise RuntimeError(f"{folder}: {MANIFEST_FILE} or {DATA_FILE} is missing or unreadable (must be regular files)")
    exported_at, reviewed, reviewed_at = manifest.get("exported_at"), manifest.get("reviewed"), manifest.get("reviewed_at")
    if not isinstance(exported_at, str) or not _EXPORTED_AT.fullmatch(exported_at):
        raise RuntimeError(f"{folder}: the manifest's exported_at is not a timestamp")
    if type(reviewed) is not bool:
        raise RuntimeError(f"{folder}: the manifest's reviewed is not true or false")
    if not (isinstance(reviewed_at, str) and _EXPORTED_AT.fullmatch(reviewed_at)) and (reviewed or reviewed_at is not None):
        raise RuntimeError(f"{folder}: the manifest's reviewed_at is not a timestamp"
                           + (" (it must be one when reviewed is true)" if reviewed else " or null"))
    if not _is_file(folder / ARCHIVE_FILE):
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is missing or unreadable (must be a regular file). An export made "
                           "before Answers Over Time has none; export and review again.")
    try:
        payload = (folder / DATA_FILE).read_bytes() if full else b""
        archive_payload = (folder / ARCHIVE_FILE).read_bytes()
    except OSError as err:
        raise RuntimeError(f"{folder}: {f'{DATA_FILE} or {ARCHIVE_FILE}' if full else ARCHIVE_FILE} is missing or "
                           f"unreadable ({type(err).__name__})") from None
    data = _check_overview(folder, manifest, payload) if full else None
    # ---- archive.json: the same rules (hash, ASCII, strict JSON, shape, counts, guards) ----
    listed = manifest.get("archive")
    if not isinstance(listed, dict) or listed.get("file") != ARCHIVE_FILE:
        raise RuntimeError(f"{folder}: the manifest does not describe {ARCHIVE_FILE}")
    if hashlib.sha256(archive_payload).hexdigest() != listed.get("sha256"):
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} does not match the sha256 in the manifest. "
                           "It changed after export; export and review again.")
    if not archive_payload.isascii():
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} holds a non-ASCII byte. An export writes every non-ASCII "
                           "character as a \\uXXXX escape so that none can hide from the reviewer; export again.")
    try:
        answers = _loads(archive_payload)
    except _Malformed as err:
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is not strict JSON ({err})") from None
    except ValueError:
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is not an exported answer archive") from None
    if full:
        problem = _archive_problem(answers)
    else:   # the chat-app track alone, and with no overview beside it, it must itself hold hand-entered replies
        problem = _chat_problem(answers) or (NO_ANSWERS if not _chat_answers(answers) else None)
    if problem:
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is refused: {problem}")
    if {k: v for k, v in listed.items() if k != "sha256"} != (_archive_counts(answers) if full else _chat_counts(answers)):
        raise RuntimeError(f"{folder}: the counts in the manifest do not match {ARCHIVE_FILE}")
    hits = (_scan(data) if full else []) + [f"archive {hit}" for hit in _scan(answers)]
    if hits:
        raise RuntimeError(f"{folder}: refused. Content guards matched:\n  " + "\n  ".join(hits))
    # After the guards: a changed question is first named by what it now says. Full only: the chat-app
    # questions were compared with their checked-in file above.
    problem = archive.set_hash_problem(answers) if full else None
    if problem:
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is refused: {problem}")
    if require_reviewed and manifest.get("reviewed") is not True:
        raise RuntimeError(f"{folder}: not approved (\"reviewed\" is not true). Read "
                           f"{f'{DATA_FILE} and {ARCHIVE_FILE}' if full else ARCHIVE_FILE}, then run "
                           "python -m live_genesis.snapshot --approve --i-have-read-it")
    return manifest


def approve_published(target: str | Path | None = None, *, confirmed: bool = False) -> dict:
    """Mark an intact export as reviewed. The check re-runs hash, counts and guards first."""
    folder = Path(target) if target is not None else PUBLISHED
    manifest = check_published(folder, require_reviewed=False)
    if not confirmed:
        raise RuntimeError(f"Not approved. Read {_read_first(folder, manifest['contents'])} in full, then repeat with --i-have-read-it")
    manifest.update(reviewed=True, reviewed_at=utcnow())
    _write_manifest(folder, manifest)
    return manifest


def build_from_data(outdir: str | Path, published: str | Path | None = None, *, api_base: str | None = None,
                    sibling: str | None = None, archive_only: bool = False) -> dict:
    """Render the site from reviewed, committed data. No database is opened. With archive_only the
    whole export is still checked, but only the chat-app track of its archive.json is rendered and
    written (an archive export holds nothing else and is copied byte for byte). An export of
    Answers Over Time only (manifest "contents": "archive") renders with archive_only and no other way."""
    out = Path(outdir)
    folder = Path(published) if published is not None else PUBLISHED
    if archive_only:
        _no_kit(api_base)
    else:
        api_base, kit, include_client = _kit(api_base)
    _check_outdir(out, sibling)
    manifest = check_published(folder)
    if manifest["contents"] == ARCHIVE:
        if not archive_only:
            raise RuntimeError(f"{folder}: this export holds Answers Over Time only; "
                               "render it with --archive-only")
        payload, info = b"", {"generated_at": manifest["exported_at"], "demo_only": False}
    else:
        payload = (folder / DATA_FILE).read_bytes()   # published byte for byte, so anyone can re-check the hash
        info = _loads(payload)["snapshot"]
    if archive_only:
        _render_archive_only(out, sibling, (folder / ARCHIVE_FILE).read_bytes())
    else:
        _render(out, payload, kit, include_client, sibling, (folder / ARCHIVE_FILE).read_bytes())
    return {**info, "api_base": api_base, "manifest": manifest}


def _describe(manifest: dict) -> str:
    answered = manifest["archive"]
    chat_note = (f"\n  NOTE: {answered['chatbots']['answers']} row(s) in {ARCHIVE_FILE} are chat-app replies ENTERED BY HAND "
                 f"({', '.join(answered['chatbots']['apps'])}; {answered['chatbots']['runs']} run(s)). They cannot be verified. Read "
                 "each service's current terms about publishing its output before you push." if "chatbots" in answered else "")
    if manifest["contents"] == ARCHIVE:
        return (f"  exported {manifest['exported_at']} · Answers Over Time only: the chat-app track "
                "(no society table was read; no model answer is exported)\n"
                f"  {ARCHIVE_FILE}: {answered['chatbots']['runs']} chat-app run(s), {answered['chatbots']['answers']} chat-app "
                f"answer row(s), {answered['chatbots']['human_answers']} human answer row(s)"
                + chat_note + f"\n  {ARCHIVE_FILE} sha256 {answered['sha256']}")
    else:
        counts = ", ".join(f"{name} {manifest['actions_by_source'].get(name, 0)}" for name in SOURCES)
        records = ", ".join(f"{name} {n}" for name, n in manifest["records"].items())
        head = (f"  exported {manifest['exported_at']} · data mode {manifest['mode']}\n"
                f"  actions by source: {counts}\n  records in file: {records}\n  sha256 {manifest['sha256']}\n")
    outside = answered["answers_by_source"].get("api", 0)
    return (head + f"  {ARCHIVE_FILE}: {answered['runs']} run(s), answers by source: "
            + ", ".join(f"{name} {n}" for name, n in answered["answers_by_source"].items())
            + (f"\n  NOTE: {outside} answer(s) in {ARCHIVE_FILE} were submitted by outside agents over the API. Each one "
               "carries that agent's name and its self-declared model." if outside else "")
            + chat_note + f"\n  {ARCHIVE_FILE} sha256 {answered['sha256']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a static, read-only observatory snapshot")
    parser.add_argument("outdir", nargs="?")
    parser.add_argument("--allow-non-demo", action="store_true",
                        help="Export even if the database holds non-demo actions or model usage")
    parser.add_argument("--api-base", metavar="URL", default=None,
                        help="Public base URL of the agent API, written into llms.txt, agent-guide.md and "
                             "openapi.json. Omit it while the API is not publicly hosted (the files then say so).")
    parser.add_argument("--from-data", metavar="DIR", default=None,
                        help="Render OUTDIR from an approved export folder instead of a database")
    parser.add_argument("--sibling", metavar="HREF", default=None,
                        help="Relative link to the other published snapshot (demo/ or ../), shown on the page")
    parser.add_argument("--archive-only", action="store_true",
                        help="Write Answers Over Time only, and of it the chat-app comparison only (archive/, its static "
                             "files, a root page that leads to it): no model-track page or data, no observatory page, no "
                             "overview.json, no script, no agent kit. With --export: write archive.json (the chat-app "
                             "track only) and MANIFEST.json only, without reading the society tables")
    steps = parser.add_mutually_exclusive_group()
    steps.add_argument("--export", metavar="DIR", nargs="?", const=str(PUBLISHED), default=None,
                       help="Write overview.json, archive.json and MANIFEST.json for review (default folder: published/); "
                            "with --archive-only, archive.json and MANIFEST.json only")
    steps.add_argument("--approve", metavar="DIR", nargs="?", const=str(PUBLISHED), default=None,
                       help="Mark an export of either kind as reviewed; needs --i-have-read-it")
    steps.add_argument("--check", metavar="DIR", nargs="?", const=str(PUBLISHED), default=None,
                       help="Exit non-zero unless the export (of either kind, as its manifest says) is intact and approved (used by CI)")
    parser.add_argument("--replace", action="store_true", help="With --export: overwrite an existing export")
    parser.add_argument("--include-unfinished", action="store_true",
                        help="With --export: export although an Answers Over Time run is still marked running (in progress, "
                             "or killed before it closed); the run is exported as it stands")
    parser.add_argument("--i-have-read-it", action="store_true", help="With --approve: confirm the review")
    args = parser.parse_args()
    try:
        if args.export or args.approve or args.check:
            if args.outdir or args.from_data:
                parser.error("--export, --approve and --check take no OUTDIR and no --from-data")
            if args.archive_only and not args.export:
                parser.error("--approve and --check take no --archive-only: the manifest says what the export holds")
            if args.include_unfinished and not args.export:
                parser.error("--include-unfinished is used with --export only")
            if args.export:
                run_lines: list[str] = []
                manifest = export_published(args.export, replace=args.replace, archive_only=args.archive_only,
                                            include_unfinished=args.include_unfinished, notes=run_lines)
                print(f"Export written to {args.export} (NOT yet approved). No model was called.\n{_describe(manifest)}")
                if run_lines:
                    print("\n".join(run_lines))
                print(f"Next: read {_read_first(Path(args.export), manifest['contents'])} in full, then run\n"
                      "  python -m live_genesis.snapshot --approve --i-have-read-it")
            elif args.approve:
                checked = check_published(args.approve, require_reviewed=False)
                print(_describe(checked))
                marked = published_non_ascii(args.approve)
                if marked:
                    print(f"Non-ASCII characters (written as \\uXXXX escapes in the file; counts per Unicode category, "
                          f"the server's own \u00b7 and \u2192 left out) in {len(marked)} text(s):\n  " + "\n  ".join(marked))
                else:
                    print("No non-ASCII characters in the file besides the server's own separators.")
                print("The guards are regular expressions and do not understand meaning. Approving means you read "
                      + ("" if checked["contents"] == ARCHIVE else f"every agent-written text in {Path(args.approve) / DATA_FILE} and ")
                      + f"every answer in {Path(args.approve) / ARCHIVE_FILE}.\n"
                      "Once pushed, the data stays in git history even if you withdraw it later.")
                manifest = approve_published(args.approve, confirmed=args.i_have_read_it)
                print(f"Approved at {manifest['reviewed_at']}. Commit the folder to publish it.")
            else:
                print(f"OK: export is intact and approved.\n{_describe(check_published(args.check))}")
            return
        if not args.outdir:
            parser.error("OUTDIR is required unless --export, --approve or --check is given")
        if args.include_unfinished:
            parser.error("--include-unfinished is used with --export only")
        if args.from_data:
            info = build_from_data(args.outdir, args.from_data, api_base=args.api_base, sibling=args.sibling,
                                   archive_only=args.archive_only)
        else:
            info = build(args.outdir, allow_non_demo=args.allow_non_demo, api_base=args.api_base, sibling=args.sibling,
                         archive_only=args.archive_only)
    except RuntimeError as err:
        parser.error(str(err))
    origin = "rendered from approved data, " if args.from_data else ""
    print(f"Static snapshot written to {args.outdir} ({origin}captured {info['generated_at']}, "
          f"{'scripted demo data only' if info['demo_only'] else 'INCLUDES NON-DEMO DATA'}). No model was called.")
    if args.archive_only:
        print("Archive only: the chat-app comparison of Answers Over Time and its static files. No model-track page or data, "
              "observatory page, overview or agent kit was written.")
        return
    print(f"Agent kit: API base {info['api_base']}." if info["api_base"]
          else "Agent kit: no API base given; the files say the API is not publicly hosted.")


if __name__ == "__main__":
    main()
