# Posting a share card (TikTok, Instagram)

Posting is done by hand, by the owner. Nothing in this repository posts anywhere, holds a social account, or schedules anything. This page is the routine and the wording rules.

**Today there is nothing to post.** No model has been called for the archive yet; every card on the public site shows scripted demo data. The routine starts after the first real run has been exported, read and approved (`docs/ANSWERS-OVER-TIME.md`, "How answers get published").

## 1. Capture the card

Every question has a card at `archive/card/<ID>.html` (9:16, plain HTML). `scripts/capture-cards.mjs` turns cards into 1080 x 1920 PNG files:

```bash
# from the published site (reviewed data): the normal case
node scripts/capture-cards.mjs --url https://harshsrigh.github.io/live-genesis/ --out ./cards --ids Q11,T001

# from a site folder built on this machine
node scripts/capture-cards.mjs --site ./site --out ./cards                 # every card
node scripts/capture-cards.mjs --site ./site --out ./cards --set trending  # T... only
```

- Options: `--site DIR` or `--url BASE` (one of them), `--out DIR`, `--ids Q11,T001` (also chat-app cards: `Q11-chatbots`, `Q11-grok`; see "Chat-app cards"), `--set core|trending`.
- It opens each card at 540 x 960 CSS pixels with a device scale factor of 2, writes `<out>/<ID>.png`, and checks that the file is 1080 x 1920.
- It reads the site folder or the one address given and blocks every other request. It calls no model and opens no database.
- It needs Node and the `playwright` package with its Chromium, installed by the operator; the project gains no dependency: `npm install -g playwright`, then `npx playwright install chromium`. Without it the script stops with that hint.
- A card whose label says scripted demo is captured and listed as `SCRIPTED DEMO`. That is a warning, not a pass: see the next section.
- Tested here against a scripted demo snapshot, from a folder and from a local server. Not tested against the public site address.

By hand instead: open the card in a browser, responsive mode, 540 x 960, device pixel ratio 2, screenshot.

## 2. Check before posting

1. **Is it real data?** Read the bottom line of the card. `Scripted demo answers and dates · no model was called` means the card is hand-written sample data. **Never post a scripted demo card as a real answer**, and do not post it at all without saying in the caption, in the first line, that it is a made-up sample. Only a card that reads `Answers are model output and may be wrong` shows recorded model output.
2. **Is it from the published site?** Capture from the published, reviewed data, not from a local database that has not been through export and approval.
3. **Is the label intact?** Never crop, cover or blur the bottom label, the model labels or the dates. A sticker or caption overlay must not sit on them.
4. **Read every answer on the card.** If an answer names a private person, gives medical, legal or financial advice, or states something harmful as fact, do not post that card. The content guards are word lists; they do not understand text.
5. **Did anything actually change?** A dark panel with a "Before" line means the wording differs from that model's earlier answer. It does not mean the facts changed, or that either answer is right.

## 3. What the labels mean

| On the card or page | Meaning | How to say it |
|---|---|---|
| The name in capitals on each panel (for example `SMALL-A`) | The label the operator gave that answerer in `LIVE_ARCHIVE_MODELS`. It is not the maker's product name | Give the requested model id from the question's page, not only the label |
| `server call` | This project's server requested that model id and stored the reply | "we asked <model id>" |
| `self-declared` | An outside client sent the answer and named its own model. Nothing verified it | "sent by an outside client, which says it used <model>"; or leave these out of posts |
| `scripted demo` | Hand-written sample text. No model | Do not post as an answer |
| `run locally` (question page) | A model file run on the operator's own machine, usually a small, compressed version | "a small open model run on our own computer". Never "<maker>'s chatbot says" |
| `via <name>` (question page) | The model ran at a third-party hosting service with that endpoint name | "<model id>, hosted by a third party". Not the maker's own app |
| `new model ID` / `new model file` (question page) | The requested id, the name the service reported, or the local file digest differs from the run before | Say that the model changed, when a change in the answer is the point of the post |
| `CHANGED` / the dark panel | The text differs from that model's previous recorded answer | "worded differently from last time", unless you have read both and the content really differs |

An answer from a model's API or from a local file is **not** what that maker's chat app would say: apps add their own instructions, search and memory.

## 4. Caption

Say what it is, when, and what it is not. A pattern (replace the parts in brackets; do not post it with brackets):

```
We ask AI models the same basic questions again and again and keep every answer.
Question: "[question text]"
Asked [month year]. Models: [model ids, with "run locally" or "hosted by a third party" where that applies].
Answers are model output and may be wrong. This is not advice.
Bare models, not the chat apps. Not affiliated with any AI company.
Full record: [link to the question's page]
```

Rules for the wording:

- Always include a sentence with the meaning of **"answers are model output and may be wrong"**.
- Name the model by its id and say where it ran. Never write that a company "says", "claims", "admits" or "thinks" something: a model's output is not a statement by its maker.
- Do not say which answer is right unless you checked it yourself against a source you can name, and then name the source.
- "Changed" means the words changed. Do not write "the AI changed its mind" or "got smarter / dumber".
- No claims about a model being conscious, having feelings or wanting anything.
- One sample per model per run: do not write "always" or "never" about a model.
- No link to anything that takes money. The project takes no payments and has no donation page.

## 5. A poll, using the platform's own sticker

The first step towards human voting is the platform's own poll (TikTok poll sticker, Instagram poll sticker in Stories). Nothing is built here, and the votes stay on the platform.

- Suggested question: **"Which answer would you trust?"** with the model labels as options, or **"Did this answer get better?"** with Better / Worse (for a card with a "Before" line).
- Avoid "Which answer is right?" when the answer is a matter of fact you have not checked: a poll does not settle a fact.
- Place the sticker in the empty band of the card, never over an answer or the bottom label.
- If you mention the result later, call it what it is: an open online poll with N votes. Not a survey, not a sample of anybody.
- Poll results are never copied into the archive, its pages or its data. On-site voting is not built (plan: `docs/ANSWERS-OVER-TIME.md`, "Human voting").

## 6. What not to post

- **Anything about a named private person.** Not in the question, not in an answer, not in the caption.
- **Medical, legal or financial advice**, or a card where an answer reads like advice (doses, symptoms, "you should buy", "you can sue"). The honesty label does not make advice safe to spread.
- **Anything implying a company said it**: no company logos, no "<company> says", no product names used as the speaker.
- A scripted demo card presented as real, a mock or dry-run answer, or anything from an unapproved export.
- A card with the bottom label cropped or covered.
- Accusations, crimes, deaths or health of a named person; election or voting-process claims.
- Operator-only figures (`cli health`, `cli turns`, costs per call), keys, endpoint addresses, or screenshots of a terminal.
- An answer that was withheld by the content check, retyped by hand.
- Edited answers. A card is posted as captured or not at all.

## Chat-app cards

The chat-app track (`docs/ANSWERS-OVER-TIME.md`, "The chat-app track") has its own cards: `archive/card/<ID>-chatbots.html` shows the latest reply of up to four apps, and `archive/card/<ID>-<app label>.html` (for example `Q11-grok`) shows one app at a larger size with the conditions it was asked under.

```bash
node scripts/capture-cards.mjs --url https://harshsrigh.github.io/live-genesis/ --out ./cards --ids Q11-chatbots,Q11-grok
```

- **Say how it was collected.** The caption says "typed in by hand on <date>", with the date on the card. Do not write "<app> says" as if it were a standing fact: it is one reply on one day.
- **Keep a screenshot** of the app's reply for your own records (the archive stores only its sha256). Do not post the screenshot: the app's interface is not ours to publish. Post the card.
- **Never post a scripted demo card.** The demo cards of this track show made-up apps ("Chat app A") and say scripted demo in the bottom line.
- **No logos.** Do not add a company's logo, icon or colours to the image or the video, and do not use a product name as the largest text. The names on the card are plain text and stay that way.
- **Do not imply endorsement.** Nothing in the caption may suggest that the company made, approved or sponsors the post. Keep the bottom line of the card ("AI-generated · typed in by hand · not affiliated · may be wrong") visible in the image, and keep "Written with Grok" on a Grok panel.
- **Use the platform's AI-content label** where its rules ask for one.
- **The safe area.** TikTok and Instagram cover parts of a 1080 x 1920 image with their own buttons and captions. On these cards the question, the app names, the replies and the dates sit inside x 65 to 940 and y 270 to 1440. The wordmark is above that box and the honesty line below it: they may end up under the app's own interface, but they are in the image.
- **Before each post, read the service's current terms** on publishing its output. They were not verified for this page, and they change. Copilot's consumer terms limit use to "your own personal use": leave Copilot cards out of anything that earns money.

## Not decided or not verified

- Which accounts to post from, how often, and whether to post at all: the owner's decisions.
- Each platform's current rules on AI-generated content labels were not read for this page. Read them before the first post and use the platform's own "AI-generated" label if its rules ask for it.
- Each model provider's terms on publishing outputs must be read before a model's answers are posted (see `docs/FREE-MODELS.md`).
