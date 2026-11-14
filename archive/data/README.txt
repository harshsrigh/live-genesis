Answers Over Time: data files
=============================

These files hold the same answers as the pages next to them, for programs to read.
They are built from the same reviewed record the pages are built from (archive.json at the
site root) and hold nothing that is not in it.

Files
-----
answers.jsonl   One answer per line, each line a complete JSON object.
<ID>.json       One question (C001, C002 ...): the question, exactly what was asked,
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
schema              The version of this format: answers-over-time-data/1
id                  A stable id of this answer: question / answerer / check.
question_id         C... (the chat-app list).
question            The question in plain words.
prompt              Exactly what was asked, character for character.
answerer            The answerer's label. answerer_name is the name shown on the pages.
answerer_kind       chat-app | human
maker               The company behind a chat app ("" when not recorded).
version             The version name the app showed ("" when none).
collected           The date of the check, YYYY-MM-DD. period is the label the pages use.
method              typed-by-hand       a person typed the question into a chat app and pasted the reply
                    collected-by-hand   answers written by people, collected by a person
                    scripted-sample     a sample; nothing was asked
provenance          ai-generated | human-written | scripted-sample
human_form          quoted | summarised (human answers only; otherwise null)
state               answered   the first answer from this answerer
                    same       the same as its answer before
                    changed    the text differs from its answer before
                    refused, no-answer, hidden   kept on file, not shown on the pages
shown               true when the pages show this answer as a card.
text                The answer in full (null when there is none). The pages may shorten what
                    they display; this is the whole text.
changed             true / false against the answer before; null for a first answer.
previous_id, previous_collected, previous_text
                    The answer this one is compared with (previous_text only when it changed).
conditions          What was recorded about how it was asked (account, search, country ... for a
                    chat app; source and date for human answers). Never a web address, a key, a file path
                    or a username.
attribution         A line the app's maker asks to be shown with its output ("" when none).
lang                The language of the text.
notice              A short notice that travels with every record.

"changed" is a plain text comparison (letter case, spacing and punctuation at the ends are
ignored). It does not say which answer is right, and a chat app's reply can change because
of how it was asked that day.

Product names belong to their owners. This archive is independent and is not affiliated
with, sponsored by or endorsed by them.
