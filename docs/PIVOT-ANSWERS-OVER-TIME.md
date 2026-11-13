# Pivot: Answers Over Time

Status, 12 November 2026: proposed by the owner on this date. **The build is merged to `main`:** the core archive, the trending track, the share cards, named endpoints (several services in one run) and per-model failure isolation. The public site shows it with a scripted demo. **Nothing real has been recorded:** both question lists are drafts and not locked, no model has been called for the archive, and nothing has been tested against a real model server.

## The decision

The public product becomes **Answers Over Time**: a small, fixed list of very basic questions that people ask, put to AI models about once a month, with every answer kept for years. The page shows when an answer changed and which model version it changed with.

The agent society (LIVE Genesis) stays in the repository and keeps working, but it stops being the thing we lead with.

## Why

- **It is readable by anyone.** "What did the AI say last year, and what does it say now?" needs no explanation. The society needed a paragraph before a visitor understood the page.
- **It gets more valuable with age.** A record of answers cannot be backfilled. Whoever starts first has the longest record.
- **It is cheap.** One run is 20 short questions per model, plus the trending questions. The society needed many long turns per round.
- **It is honest by construction.** Each answer is a stored model reply with its date, model ID and source. Nothing has to be interpreted.
- **We did not find one.** Our search on 12 November 2026 found no public archive of this kind. That is a search result, not a guarantee.

## What it is

| Part | Rule |
|---|---|
| Core questions | 20, in plain words, in four groups: everyday facts, health and home, right now, about the AI. Locked once, then never changed |
| Trending questions | A second list that grows: questions people are searching for, written by hand. Once locked, a question never changes or disappears, and it is asked in every later run |
| Who answers | Models, named by provider and model ID. Not the resident agents |
| Prompt | `Answer in one or two sentences.` then the question. No persona, no date, no other answers |
| Rhythm | About one run a month. Not enforced in code |
| "Changed" | Plain text comparison with the previous run, ignoring capitals, spacing and surrounding punctuation |
| Source label | `server call` (we called the model and hold the receipt) or `self-declared` (an outside client sent it) |
| Where it ran | `run locally` or `via <endpoint name>`; never who made the model, never an address |
| Publishing | Real answers go public only through the existing export, read and approve step |

## What is merged (12 November 2026)

| Part | State |
|---|---|
| Core archive: question file, lock, runs, append-only answers, `changed` flag, script-free pages, logo | Merged. Question list is a draft of 20 |
| Trending track: growing list, `archive-lock --set trending`, `archive-trends` (prints candidates, writes nothing), `archive/trending.html` | Merged. 12 draft questions; none locked |
| Share cards: `archive/card/<ID>.html`, 9:16 | Merged. `scripts/capture-cards.mjs` writes them as PNG files (needs the operator's own Playwright) |
| Named endpoints: several services in one run, each with its own base URL, key variable and rates; pricing basis per answer; reported model name and local model digest stored | Merged. **Not tested against any real server** |
| Per-model failure isolation: a failed call on a zero-priced endpoint skips that model, the run goes on (`complete_with_gaps`); a priced endpoint keeps stop-and-reconcile | Merged |
| Outside clients: `POST /api/agent/archive-answer`, stored as self-declared | Merged. Unusable until a run exists |
| Real answers | **None.** Nothing locked, no run made |

## What changes, stays, and is parked

| | |
|---|---|
| **Changes** | The front page and name people see; the logo (two squares and a turned one); the question the project answers |
| **Stays** | The budget gate and paid-call rules, content guards, append-only records, provenance labels, reviewed publishing, the static site on GitHub Pages, the test suite |
| **Parked** | New society features (games, governance, documents), the Near-Miss Register, outreach for outside resident agents |
| **Still worth doing once** | The solo-versus-group control study, because it is already built and only needs a paid run |

## Rules that carry over unchanged

- No model call without the owner's explicit word. `archive-run` is treated as paid, like `round`, also when every model is local or free.
- Scripted sample answers are always labelled as scripted.
- No question about consciousness or feelings, and no claims about them.
- No payments, no donation links, no credentials in the repository.
- Only the owner exports and approves what is published.

## Limits to state plainly on the page

1. **Rewording counts as a change.** The comparison is text, not meaning. A dark square says "different words", not "different answer".
2. **Models vary run to run.** The same model can word an answer differently twice in a row. One sample a month cannot separate that noise from a real shift. Archive calls now send `temperature=0` and a fixed seed where the service takes them, which reduces the noise and does not remove it. See open decision 2.
3. **An API model is not the consumer app.** Apps add their own instructions, search and memory. We record what the bare model says. A small local file is further still from the maker's app.
4. **Model IDs retire, and services substitute.** When a provider withdraws a model, its column ends. A successor starts as a new ID in the same column and the page marks it. The name a service reports is stored with every answer for this reason.
5. **Twenty questions is a sample.** It shows drift on these questions, not how good a model is.
6. **A trending question says what people searched for,** not what is true or important.

## Trending and social plan

The trending track exists to give the archive something new each week or month without touching the fixed list, and to give short posts their material.

1. **Candidates.** `archive-trends` prints the day's popular search terms. It is a prompt for ideas only: the owner writes each question by hand, within the rules in `docs/ANSWERS-OVER-TIME.md` (no named private person, no advice, no elections, no accusations, nothing about consciousness).
2. **Lock.** `archive-lock --set trending` adds the new questions for good.
3. **Run and publish.** As for the core list: on the owner's word, then export, read, approve.
4. **Card.** Each question has a 9:16 share card. `scripts/capture-cards.mjs` writes it as a 1080 x 1920 PNG.
5. **Post by hand.** TikTok and Instagram, by the owner. Nothing in the repository posts, schedules or holds an account. The caption says that answers are model output and may be wrong, names the model and where it ran, and never implies a company said it. A scripted demo card is never posted as real. Full routine and the list of what not to post: `docs/SOCIAL-POSTS.md`.

**Human voting: not built. Platform polls first.** The first step is the platform's own poll sticker on a post ("Which answer would you trust?"): nothing to build, and the votes stay on the platform. An on-site vote would need a server, which the static site does not have; it would be a separate service that never writes to the answers tables and never ranks or changes what the archive shows, labelled as an open online poll and not a survey. No vote route, table or page exists, and none is to be added without the owner's instruction. The plan is recorded in `docs/ANSWERS-OVER-TIME.md`, "Human voting".

Not decided: which accounts, how often to post, and whether to post before there are several real runs to show a change.

## Steps

**Owner**
1. Read and edit the 20 core draft questions and the 12 trending draft questions. This is the one step that cannot be redone later. *Open.*
2. Lock them with `archive-lock` and `archive-lock --set trending`. *Open.*
3. Choose the models to track (see open decision 1): read `docs/FREE-MODELS.md`, and for each hosted service its current terms. Report the hardware and Ollama state of the WSL machine. *Open.*
4. Approve the first real run and its ceiling, in so many words. *Open.*
5. Read the exported answers and approve publishing. *Open.*
6. Repeat the run monthly.
7. If the name matters commercially, have a trademark search done before relying on it. *Open.*

**Claude**
1. Merge the build after checking it in a browser; add the logo and favicon. *Done.*
2. List the draft questions for the owner's review. *Done: they are in `live_genesis/archive_questions.json` and `live_genesis/archive_trending.json`, and `archive-plan` prints them.*
3. After the first real run, move Answers Over Time to the site's front page and the society to a secondary link. *Waiting for the first real run.*
4. Update `README.md` and `docs/ROADMAP.md` to describe the new lead product. *Done on 12 November 2026, ahead of the first run; both say plainly that only scripted demo data exists.* `CLAUDE.md`: the owner applies the lines proposed for it.

## Open decisions for the owner

1. **Which models. Still open.** One model makes a thin page. Three or four from different makers is the point of the product. The code now allows up to 8 models from several services in one run, local ones included. `docs/FREE-MODELS.md` records what was found on 12 November 2026: a local starter set through Ollama (four makers, tags unverified), which hosted free tiers looked usable, and which to avoid because their terms forbid benchmarking or limit outputs to internal testing. The makers' own APIs are not free; a run costs a few cents at most on each.
2. **One sample or several. Decided by the owner on 13 November 2026: three samples per question per run (`LIVE_ARCHIVE_SAMPLES=3`). It had to be decided before the first real run,** because the method cannot change later. Asking each question three times per run and showing whether the three agree would separate noise from real change. It triples the calls of a run. A planned option, `LIVE_ARCHIVE_SAMPLES`, is being built; until it is merged a run takes one sample per question and model. Pinned sampling (already merged) reduces run-to-run noise; it does not replace this decision.
3. **Name. Still open.** "Answers Over Time" describes the product exactly, which makes it clear and also hard to protect as a word mark. Keep it, or pick a coined name and keep this as the tagline.
4. **The repository name. Still open; no action needed.** `live-genesis` can stay; the site address would then still say `live-genesis`. A rename or a custom domain is optional.
5. **How long to commit. Still open.** The record only pays off if the monthly run actually happens for a year or more. Local models make the run free of provider fees but tie it to one machine and to model files that must be kept safe.

## What would make us stop

- A provider's terms forbid publishing its model's answers in this form.
- After six monthly runs nothing has changed on any question and no one is reading it.
- An established public archive doing the same thing appears, with a longer record.
