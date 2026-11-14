# Plan: the chat-app comparison (second pivot)

Written 14 November 2026. Owner's decision: lead with the consumer chat apps, not API models. This plan uses only what exists today; anything not yet built or not yet verified is marked.

## Owner's decisions after this plan was written (14 November 2026)

These override anything below that disagrees. Written down by the PC session from the owner's words.

1. **Three apps, not four: ChatGPT, Gemini, Grok. Copilot is dropped.** The owner wants a joke line about it, along the lines of "it is cheaper to ask a human than Copilot".
2. **A fourth column: Human.** Answers written by people, collected by asking the same questions on Reddit. It is always labelled as human-written and is never called a model.
3. **No politics.** Do not add political questions. If one is ever shown, the page carries a line saying the site does not promote hate.
4. **The chat-app question list is its own list.** It is not linked to the locked core list or the locked trending list.
5. **The front page is the trending page, showing those questions with the chat apps' answers.**
6. **Show only answers where the app actually answered.** A refusal or a non-answer is left out. The owner's words: this is a comparison for laughs, not a data-collection service.
7. **Tone: short, punchy, made for people who scroll fast.** Headlines may be cheeky.
8. **Ask logged in.**

### Built state (14 November 2026, after the redesign merge)

The decisions above are built and merged: three apps (ChatGPT, Gemini, Grok) and a Human column; a chat-app question list of its own (ten draft questions `C001` to `C010` in `live_genesis/archive_chatbots.json`, editable until the first import, fixed after it); refusals and non-answers stored and left off the pages; the chat-app comparison as the front page; the redesigned reading pages; share cards inside the safe area; machine-readable labels and data files. The political trending question was removed and the published local-model answers were withdrawn by the owner, so the public site shows scripted samples only, under made-up app names. Where the older sections below say four apps, Copilot, or "being built", this section is the current state.

Still the owner's to decide: the Reddit quoting rule (before the first human answer is imported); whether a refused reply's words may stay in the published data files (they are on no page); the wording and place of the Copilot joke (one line on the "How it works" page, marked as a joke); whether hooks stay frozen with their question; the name.

### State when these decisions were made

The table under "What we have" was written before the PC session's work of 13 to 14 November. Since then: both question lists are locked in the real database; two real runs of four local models are recorded (run 1: phi4-mini only; run 2: all four, 384 answers, $0); the reviewed archive-only export was used and approved by the owner; and the public site shows those recorded answers, opening on the trending page, with the scripted demo under `demo/`. The line "No real answer of any kind has been recorded or published" is no longer true.

### Points these decisions leave open (PC session's notes, not decisions)

- **The locked trending list already holds a political question** (T004, "Who is the president of the United States?"), and a wrong model answer to it is public. A locked question cannot be removed from the list. Whether the pages may leave it out, or carry the no-hate line, is for the owner to say.
- **Reddit answers are other people's writing.** Publishing them needs a rule before the first one is imported: say in the Reddit post that replies may be quoted, publish no usernames, run the same content guards as for model answers, and read each subreddit's rules on surveys and self-promotion. Posting on Reddit is outreach, so the owner posts; the software never does.
- **The Copilot joke names a real product.** Keep it clearly a joke and make no factual claim about price or quality that could be read as one.
- **"Only where the app answered" is a display rule.** The page should not suggest it shows everything; one plain line such as "refusals are left out" keeps it honest. Whether left-out replies are still stored is not decided.
- **Cheeky headlines must still say what the app actually said.** The honesty labels (recorded, may be wrong, not affiliated, written with Grok) stay.

## The product in one paragraph

Once a month the owner types the same ten questions, by hand, into **ChatGPT, Gemini, Grok and Copilot**, in a fresh chat each time, and pastes each reply unedited into a file. The site shows the four replies side by side, keeps every month, and marks when an app's answer changed. Each question also becomes portrait cards for TikTok and Instagram. The recurring hook is **"the answer changed since last month"**.

## What we have

| Piece | State |
|---|---|
| Site, logo, pages with no script, share cards | Live at the public address, showing scripted demo data only |
| Question lists | Final and locked in the files: 20 core, 12 trending |
| Model track (local and hosted models, three samples) | Built; no real run yet; needs model setup on the PC |
| Reviewed publishing (`--export --archive-only`, approve, `--check`) | Built; not yet used |
| Card capture script, social post guide | Built; tested on demo data only |
| Chat-app track (fill-in sheet, import, Chatbots page, chat-app cards) | **Being built now; not merged** |
| Terms research for the four apps | Done 14 Nov. Google and Microsoft pages read directly; OpenAI and xAI wording is from search excerpts and unverified |
| Prior-art research | Done 14 Nov. No one found doing a monthly hand-typed consumer-app archive |
| Reading UI | **Under review now; redesign to follow** |

No real answer of any kind has been recorded or published.

## What the research fixed in place

- **By hand only.** All four services forbid bots and scraping. The software never contacts them.
- **Plain text, own design.** No logos, no interface screenshots on the site or cards; product names in plain text and never the largest element. A "not affiliated" line everywhere real answers appear.
- **Say it is AI-generated.** Grok answers carry "Written with Grok".
- **Copilot is personal-use only** in its consumer terms. Fine while nothing earns money; drop it or ask Microsoft before monetising.
- **Fixed conditions per app, recorded every time:** ChatGPT Temporary Chat (Unpersonalized); Gemini Temporary chat; Grok Private Chat with memory off; Copilot with personalisation and memory off, Smart mode, new chat. Note whether a sources marker appeared.
- **Stay out of politics.** That ground is taken and it is the riskiest.
- **Video first on TikTok;** the same cards as an Instagram carousel. About two posts a week from one month of answers.

## Phases

### Phase 1: finish the build (Claude)
1. Merge the chat-app track after a browser check of its pages and cards.
2. Redesign the reading pages from the two UI reviews: the newest answers from all four apps visible with as little scrolling as possible on a phone, the previous answer shown inline where one changed, older months one tap away. No script.
3. Bring the model share cards inside the social apps' safe area, as the chat-app cards already are.
4. Update README, the pivot document and the handover notes to lead with the chat-app comparison.

Done when: the full test suite passes, the pages are clean in a browser at phone and laptop width, and the public demo shows the new layout under made-up app names.

### Phase 2: the owner's preparation
1. Read the ten chosen questions; swap any before the first import (after it, the list can only grow).
2. Read the current terms pages of the four apps, OpenAI's and xAI's in particular. OpenAI's usage policies have a new version from 20 November.
3. Make or pick one free account per app and set each up as in the recipe above.
4. Decide the name. "Answers Over Time" has no clash but is a generic phrase. Alternatives with no clash found: Asked Again, The Bots Said. Easier to change before the first post than after.
5. Create the TikTok and Instagram accounts under the chosen name.

### Phase 3: first collection (owner, about half an hour)
1. `archive-chatbot-template` writes the sheet.
2. Ask: one new chat per question, paste the prompt exactly, copy the whole reply, do not edit. Keep a private screenshot of each.
3. `archive-chatbot-import --dry-run`, then the import.
4. Export, read every answer, approve, push. Check the live pages.

Nothing here calls a model or costs money. The exact command names are confirmed when the build is merged.

### Phase 4: first posts
1. Capture the cards with the capture script.
2. Post about twice a week. Lead with the cheeky questions and with "one app said something different". Month one has no "changed" story yet, so use the method post ("I type the same ten questions by hand every month") and a "bet which app changes its answer" tease.
3. Votes: Instagram Story poll (four options) or "comment 1 to 4" on TikTok, whose poll has only two options. Results are noted by hand; nothing is built for voting.

### Phase 5: month two onward
1. Collect again under the same conditions, on about the same day.
2. Lead every cycle with what changed.
3. Add questions only by appending. Add or drop an app only as a new column; old columns stay.
4. The model track runs alongside when the PC has models set up; it is no longer on the critical path.

## Decisions still open

| Decision | Who | By when |
|---|---|---|
| The ten questions (a list of their own; no politics) | Owner | Before the first import |
| Name | Owner | Before the first post |
| ~~Keep Copilot~~ | Decided: dropped | 14 Nov |
| ~~Logged in or out~~ | Decided: logged in | 14 Nov |
| How Reddit answers are collected and quoted | Owner | Before the first human answer is imported |
| ~~What to do with the question T004~~ | Decided: removed | 14 Nov |
| Whether to build an on-site vote | Owner | After there are visitors |

## Risks

1. **A changed answer that is not the app's doing.** Search switching itself on, memory, country and silent model changes all move answers. Recorded conditions and a marker when they differ are the defence; the page says "the text differs", not "the app changed its mind".
2. **Terms change.** Re-read the four pages before each monthly run.
3. **Thin content.** Forty answers a month must stretch to eight or so posts.
4. **It rests on one person doing it every month.** The archive is only worth something if the months are not skipped.
5. **A wrong or harmful reply published under an app's name.** The owner reads every answer before approving; the content guards catch only patterns, not meaning.

## What would make us stop or change course

- One of the four services objects, or its terms change to forbid publishing or comparing.
- Three months of posts with no audience and no changed answers worth showing.
- An established account starts doing the same thing with a longer record.
