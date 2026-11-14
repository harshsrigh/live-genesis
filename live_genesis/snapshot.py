"""Build the public site of Answers Over Time as a static folder (no server, no API, no database).

    python -m live_genesis.snapshot OUTDIR [--archive-only]

The site is the chat-app comparison and nothing else (owner decision: the public site publishes
nothing of the model track): `archive/` (`index.html`, `chatbots.html`, `c/`, the chat-app cards and
data files; archive_pages.py, plain HTML, no script), an `archive.json` holding the chat-app track
(a schema marker, `archive_chatbots.SITE_SCHEMA`, and the `chatbots` block), the stylesheet, mark,
icon and fonts those pages use, and a root `index.html` that sends the visitor to
`archive/index.html`. Every address is relative, so the folder works under a sub-path.
`--archive-only` is accepted and changes nothing: it has been the only kind of site since the agent
society was removed (14 November 2026), and the workflow still passes it.

By default only a database whose archive holds nothing but scripted demo answers may be built from,
so a public snapshot cannot quietly contain recorded replies; pass --allow-non-demo only after
reviewing that content. The database is read through a read-only connection; no model is called.

Publishing recorded (non-demo) replies goes through reviewed JSON committed to the repository:

    python -m live_genesis.snapshot --export              # database -> published/ (guards run first)
    python -m live_genesis.snapshot --approve --i-have-read-it
    python -m live_genesis.snapshot --check               # what CI runs: hash intact and reviewed
    python -m live_genesis.snapshot OUTDIR --from-data published   # render without a database

`--export` writes `archive.json` and `MANIFEST.json` and nothing else. Its `archive.json` is the
chat-app track ALONE: replies the owner collected from chat apps BY HAND (archive_chatbots.py). Model
runs and answers in the database are not exported, and with no reply entered by hand the export is
refused. A screenshot is exported as its sha256 only. `--check` holds the file to: hash, ASCII, strict
JSON, closed key sets, guards, counts, `reviewed`; it refuses a chat app that is not in
`archive_chatbots.json`, an answer to a question outside its run's recorded id list, an answer
without recorded conditions, and a file that carries the model track. The manifest says
`"contents": "archive"`. A manifest that says `"full"` is an export of the kind that also held the
society's `overview.json`; that kind is no longer made, checked or rendered.

Only the owner exports and approves. Publishing is not reversible in practice: git history keeps the data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from . import archive, archive_chatbots, archive_pages, guards
from .core import utcnow

STATIC = Path(__file__).resolve().parent / "static"
PUBLISHED = Path(__file__).resolve().parent.parent / "published"   # reviewed data, committed by the owner
SCHEMA = "live-genesis-published/5"   # /5: the manifest says what the folder holds ("contents")
ARCHIVE_FILE, MANIFEST_FILE = "archive.json", "MANIFEST.json"
FILES = (ARCHIVE_FILE, MANIFEST_FILE)
# The manifest's "contents". Every export made now is ARCHIVE: Answers Over Time alone. FULL and LEGACY_DATA_FILE
# name what an export made before the agent society was removed could hold; both are only recognised to be refused.
FULL, ARCHIVE = "full", "archive"
LEGACY_DATA_FILE = "overview.json"
ARCHIVE_MANIFEST_KEYS = {"schema", "contents", "file", "exported_at", "archive", "reviewed", "reviewed_at"}
_EXPORTED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+Z-]{0,26}")   # [0-9], not \d: \d also matches non-ASCII digits
NO_ANSWERS = "it holds no answers, so there is nothing to publish"
FULL_REMOVED = ("it is a full export (the kind that also held overview.json). That kind was removed with the agent society "
                "and is no longer checked or rendered; export again: python -m live_genesis.snapshot --export")
# Relative address of the other published snapshot (demo <-> recorded); nothing else is accepted.
SIBLING = re.compile(r"(?:\.\./|[a-z0-9-]+/)")
_HASH_VALUE = re.compile(r"[0-9a-f]{64}")


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


def _check_outdir(out: Path, sibling: str | None) -> None:
    if sibling is not None and not SIBLING.fullmatch(sibling):
        raise RuntimeError("--sibling must be a relative folder such as demo/ or ../")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise RuntimeError("Output folder must be empty or not exist yet")


def _render_archive_only(out: Path, sibling: str | None, archive_payload: bytes) -> None:
    """The site: the chat-app track's pages, the static files they use, the data behind them
    (archive.json holds the `chatbots` block and a schema marker) and a root page that leads to
    them. Nothing of the model track."""
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
    if chat is not data:   # the database's whole payload: write its chat-app part; a reviewed export is copied byte for byte
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


def build(outdir: str | Path, *, allow_non_demo: bool = False, sibling: str | None = None,
          archive_only: bool = True) -> dict:
    """Database (read-only) -> the static site. `archive_only` is accepted for existing callers and
    changes nothing: this is the only kind of site."""
    out = Path(outdir)
    _check_outdir(out, sibling)
    answers = _capture_archive()
    info = {"generated_at": utcnow(), "demo_only": bool(answers["demo_only"]),
            "note": "Static copy of recorded data. Nothing is running behind this page."}
    if not info["demo_only"] and not allow_non_demo:
        raise RuntimeError("The archive in this database is not scripted-demo-only. Review the content, then pass "
                           "--allow-non-demo to build from it anyway.")
    _render_archive_only(out, sibling, _encode(answers))   # generated before anything is written: a failure leaves no half-built folder
    return info


# ---- reviewed data: export -> approve -> check -> render ------------------------------------

def guard_hits(node: object, kind: str = "archive", ref: object = None, field: str = "") -> list[str]:
    """Run the content guards over every string in the export. Names record and category only,
    never the matched text. Scans everything rather than a list of known fields, so a field
    added to the payload later is covered; the one exemption is a well-formed `*_hash` value."""
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
    return hits


SERVER_MARKS = "\u00b7\u2192"   # the separator and arrow the server itself writes into summaries


def non_ascii_report(node: object, kind: str = "archive", ref: object = None, field: str = "") -> list[str]:
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
    data = _loads((folder / ARCHIVE_FILE).read_bytes())
    return ["archive " + line for kind, value in data.items() for line in non_ascii_report(value, kind, None, kind)]


def _chat_block_counts(chat: dict) -> dict:
    return {"runs": len(chat["runs"]), "answers": len(chat["answers"]), "apps": [app["label"] for app in chat["apps"]],
            "human_answers": len(chat["human_answers"]), "questions": len(chat["questions"])}


def _chat_counts(answers: dict) -> dict:
    """The manifest's "archive" block (without the hash)."""
    return {"file": ARCHIVE_FILE, "chatbots": _chat_block_counts(answers["chatbots"])}


def _chat_problem(answers: object) -> str | None:
    """Why an exported archive.json may not be published, or None. It is
    the chat-app track alone: the schema marker and the `chatbots` block, hand-entered replies only,
    the apps and questions of the checked-in configuration, closed keys."""
    if isinstance(answers, dict) and answers.get("schema") in (archive.PAYLOAD_SCHEMA, archive.PAYLOAD_SCHEMA_CHATBOTS):
        return ("it holds the model track; an export of Answers Over Time only holds the chat-app track alone "
                "(an export made by an earlier version must be exported and reviewed again)")
    return archive_chatbots.site_problem(answers) or archive_chatbots.export_problem(answers["chatbots"])


def _chat_answers(answers: dict) -> list:
    chat = answers.get("chatbots", {})
    return chat.get("answers", []) + chat.get("human_answers", [])


def _write_manifest(folder: Path, manifest: dict) -> None:
    (folder / MANIFEST_FILE).write_bytes((json.dumps(manifest, indent=1) + "\n").encode("utf-8"))


def _is_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def export_published(target: str | Path | None = None, *, replace: bool = False, archive_only: bool = True) -> dict:
    """Database (read-only) -> published/archive.json + MANIFEST.json, unreviewed. archive.json holds
    the chat-app track only (replies entered by hand); model runs in the database are not exported.
    `archive_only` is accepted for existing callers and changes nothing: this is the only kind of export."""
    out = Path(target) if target is not None else PUBLISHED
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise RuntimeError(f"{out} exists and is not a plain folder")
    if out.exists() and any(out.iterdir()) and not replace:
        raise RuntimeError(f"{out} already holds an export. Pass --replace to overwrite it "
                           "(the new export starts unreviewed).")
    answers = archive_chatbots.site_payload(_capture_archive())   # the chat-app track alone: no model answer is exported
    if not any(not run["demo"] for run in answers["chatbots"]["runs"]):
        raise RuntimeError("Export refused; nothing was written. Answers Over Time holds no chat-app reply entered by "
                           "hand (scripted answers are never exported, and the export leaves the model track out), "
                           "so there is nothing to review: the scripted demo is already the public site.")
    problem = _chat_problem(answers) or (NO_ANSWERS if not _chat_answers(answers) else None)
    if problem:
        raise RuntimeError(f"Export refused; nothing was written. Answers Over Time: {problem}.")
    hits = [f"archive {hit}" for hit in _scan(answers)]
    if hits:
        raise RuntimeError("Export refused; nothing was written. Content guards matched:\n  " + "\n  ".join(hits))
    archive_payload = _encode(answers)
    listed = {**_chat_counts(answers), "sha256": hashlib.sha256(archive_payload).hexdigest()}
    # No top-level sha256: the one data file is archive.json and its hash is in the "archive" block.
    # archive.json carries no export time of its own, so exported_at is a note here, not a checked value.
    manifest = {"schema": SCHEMA, "contents": ARCHIVE, "file": ARCHIVE_FILE, "exported_at": utcnow(),
                "archive": listed, "reviewed": False, "reviewed_at": None}
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.iterdir()):   # only reached with --replace: an earlier export of any kind is removed whole
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    (out / ARCHIVE_FILE).write_bytes(archive_payload)
    _write_manifest(out, manifest)
    return manifest


def check_published(target: str | Path | None = None, *, require_reviewed: bool = True) -> dict:
    """The one gate used by `--check` (CI), `--approve` and `--from-data`. Raises RuntimeError.

    Hash, folder contents, counts and the content guards are all re-checked here, so a
    hand-edited manifest cannot carry text past the regex floor. Every file in the folder is always
    checked: anything besides archive.json and MANIFEST.json is refused, and so is a manifest that
    says the folder holds a full export (FULL_REMOVED)."""
    folder = Path(target) if target is not None else PUBLISHED
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError(f"{folder}: the export folder is missing or unreadable (not a plain folder)")
    names = sorted(entry.name for entry in folder.iterdir())
    extra = [name for name in names if name not in FILES]
    if extra:
        raise RuntimeError(f"{folder}: unexpected entries ({len(extra)}); an export folder must hold only "
                           f"{ARCHIVE_FILE} and {MANIFEST_FILE}"
                           + (f", and {LEGACY_DATA_FILE} is there" if LEGACY_DATA_FILE in extra else ""))
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
    if isinstance(kind, str) and kind == FULL:
        raise RuntimeError(f"{folder}: refused: {FULL_REMOVED}")
    if not isinstance(kind, str) or kind != ARCHIVE:
        raise RuntimeError(f"{folder}: the manifest does not say what the export holds (\"contents\" must be "
                           f"\"{ARCHIVE}\")")
    if manifest.get("file") != ARCHIVE_FILE:
        raise RuntimeError(f"{folder}: manifest is not a {SCHEMA} manifest")
    if set(manifest) != ARCHIVE_MANIFEST_KEYS:
        raise RuntimeError(f"{folder}: the manifest must hold exactly " + ", ".join(sorted(ARCHIVE_MANIFEST_KEYS)))
    exported_at, reviewed, reviewed_at = manifest.get("exported_at"), manifest.get("reviewed"), manifest.get("reviewed_at")
    if not isinstance(exported_at, str) or not _EXPORTED_AT.fullmatch(exported_at):
        raise RuntimeError(f"{folder}: the manifest's exported_at is not a timestamp")
    if type(reviewed) is not bool:
        raise RuntimeError(f"{folder}: the manifest's reviewed is not true or false")
    if not (isinstance(reviewed_at, str) and _EXPORTED_AT.fullmatch(reviewed_at)) and (reviewed or reviewed_at is not None):
        raise RuntimeError(f"{folder}: the manifest's reviewed_at is not a timestamp"
                           + (" (it must be one when reviewed is true)" if reviewed else " or null"))
    if not _is_file(folder / ARCHIVE_FILE):
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is missing or unreadable (must be a regular file)")
    try:
        archive_payload = (folder / ARCHIVE_FILE).read_bytes()
    except OSError as err:
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is missing or unreadable ({type(err).__name__})") from None
    # ---- archive.json: hash, ASCII, strict JSON, shape, counts, guards ----
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
    # The chat-app track alone, and it must itself hold hand-entered replies.
    problem = _chat_problem(answers) or (NO_ANSWERS if not _chat_answers(answers) else None)
    if problem:
        raise RuntimeError(f"{folder}: {ARCHIVE_FILE} is refused: {problem}")
    if {k: v for k, v in listed.items() if k != "sha256"} != _chat_counts(answers):
        raise RuntimeError(f"{folder}: the counts in the manifest do not match {ARCHIVE_FILE}")
    hits = [f"archive {hit}" for hit in _scan(answers)]
    if hits:
        raise RuntimeError(f"{folder}: refused. Content guards matched:\n  " + "\n  ".join(hits))
    if require_reviewed and manifest.get("reviewed") is not True:
        raise RuntimeError(f"{folder}: not approved (\"reviewed\" is not true). Read {ARCHIVE_FILE}, then run "
                           "python -m live_genesis.snapshot --approve --i-have-read-it")
    return manifest


def approve_published(target: str | Path | None = None, *, confirmed: bool = False) -> dict:
    """Mark an intact export as reviewed. The check re-runs hash, counts and guards first."""
    folder = Path(target) if target is not None else PUBLISHED
    manifest = check_published(folder, require_reviewed=False)
    if not confirmed:
        raise RuntimeError(f"Not approved. Read {folder / ARCHIVE_FILE} in full, then repeat with --i-have-read-it")
    manifest.update(reviewed=True, reviewed_at=utcnow())
    _write_manifest(folder, manifest)
    return manifest


def build_from_data(outdir: str | Path, published: str | Path | None = None, *, sibling: str | None = None,
                    archive_only: bool = True) -> dict:
    """Render the site from reviewed, committed data. No database is opened. The export is checked
    whole and its archive.json is copied byte for byte. `archive_only` is accepted for existing
    callers and changes nothing."""
    out = Path(outdir)
    folder = Path(published) if published is not None else PUBLISHED
    _check_outdir(out, sibling)
    manifest = check_published(folder)
    _render_archive_only(out, sibling, (folder / ARCHIVE_FILE).read_bytes())
    return {"generated_at": manifest["exported_at"], "demo_only": False, "manifest": manifest}


def _describe(manifest: dict) -> str:
    answered = manifest["archive"]
    return (f"  exported {manifest['exported_at']} · Answers Over Time only: the chat-app track "
            "(no model answer is exported)\n"
            f"  {ARCHIVE_FILE}: {answered['chatbots']['runs']} chat-app run(s), {answered['chatbots']['answers']} chat-app "
            f"answer row(s), {answered['chatbots']['human_answers']} human answer row(s)"
            f"\n  NOTE: {answered['chatbots']['answers']} row(s) in {ARCHIVE_FILE} are chat-app replies ENTERED BY HAND "
            f"({', '.join(answered['chatbots']['apps'])}; {answered['chatbots']['runs']} run(s)). They cannot be verified. Read "
            "each service's current terms about publishing its output before you push."
            f"\n  {ARCHIVE_FILE} sha256 {answered['sha256']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static, read-only site of Answers Over Time (the chat-app comparison)")
    parser.add_argument("outdir", nargs="?")
    parser.add_argument("--allow-non-demo", action="store_true",
                        help="Build although the database's archive holds recorded (non-demo) answers")
    parser.add_argument("--from-data", metavar="DIR", default=None,
                        help="Render OUTDIR from an approved export folder instead of a database")
    parser.add_argument("--sibling", metavar="HREF", default=None,
                        help="Relative link to the other published snapshot (demo/ or ../), shown on the page")
    parser.add_argument("--archive-only", action="store_true",
                        help="Accepted and without effect: the chat-app comparison of Answers Over Time is the only "
                             "thing this command builds or exports")
    steps = parser.add_mutually_exclusive_group()
    steps.add_argument("--export", metavar="DIR", nargs="?", const=str(PUBLISHED), default=None,
                       help="Write archive.json (the chat-app track) and MANIFEST.json for review (default folder: published/)")
    steps.add_argument("--approve", metavar="DIR", nargs="?", const=str(PUBLISHED), default=None,
                       help="Mark an export as reviewed; needs --i-have-read-it")
    steps.add_argument("--check", metavar="DIR", nargs="?", const=str(PUBLISHED), default=None,
                       help="Exit non-zero unless the export is intact and approved (used by CI)")
    parser.add_argument("--replace", action="store_true", help="With --export: overwrite an existing export")
    parser.add_argument("--i-have-read-it", action="store_true", help="With --approve: confirm the review")
    args = parser.parse_args()
    try:
        if args.export or args.approve or args.check:
            if args.outdir or args.from_data:
                parser.error("--export, --approve and --check take no OUTDIR and no --from-data")
            if args.archive_only and not args.export:
                parser.error("--approve and --check take no --archive-only: the manifest says what the export holds")
            if args.export:
                manifest = export_published(args.export, replace=args.replace)
                print(f"Export written to {args.export} (NOT yet approved). No model was called.\n{_describe(manifest)}")
                print(f"Next: read {Path(args.export) / ARCHIVE_FILE} in full, then run\n"
                      "  python -m live_genesis.snapshot --approve --i-have-read-it")
            elif args.approve:
                checked = check_published(args.approve, require_reviewed=False)
                print(_describe(checked))
                marked = published_non_ascii(args.approve)
                if marked:
                    print(f"Non-ASCII characters (written as \\uXXXX escapes in the file; counts per Unicode category, "
                          f"the server's own · and → left out) in {len(marked)} text(s):\n  " + "\n  ".join(marked))
                else:
                    print("No non-ASCII characters in the file besides the server's own separators.")
                print("The guards are regular expressions and do not understand meaning. Approving means you read "
                      f"every answer in {Path(args.approve) / ARCHIVE_FILE}.\n"
                      "Once pushed, the data stays in git history even if you withdraw it later.")
                manifest = approve_published(args.approve, confirmed=args.i_have_read_it)
                print(f"Approved at {manifest['reviewed_at']}. Commit the folder to publish it.")
            else:
                print(f"OK: export is intact and approved.\n{_describe(check_published(args.check))}")
            return
        if not args.outdir:
            parser.error("OUTDIR is required unless --export, --approve or --check is given")
        if args.from_data:
            info = build_from_data(args.outdir, args.from_data, sibling=args.sibling)
        else:
            info = build(args.outdir, allow_non_demo=args.allow_non_demo, sibling=args.sibling)
    except RuntimeError as err:
        parser.error(str(err))
    origin = "rendered from approved data, " if args.from_data else ""
    print(f"Static snapshot written to {args.outdir} ({origin}captured {info['generated_at']}, "
          f"{'scripted demo data only' if info['demo_only'] else 'INCLUDES NON-DEMO DATA'}). No model was called.")
    print("Archive only: the chat-app comparison of Answers Over Time and its static files. No model-track page or data "
          "was written.")


if __name__ == "__main__":
    main()
