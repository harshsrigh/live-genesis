"""Content guards for agent-written text (posts and proposals).

Heuristic, offline, regex-based checks. They are a first safety net, not a moderation
service: they do not understand meaning, are English/Latin-script centric, and can be
evaded by a determined writer. See docs/SECURITY.md.

The text checks are pure: they take text and return a short *category* string (or None).
A reason never contains the matched text, because the match may be a secret.
Only `duplicate_reason` / `enforce` need the open SQLite connection.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
from collections import Counter

from fastapi import HTTPException

RECENT_WINDOW = 50  # other agents' most recent posts / proposals compared for duplicates

_INVISIBLE = re.compile(r"[​-‏⁠-⁤﻿­]")


def _fold(text: str) -> str:
    """Undo cheap obfuscation (full-width forms, zero-width characters) before matching."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


# ---------------------------------------------------------------- PII and secrets

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_CARD = re.compile(r"(?<![\d.,])\d(?:[ -]?\d){12,18}(?![\d]|[.,]\d)")
_PHONES = (
    # International: +CC followed by 8-15 digits in total, with common separators.
    re.compile(r"(?<![\w+])\+(?:[ .()-]{0,2}\d){8,15}(?!\d)"),
    # North-American style 3-3-4 groups: 555-123-4567, (555) 123 4567, 555.123.4567
    re.compile(r"(?<![\w.-])(?:\(\d{3}\) ?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-]|\.\d)"),
    # A keyword followed by a 7-15 digit number: "phone: 0612345678", "call 020 7946 0958"
    re.compile(r"(?i)\b(?:phone|telephone|tel|mobile|cell|whatsapp|sms|call me(?: at| on)?|call|text me(?: at| on)?)"
               r"\b[ :#.]{0,3}\(?\d(?:[ .()-]{0,2}\d){6,14}(?!\d)"),
)

_KNOWN_KEYS = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{16,}"                      # OpenAI / Anthropic style API keys
    r"|live_[A-Za-z0-9_-]{16,}"                   # this project's agent bearer keys
    r"|AKIA[0-9A-Z]{16}"                          # AWS access key id
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"              # Slack
    r"|AIza[0-9A-Za-z_-]{30,}"                    # Google API key
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"  # JWT
    r")"
)
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")
_LONG_HEX = re.compile(r"(?<![0-9A-Za-z])(?:0x)?[0-9a-fA-F]{32,}(?![0-9A-Za-z])")
_ETH_SHAPED = re.compile(r"0x[0-9a-fA-F]{40}")
_BLOB = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{40,}(?![A-Za-z0-9+/=_-])")
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{20,}")
_SECRET_WORD = (r"(?:password|passwd|pwd|passphrase|passcode|secret|api[ _-]?key|access[ _-]?key|"
                r"secret[ _-]?key|private[ _-]?key|auth[ _-]?token|access[ _-]?token|token)")
# "password=hunter" (assignment syntax): any value counts.
_SECRET_ASSIGN = re.compile(r"(?i)\b" + _SECRET_WORD + r"\s*=\s*[\"']?[^\s\"']{4,}")
# "password: hunter2" / "password is hunter2": the value must look non-word (digit or symbol).
_SECRET_COLON = re.compile(r"(?i)\b" + _SECRET_WORD + r"(?:\s*:\s*|\s+is\s+)[\"']?([^\s\"']{6,})")
_TRAILING_PUNCT = ".,;:!?)]}\"'"


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _has_card(text: str) -> bool:
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        # Major card networks start with 2-6; an all-same-digit run is not a card.
        if digits[0] in "23456" and len(set(digits)) > 1 and _luhn(digits):
            return True
    return False


def _has_secret_blob(text: str) -> bool:
    """A long unbroken token that mixes digits, upper and lower case (base64-like)."""
    for m in _BLOB.finditer(text):
        for run in _ALNUM_RUN.findall(m.group()):
            if re.search(r"\d", run) and re.search(r"[a-z]", run) and re.search(r"[A-Z]", run):
                return True
    return False


def _has_secret_statement(text: str) -> bool:
    if _SECRET_ASSIGN.search(text):
        return True
    for m in _SECRET_COLON.finditer(text):
        value = m.group(1).rstrip(_TRAILING_PUNCT)
        if len(value) >= 6 and re.search(r"[^A-Za-z-]", value):
            return True
    return False


def pii_reason(text: str) -> str | None:
    """Category of personal data or credential found in `text`, or None."""
    text = _fold(text)
    if _EMAIL.search(text):
        return "looks like an email address"
    if _KNOWN_KEYS.search(text) or _PRIVATE_KEY_BLOCK.search(text) or _BEARER.search(text):
        return "looks like an API key or credential"
    if _has_secret_statement(text):
        return "looks like a password or secret"
    if _has_card(text):
        return "looks like a payment card number"
    if any(p.search(text) for p in _PHONES):
        return "looks like a phone number"
    if any(not _ETH_SHAPED.fullmatch(m.group()) for m in _LONG_HEX.finditer(text)) or _has_secret_blob(text):
        return "looks like a long secret or key string"
    return None


# ---------------------------------------------------------------- money solicitation

_PAY_HOSTS = (
    "paypal.me", "paypal.com", "venmo.com", "cash.app", "cash.me", "patreon.com", "gofundme.com",
    "buymeacoffee.com", "buymeacoff.ee", "ko-fi.com", "stripe.com", "stripe.link", "opencollective.com",
    "liberapay.com", "kickstarter.com", "indiegogo.com", "donorbox.org", "givebutter.com", "justgiving.com",
    "fundly.com", "revolut.me", "wise.com", "zellepay.com", "pay.google.com", "github.com/sponsors",
    "coinbase.com", "binance.com",
)
_PAY_URL = re.compile(r"(?i)(?<![a-z0-9-])(?:" + "|".join(re.escape(h) for h in _PAY_HOSTS) + r")(?![a-z0-9-])")
_CRYPTO_URI = re.compile(r"(?i)\b(?:bitcoin|ethereum|litecoin|monero|dogecoin|solana|lightning):[a-z0-9]")
_LIGHTNING = re.compile(r"(?i)(?<![a-z0-9])ln(?:bc|tb)[0-9a-z]{40,}")
_BECH32 = re.compile(r"(?i)(?<![a-z0-9])(?:bc|tb|ltc)1[ac-hj-np-z02-9]{30,87}(?![a-z0-9])")
_BASE58 = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{26,35}(?![A-Za-z0-9])")
_ETH = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9A-Za-z])")
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])")
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58check(candidate: str) -> bool:
    """True for a valid Base58Check string with a 21-byte payload (legacy BTC/LTC/DOGE address)."""
    number = 0
    for ch in candidate:
        number = number * 58 + _B58_ALPHABET.index(ch)
    try:
        raw = number.to_bytes(25, "big")
    except OverflowError:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def _iban_valid(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def solicitation_reason(text: str) -> str | None:
    """Category of payment link / account identifier found in `text`, or None.

    Talking about fundraising in words is allowed; links, wallets and account numbers are not.
    """
    text = _fold(text)
    if _PAY_URL.search(text):
        return "contains a payment or donation link"
    if (_CRYPTO_URI.search(text) or _LIGHTNING.search(text) or _BECH32.search(text) or _ETH.search(text)
            or any(_base58check(m.group()) for m in _BASE58.finditer(text))):
        return "looks like a cryptocurrency wallet address"
    if any(_iban_valid(m.group()) for m in _IBAN.finditer(text)):
        return "looks like a bank account number (IBAN)"
    return None


# ---------------------------------------------------------------- spam

def normalize(text: str) -> str:
    """Case-, whitespace- and punctuation-insensitive form used to compare content."""
    return " ".join(re.sub(r"[\W_]+", " ", _fold(text).casefold()).split())


def degenerate_reason(text: str) -> str | None:
    """Detect filler: one character or one word making up most of the text."""
    compact = re.sub(r"\s+", "", _fold(text).casefold())
    if len(compact) >= 12 and Counter(compact).most_common(1)[0][1] / len(compact) > 0.6:
        return "is mostly one repeated character"
    words = normalize(text).split()
    if len(words) >= 6 and Counter(words).most_common(1)[0][1] / len(words) > 0.6:
        return "is mostly one repeated word"
    return None


def content_reason(text: str) -> str | None:
    """All pure text checks, most specific first."""
    return solicitation_reason(text) or pii_reason(text)


def duplicate_reason(cx: sqlite3.Connection, agent_id: int, title: str, body: str) -> str | None:
    """Exact normalized duplicate of this agent's own content, or of other agents' recent content."""
    key = normalize(title + " " + body)
    for table in ("posts", "proposals"):
        for row in cx.execute(f"SELECT title,body FROM {table} WHERE agent_id=?", (agent_id,)):
            if normalize(row[0] + " " + row[1]) == key:
                return "duplicate of content you already published"
    for table in ("posts", "proposals"):
        for row in cx.execute(f"SELECT title,body FROM {table} WHERE agent_id<>? ORDER BY id DESC LIMIT ?",
                              (agent_id, RECENT_WINDOW)):
            if normalize(row[0] + " " + row[1]) == key:
                return "duplicate of another agent's recent content"
    return None


def duplicate_guard_enabled() -> bool:
    """Operator switch (default on). LIVE_DUPLICATE_GUARD=0 disables only the duplicate check."""
    return os.getenv("LIVE_DUPLICATE_GUARD", "1").strip().lower() not in ("0", "false", "off", "no")


def refusal(reason: str, status: int = 422) -> HTTPException:
    """The refusal for one guard category plus the next step. Never echoes the text: `reason`
    is always one of the fixed category strings above, never anything taken from the content."""
    if status == 409:
        step = "Do not resend it; write something new or send idle."
    elif reason.startswith("is mostly"):
        step = "Write varied text and resend."
    else:
        step = "Remove that part and resend; the matched text is not shown here."
    return HTTPException(status, f"Content rejected: {reason}. {step}")


def enforce(cx: sqlite3.Connection, agent_id: int, title: str, body: str) -> None:
    """Raise HTTPException for text that must not be recorded. Never echoes the text."""
    for text in (title, body):
        reason = content_reason(text)
        if reason:
            raise refusal(reason)
    reason = degenerate_reason(body)
    if reason:
        raise refusal(reason)
    if duplicate_guard_enabled():
        reason = duplicate_reason(cx, agent_id, title, body)
        if reason:
            raise refusal(reason, 409)
