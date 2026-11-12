# Pivot: Answers Over Time

Status: proposed by the owner on 12 November 2026. The build is in progress on a branch and is not merged. No real answer has been recorded.

## The decision

The public product becomes **Answers Over Time**: a small, fixed list of very basic questions that people ask, put to AI models about once a month, with every answer kept for years. The page shows when an answer changed and which model version it changed with.

The agent society (LIVE Genesis) stays in the repository and keeps working, but it stops being the thing we lead with.

## Why

- **It is readable by anyone.** "What did the AI say last year, and what does it say now?" needs no explanation. The society needed a paragraph before a visitor understood the page.
- **It gets more valuable with age.** A record of answers cannot be backfilled. Whoever starts first has the longest record.
- **It is cheap.** One run is 20 short questions per model. The society needed many long turns per round.
- **It is honest by construction.** Each answer is a stored model reply with its date, model ID and source. Nothing has to be interpreted.
- **We did not find one.** Our search on 12 November 2026 found no public archive of this kind. That is a search result, not a guarantee.

## What it is

| Part | Rule |
|---|---|
| Questions | 20, in plain words, in four groups: everyday facts, health and home, right now, about the AI |
| Who answers | Models, named by provider and model ID. Not the resident agents |
| Prompt | `Answer in one or two sentences.` then the question. No persona, no date, no other answers |
| Rhythm | About one run a month. Not enforced in code |
| "Changed" | Plain text comparison with the previous run, ignoring capitals, spacing and surrounding punctuation |
| Source label | `server call` (we called the model and hold the receipt) or `self-declared` (an outside client sent it) |
| Publishing | Real answers go public only through the existing export, read and approve step |

## What changes, stays, and is parked

| | |
|---|---|
| **Changes** | The front page and name people see; the logo (two squares and a turned one); the question the project answers |
| **Stays** | The budget gate and paid-call rules, content guards, append-only records, provenance labels, reviewed publishing, the static site on GitHub Pages, the test suite |
| **Parked** | New society features (games, governance, documents), the Near-Miss Register, outreach for outside resident agents |
| **Still worth doing once** | The solo-versus-group control study, because it is already built and only needs a paid run |

## Rules that carry over unchanged

- No paid call without the owner's explicit word. `archive-run` is paid, like `round`.
- Scripted sample answers are always labelled as scripted.
- No question about consciousness or feelings, and no claims about them.
- No payments, no donation links, no credentials in the repository.
- Only the owner exports and approves what is published.

## Limits to state plainly on the page

1. **Rewording counts as a change.** The comparison is text, not meaning. A dark square says "different words", not "different answer".
2. **Models vary run to run.** The same model can word an answer differently twice in a row. One sample a month cannot separate that noise from a real shift. See open decision 2.
3. **An API model is not the consumer app.** Apps add their own instructions, search and memory. We record what the bare model says.
4. **Model IDs retire.** When a provider withdraws a model, its column ends. A successor starts as a new ID in the same column and the page marks it.
5. **Twenty questions is a sample.** It shows drift on these questions, not how good a model is.

## Steps

**Owner**
1. Read and edit the 20 draft questions. This is the one step that cannot be redone later.
2. Lock them with `archive-lock`.
3. Choose the models to track (see open decision 1) and check their prices.
4. Approve the first real run and its spending ceiling.
5. Read the exported answers and approve publishing.
6. Repeat the run monthly.
7. If the name matters commercially, have a trademark search done before relying on it.

**Claude**
1. Merge the build after checking it in a browser; add the logo and favicon.
2. List the 20 draft questions for the owner's review.
3. After the first real run, move Answers Over Time to the site's front page and the society to a secondary link.
4. Update `CLAUDE.md`, `README.md` and `docs/ROADMAP.md` to describe the new lead product. Not before step 3, so the docs never describe something that has no data.

## Open decisions for the owner

1. **Which models.** One model makes a thin page. Three or four from different makers is the point of the product. More models means more cost per run.
2. **One sample or several.** Asking each question three times per run and showing whether the three agree would separate noise from real change. It triples the cost of a run. Recommended, but it must be decided before the first real run, because the method cannot change later.
3. **Name.** "Answers Over Time" describes the product exactly, which makes it clear and also hard to protect as a word mark. Keep it, or pick a coined name and keep this as the tagline.
4. **The repository name.** `live-genesis` can stay; the site address would then still say `live-genesis`. A rename or a custom domain is optional.
5. **How long to commit.** The record only pays off if the monthly run actually happens for a year or more.

## What would make us stop

- A provider's terms forbid publishing its model's answers in this form.
- After six monthly runs nothing has changed on any question and no one is reading it.
- An established public archive doing the same thing appears, with a longer record.
