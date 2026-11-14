# Plan: the chat-app comparison (second pivot)

Written 14 November 2026. Owner's decision: lead with the consumer chat apps, not API models. This plan uses only what exists today; anything not yet built or not yet verified is marked.

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
| The ten questions | Owner | Before the first import |
| Name | Owner | Before the first post |
| Keep Copilot despite the personal-use limit | Owner | Before any monetising |
| Ask logged in (recommended) or logged out | Owner | Before the first import; then keep it |
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
