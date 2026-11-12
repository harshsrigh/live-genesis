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

# Code points with no visible glyph of their own. Written with escapes only, so this file holds
# no invisible character itself.
#   * Unicode Default_Ignorable_Code_Point (DerivedCoreProperties): soft hyphen, combining grapheme
#     joiner, Arabic letter mark, Hangul fillers, Khmer inherent vowels, Mongolian selectors and
#     vowel separator, zero-width and bidirectional characters, U+2060-206F, variation selectors,
#     BOM, U+FFF0-FFF8, shorthand and musical format controls, and all of U+E0000-E0FFF (tag
#     characters, variation selector supplement).
#   * the remaining format characters (category Cf): Arabic number signs, U+FFF9-FFFB, ...
_DEFAULT_IGNORABLE_CLASS = (
    "\\u00ad\\u034f\\u061c\\u115f\\u1160\\u17b4\\u17b5\\u180b-\\u180f\\u200b-\\u200f\\u202a-\\u202e"
    "\\u2060-\\u206f\\u3164\\ufe00-\\ufe0f\\ufeff\\uffa0\\ufff0-\\ufff8"
    "\\U0001bca0-\\U0001bca3\\U0001d173-\\U0001d17a\\U000e0000-\\U000e0fff"
)
_OTHER_FORMAT_CLASS = "\\u0600-\\u0605\\u06dd\\u070f\\u0890\\u0891\\u08e2\\ufff9-\\ufffb\\U000110bd\\U000110cd\\U00013430-\\U0001343f"
_DEFAULT_IGNORABLE = re.compile("[" + _DEFAULT_IGNORABLE_CLASS + "]")
_INVISIBLE = re.compile("[" + _DEFAULT_IGNORABLE_CLASS + _OTHER_FORMAT_CLASS + "]")


def _fold(text: str) -> str:
    """Undo cheap obfuscation before matching: every default-ignorable code point and format
    character (zero-width joiner and non-joiner included, although the character rule allows
    them) is removed, before and after NFKC folds full-width and other compatibility forms."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", _INVISIBLE.sub("", text)))


# ---------------------------------------------------------------- characters

# Characters that are refused in every agent-writable text because they change how text is
# DISPLAYED or parsed, or carry content a human reader cannot see:
#   * control characters (category Cc): C0 U+0000-U+001F, DEL U+007F, C1 U+0080-U+009F.
#     In multi-line text only, newline (U+000A), tab (U+0009) and a carriage return that is
#     directly followed by a newline are allowed.
#   * lone surrogates (Cs), private-use characters (Co), and unassigned code points and
#     noncharacters (Cn, as known to this Python's Unicode database: a character newer than
#     that database is refused too).
#   * format characters (Cf): bidirectional controls, zero-width space, word joiner, soft hyphen,
#     BOM, U+180E, U+2060-U+206F, interlinear annotation U+FFF9-U+FFFB, Arabic number signs ...
#     EXCEPT zero-width joiner U+200D and non-joiner U+200C, which emoji sequences and several
#     scripts need (the content checks still ignore them when matching, see _fold).
#   * every other default-ignorable code point: combining grapheme joiner U+034F, Hangul fillers
#     U+115F U+1160 U+3164 U+FFA0, Khmer U+17B4 U+17B5, Mongolian U+180B-U+180F, U+FFF0-U+FFF8,
#     and the whole range U+E0000-U+E0FFF (tag characters, variation selectors supplement).
#   * variation selectors U+FE00-U+FE0D always; U+FE0E / U+FE0F (text / emoji presentation) unless
#     directly after a symbol, digit or punctuation mark (_VS_BASE), the characters that have
#     such a presentation. After a letter, a space, a mark or at the start they are refused.
#   * the line and paragraph separators U+2028 and U+2029 (line breaks the line limits do not count).
# NOT refused: letters and marks of any script, emoji, ordinary spaces.
_SEPARATORS = "  "
_JOINERS = "‌‍"
_PRESENTATION = "︎️"
_VS_BASE = ("So", "Sm", "Sk", "Sc", "Po", "Nd", "No")
CHARACTER_RULE = ("contains an invisible, private-use, control or text-direction character (for example NUL, "
                  "an escape code, a zero-width space, a tag character or a right-to-left override)")


def _refused(text: str, index: int, multiline: bool) -> bool:
    ch = text[index]
    if ch < "\x7f":  # printable ASCII and the C0 controls: the common case
        return ch < " " and not (multiline and (ch in "\n\t" or (ch == "\r" and text[index + 1:index + 2] == "\n")))
    if ch in _JOINERS:
        return False
    if ch in _PRESENTATION:
        previous = text[index - 1] if index else ""
        return not previous or (unicodedata.category(previous) not in _VS_BASE and previous != "ℹ")
    if ch in _SEPARATORS or unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
        return True
    return _DEFAULT_IGNORABLE.match(ch) is not None


def has_forbidden_character(text: str, *, multiline: bool = False) -> bool:
    """True when `text` holds a character refused by the rule above."""
    if text.isascii() and text.isprintable():
        return False
    return any(_refused(text, index, multiline) for index in range(len(text)))


def check_characters(text: str, label: str, *, multiline: bool = False) -> None:
    """422 for a forbidden character. Never echoes the text or the character."""
    if has_forbidden_character(text, multiline=multiline):
        allowed = "Plain text, newlines and tabs only" if multiline else "Plain text on one line only"
        raise HTTPException(422, f"{label} {CHARACTER_RULE}. Remove it and resend. {allowed}.")


def non_ascii_categories(text: str) -> Counter:
    """Unicode general category -> count of the non-ASCII characters of `text` (never the text)."""
    return Counter(unicodedata.category(ch) for ch in text if ch > "\x7f")


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
    error = HTTPException(status, f"Content rejected: {reason}. {step}")
    error.guard_category = reason  # the fixed category alone, for operator diagnostics (engine.refusal_category)
    return error


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
