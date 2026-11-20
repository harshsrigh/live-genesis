"""A "just for fun" answering-style profile per AI answerer. Pure functions: no network, no model, no
randomness, no stored state. The pages work a profile out again at every build from the answers they
show, so it sharpens by itself as the months add up. It is this site's own commentary, display only:
never part of a recorded answer, never in a data file, the export or the published payload.

HOW A PROFILE IS WORKED OUT
Every shown answer is checked for eight yes-or-no signals (an answer counts once per signal, however
often the thing appears in it), and each signal is worth a few points:

  wild end                                     points   grown-up end                              points
  an emoji                                       +2     a plain statement (none of the wild five)   -2
  an exclamation mark                            +2     a careful word or phrase (CARE)             -3
  a slang word or interjection (SLANG)           +3     longer than LONG_WORDS words                -1
  a word in capitals (3+ letters, not an
    everyday abbreviation: PLAIN_CAPS)           +2
  a question thrown back ("?")                   +1

The points of all answers are added up and divided by the number of answers: the average per answer.
The average picks one of five steps (BOUNDS). Whole numbers only (the comparison is total against
bound x count), so the same answers always give the same step, and a value exactly on a bound takes
the wilder step. With fewer than MIN_ANSWERS shown answers there is no profile: "Too early to tell".

The word lists are short, kept here and nowhere else, and ENGLISH ONLY: an answer in another language
is counted by its emoji, punctuation, capitals and length alone. This is a crude count of surface
habits. It says nothing about what a model is, knows or can do, and the names are jokes about the
STYLE of the answers only: never about a mind, feelings, quality, safety or the maker, and never a
ranking. No name may be a word about mental health (see FORBIDDEN, checked by a test).

Answer text is untrusted: it is only matched against the fixed patterns below and counted. Nothing
found in it is run, and the only words of an answer that reach a page are a whole answer picked as
"most in character", which the page escapes like any other text.
"""
from __future__ import annotations

import re

MIN_ANSWERS = 5          # fewer shown answers than this: "Too early to tell"
LONG_WORDS = 20          # the questions ask for one short sentence: more words than this is a long answer
QUOTE_CHARS = 140        # a "most in character" answer is shown whole or not at all: only one this short is picked

# Wild end to grown-up end. Short, kind, obviously a joke, about answering style only.
STEPS = ("Chaos gremlin", "Class clown", "Good-time friend", "The sensible one", "The grown-up")
ENDS = ("Wild", "Grown-up")
# The average points per answer from which a step starts, wild end first; below the last one it is the fifth step.
BOUNDS = (4, 1.5, -1, -3)
TOO_EARLY = "Too early to tell"
KICKER = "Answering style"
NEED = f"We need {MIN_ANSWERS} to tell."
LABEL = ("Just for fun: our own reading of how each one answers, worked out by counting things like emoji and "
         "exclamation marks in the answers on this site. It says nothing about what any of them is or can do.")
LABEL_SAMPLE = " Here the answers are samples we wrote, so these styles are samples too."

# (signal, points). The order is also the order of the "because" facts when two signals are equally common.
WILD = (("emoji", 2), ("shout", 2), ("slang", 3), ("caps", 2), ("ask", 1))
CALM = (("plain", -2), ("care", -3), ("long", -1))
POINTS = dict(WILD + CALM)

# English only. Lower case; matched as whole words, whatever the capitals. "like," needs its comma (the filler).
SLANG = ("omg", "lol", "lmao", "nah", "nope", "yep", "yeah", "kinda", "gonna", "wanna", "gotta", "tho", "obvs", "haha",
         "like,", "totally", "legit", "bruh", "dude", "duh", "meh", "ugh", "whoa", "yikes", "yay")
CARE = ("technically", "it depends", "arguably", "in general", "generally", "strictly speaking", "to be fair", "probably",
        "perhaps", "maybe", "i think", "pretty sure", "usually", "it seems")
# Everyday abbreviations that are written in capitals without any shouting.
PLAIN_CAPS = frozenset({"USA", "NASA", "DIY", "FAQ", "BBQ", "PDF", "GPS", "ATM", "DNA"})

# No profile text may hold one of these (a test builds every text this module can produce). The first four are words
# about illness used as insults; the rest would turn a joke about style into a claim about a mind or a ranking.
FORBIDDEN = ("psycho", "crazy", "insane", "unhinged", "smart", "best", "conscious", "feel")

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF☀-➿⬀-⯿]")
_CAPS = re.compile(r"\b[A-Z]{3,}\b")
_WORD = re.compile(r"[^\W_]+(?:['’\-‑][^\W_]+)*")


def _finder(words: tuple[str, ...]) -> re.Pattern:
    parts = [r"\b" + re.escape(word) + (r"\b" if word[-1].isalnum() else "") for word in words]
    return re.compile("|".join(sorted(parts, key=len, reverse=True)), re.IGNORECASE)


_SLANG, _CARE = _finder(SLANG), _finder(CARE)


def _shown(word: str) -> str:
    """A list word as a page shows it: "omg" and "lol" in capitals, "like," without its comma."""
    word = word.rstrip(",")
    return word.upper() if word in ("omg", "lol", "lmao") else word


def signals(text: str) -> dict:
    """The eight yes-or-no signals of one answer, plus the slang and careful words found in it (from the
    lists, in lower case: never the answer's own spelling)."""
    slang = sorted({m.group(0).lower() for m in _SLANG.finditer(text)}, key=SLANG.index)
    care = sorted({m.group(0).lower() for m in _CARE.finditer(text)}, key=CARE.index)
    found = {"emoji": bool(_EMOJI.search(text)), "shout": "!" in text, "slang": bool(slang),
             "caps": any(word not in PLAIN_CAPS for word in _CAPS.findall(text)), "ask": "?" in text}
    found["plain"] = not any(found.values())
    found.update(care=bool(care), long=len(_WORD.findall(text)) > LONG_WORDS, slang_words=slang, care_words=care)
    return found


def points(found: dict) -> int:
    """One answer's points: above zero leans wild, below leans grown-up."""
    return sum(value for name, value in POINTS.items() if found[name])


def _step(total: int, count: int) -> int:
    """1 (wild end) to 5 (grown-up end). `total` is the sum of points over `count` answers; a bound holds halves, so
    both sides are doubled and the comparison stays in whole numbers."""
    for step, bound in enumerate(BOUNDS, 1):
        if 2 * total >= int(2 * bound) * count:
            return step
    return len(STEPS)


def _top(words: list[list[str]], order: tuple[str, ...]) -> str:
    """The list word found in the most answers; the earlier one in the list when two are equal."""
    flat = [word for found in words for word in found]
    return max(order, key=lambda word: (flat.count(word), -order.index(word)))


def _fact(name: str, hits: int, count: int, word: str) -> str:
    return {"emoji": f"an emoji in {hits} of {count} answers",
            "shout": f"an exclamation mark in {hits} of {count} answers",
            "slang": f"slang like “{word}” in {hits} of {count} answers",
            "caps": f"a word in capitals in {hits} of {count} answers",
            "ask": f"a question thrown back in {hits} of {count} answers",
            "plain": f"{hits} of {count} answers are plain statements, with no emoji, exclamation mark or slang",
            "care": f"careful words like “{word}” in {hits} of {count} answers",
            "long": f"{hits} of {count} answers run past {LONG_WORDS} words"}[name]


def position(step: int) -> str:
    """The meter's place in words, so that neither colour nor position is the only sign."""
    where = {1: ", the wild end", len(STEPS): ", the grown-up end"}.get(step, "")
    return f"{step} of {len(STEPS)} from wild to grown-up{where}"


def profile(texts: list[str]) -> dict:
    """The profile of one answerer from the texts of its shown answers, oldest first. Returns `count`, `counts`
    (answers per signal), `total` (points), `step` (1-5, or None when it is too early), `name`, `because` (one or
    two facts in plain words) and `pick` (the index of the answer most in character, or None)."""
    found = [signals(text) for text in texts]
    count = len(found)
    counts = {name: sum(1 for one in found if one[name]) for name in POINTS}
    total = sum(points(one) for one in found)
    out = {"count": count, "counts": counts, "total": total, "step": None, "name": TOO_EARLY, "because": [], "pick": None}
    if count < MIN_ANSWERS:
        return out
    step = _step(total, count)
    word = {"slang": _shown(_top([one["slang_words"] for one in found], SLANG)),
            "care": _top([one["care_words"] for one in found], CARE)}

    def ranked(side: tuple) -> list[str]:
        """The signals of one side that were found at all, the most common first; the listed order breaks a tie."""
        names = [name for name, _ in side if counts[name]]
        return sorted(names, key=lambda name: -counts[name])

    wild, calm = ranked(WILD), ranked(CALM)
    middle = (len(STEPS) + 1) // 2
    chosen = (wild[:2] or calm[:1]) if step < middle else (calm[:2] or wild[:1]) if step > middle else wild[:1] + calm[:1]
    # The answer most in character: the most points at the wild end, the fewest at the grown-up end, the one nearest
    # the answerer's own average in the middle. Whole and short enough to show uncut, or none; the oldest wins a tie.
    scores = [points(one) for one in found]
    def lean(n: int) -> int:
        return -scores[n] if step < middle else scores[n] if step > middle else abs(scores[n] * count - total)

    # A whole answer only, so one that fits a line or two; and never one that would put a forbidden word under a name.
    fits = [n for n, text in enumerate(texts) if 0 < len(text) <= QUOTE_CHARS and "\n" not in text.strip()
            and not any(bad in text.lower() for bad in FORBIDDEN)]
    out.update(step=step, name=STEPS[step - 1], because=[_fact(name, counts[name], count, word.get(name, "")) for name in chosen],
               pick=min(fits, key=lambda n: (lean(n), n)) if fits else None)
    return out


def every_text() -> list[str]:
    """Every piece of profile wording this module can produce, for the test that no forbidden word is among them."""
    facts = [_fact(name, 1, 2, _shown(word)) for name in POINTS for word in (SLANG + CARE)]
    return [*STEPS, *ENDS, TOO_EARLY, KICKER, NEED, LABEL, LABEL_SAMPLE, *facts, *[position(step) for step in range(1, len(STEPS) + 1)],
            *[_shown(word) for word in SLANG], *CARE]
