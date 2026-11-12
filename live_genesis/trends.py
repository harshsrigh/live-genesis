"""`cli archive-trends`: print what people are searching for today, as candidates for the owner.

Free and model-free. It reads Google's public "trending now" RSS feed for one country and prints
the terms as JSON. That is all it does:

* It never writes a question, never edits `archive_trending.json`, never opens the database and
  never calls a model. The owner reads the list and writes questions by hand (rules:
  docs/ANSWERS-OVER-TIME.md).
* Standard library only. One GET to one fixed https host, 15 second timeout, at most 1 MB read,
  no redirect followed to any other host or scheme, a generic User-Agent, no cookies, no key.
* Everything in the feed is untrusted text. A document with a DOCTYPE or an entity declaration is
  refused before the XML parser sees it; every string has its control and invisible characters
  removed, its whitespace collapsed and its length capped; a string the content guards match
  (an email address, a payment link ...) is dropped and only counted. Output is ASCII-escaped JSON.
* Nothing from the feed is ever executed, followed or sent anywhere.
"""
from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from . import guards

HOST = "trends.google.com"
PATH = "/trending/rss"
USER_AGENT = "Mozilla/5.0 (compatible; rss-reader)"   # generic: no project name, no contact details
TIMEOUT_SECONDS = 15
MAX_BYTES = 1_000_000
GEO = re.compile(r"[A-Z]{2}")
DEFAULT_LIMIT, MAX_LIMIT = 20, 50
TERM_CHARS, TRAFFIC_CHARS, HEADLINE_CHARS, HEADLINES = 80, 16, 160, 2
MAX_ITEMS_READ = 200
_DECLARATION = re.compile(r"\A﻿?\s*<\?xml[^>]*\?>")
_ENCODING = re.compile(r"encoding\s*=\s*[\"']([^\"']*)[\"']", re.I)
_FORBIDDEN_MARKUP = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.I)
NOTE = ("Candidates only, copied from a public feed: untrusted text, not checked for truth. Nothing was written and no "
        "model was called. Write questions by hand, following the rules in docs/ANSWERS-OVER-TIME.md.")


class TrendsRefused(RuntimeError):
    """The feed could not be fetched or is not a feed this command accepts. The message says why."""


def feed_url(geo: str) -> str:
    code = geo.strip().upper() if isinstance(geo, str) else ""
    if not GEO.fullmatch(code):
        raise TrendsRefused("--geo must be a two-letter country code, for example US")
    return f"https://{HOST}{PATH}?geo={code}"


class _SameHostOnly(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to https on the same host; anything else ends the request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(f"https://{HOST}/"):
            raise TrendsRefused("the feed redirected to another address; nothing was fetched from it")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(geo: str = "US") -> bytes:
    """One GET of the public feed. Raises TrendsRefused; never returns more than MAX_BYTES."""
    request = urllib.request.Request(feed_url(geo), headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"})
    opener = urllib.request.build_opener(_SameHostOnly)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise TrendsRefused(f"the feed answered with status {response.status}")
            body = response.read(MAX_BYTES + 1)
    except TrendsRefused:
        raise
    except urllib.error.HTTPError as err:
        raise TrendsRefused(f"the feed answered with status {err.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as err:
        raise TrendsRefused(f"the feed could not be fetched ({type(err).__name__}). Save it in a browser and use --file") from None
    if len(body) > MAX_BYTES:
        raise TrendsRefused("the feed is larger than 1 MB; refused")
    return body


def read_file(path: str) -> bytes:
    try:
        with Path(path).open("rb") as handle:
            body = handle.read(MAX_BYTES + 1)
    except OSError as err:
        raise TrendsRefused(f"--file could not be read ({type(err).__name__})") from None
    if len(body) > MAX_BYTES:
        raise TrendsRefused("the file is larger than 1 MB; refused")
    return body


def clean(value: object, limit: int) -> str:
    """Untrusted feed text -> one printable line: forbidden characters (guards' character rule)
    removed, whitespace collapsed, cut to `limit`. '' when nothing is left or a content guard
    matches."""
    if not isinstance(value, str):
        return ""
    text = " ".join(guards.strip_forbidden_characters(value).split())[:limit].strip()
    return "" if not text or guards.content_reason(text) else text


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse(body: bytes, limit: int = DEFAULT_LIMIT) -> dict:
    """Feed bytes -> {"candidates": [{term, approx_traffic, headlines}], "skipped": n}.
    Refuses anything that is not plain UTF-8 RSS without a DOCTYPE or entity declaration."""
    if len(body) > MAX_BYTES:
        raise TrendsRefused("the feed is larger than 1 MB; refused")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise TrendsRefused("the feed is not UTF-8 text; refused") from None
    declaration = _DECLARATION.match(text)
    if declaration:
        encoding = _ENCODING.search(declaration.group(0))
        if encoding and encoding.group(1).lower().replace("_", "-") not in ("utf-8", "utf8"):
            raise TrendsRefused("the feed declares an encoding other than UTF-8; refused")
        text = text[declaration.end():]
    if _FORBIDDEN_MARKUP.search(text):
        raise TrendsRefused("the feed holds a DOCTYPE or an entity declaration; refused before parsing")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise TrendsRefused("the feed is not well-formed XML") from None
    if _local(root.tag) != "rss":
        raise TrendsRefused("the document is not an RSS feed")
    limit = min(max(int(limit), 1), MAX_LIMIT)
    candidates, seen, skipped = [], set(), 0
    items = [node for node in root.iter() if _local(node.tag) == "item"][:MAX_ITEMS_READ]
    for item in items:
        if len(candidates) >= limit:
            break
        term, traffic, headlines = "", "", []
        for child in item:
            name = _local(child.tag)
            if name == "title" and not term:
                term = clean(child.text, TERM_CHARS)
            elif name == "approx_traffic" and not traffic:
                traffic = clean(child.text, TRAFFIC_CHARS)
            elif name == "news_item":
                for part in child:
                    if _local(part.tag) == "news_item_title" and len(headlines) < HEADLINES:
                        headline = clean(part.text, HEADLINE_CHARS)
                        if headline:
                            headlines.append(headline)
        if not term or term.casefold() in seen:
            skipped += 1
            continue
        seen.add(term.casefold())
        candidates.append({"term": term, "approx_traffic": traffic, "headlines": headlines})
    return {"candidates": candidates, "skipped": skipped}


def report(geo: str = "US", path: str | None = None, limit: int = DEFAULT_LIMIT, today: datetime.date | None = None) -> dict:
    """What `archive-trends` prints. `path` parses a saved feed instead of fetching one."""
    url = feed_url(geo)   # validates --geo also when a file is read: it is printed in trend_source
    code = url.rsplit("=", 1)[-1]
    found = parse(read_file(path) if path else fetch(code), limit)
    day = (today or datetime.datetime.now(datetime.timezone.utc).date()).isoformat()
    return {"source": "saved file (--file)" if path else url, "geo": code, "date": day,
            "trend_source": f"Google Trends {code}, {day}", **found, "note": NOTE}


def add_parsers(sub) -> None:
    parser = sub.add_parser("archive-trends", help="Answers Over Time: print today's trending search terms from Google's public feed as "
                                                   "candidates for trending questions (free; no model; writes nothing)")
    parser.add_argument("--geo", default="US", help="Two-letter country code (default: US)")
    parser.add_argument("--file", help="Parse a saved copy of the feed instead of fetching it")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"How many terms to print (1-{MAX_LIMIT}; default {DEFAULT_LIMIT})")


def handle_command(args, parser) -> bool:
    if args.command != "archive-trends":
        return False
    try:
        if not 1 <= args.limit <= MAX_LIMIT:
            raise TrendsRefused(f"--limit must be between 1 and {MAX_LIMIT}")
        print(json.dumps(report(args.geo, args.file, args.limit), indent=2, ensure_ascii=True))
    except TrendsRefused as err:
        parser.exit(1, f"archive-trends: {err}\n")
    return True
