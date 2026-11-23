# Sharing a card

What a visitor can do with a question's share card today, what was asked for and not built, and the one decision that would unlock the rest. Written 23 November 2026.

## What is built (no script)

Beside every chat-app question, on the front page and on the question's own page:

| Control | What it is | Where |
|---|---|---|
| Facebook | A plain link to Facebook's share page for the question's page. Present only when the build knows the site's public address (`LIVE_SITE_URL`). | Both |
| Save card | A plain link to the card's own PNG (`card/<ID>.png`) with a save name, for example `answers-over-time-C001-2026-11.png` (question id, then the year and month of the newest answers shown). | Both |
| Open picture | A plain link to the same PNG with no save hint, for browsers that ignore the hint and for touch-and-hold saving. | Both |
| The sentence | "Save the image, then upload it in Instagram or TikTok." and a note that saving depends on the browser. | Question page |
| Caption to copy | A closed block holding the caption as selectable text. | Question page |

The picture is the card the build already makes; nothing takes a new screenshot and no word of a card changes. When the build made no pictures (a local build without `LIVE_CARD_IMAGES=YES`), "Save card" and "Open picture" are replaced by one link to the card page, so no link points at a file that does not exist.

Nothing posts anywhere. No page says a card was posted, and no platform kit, account connection or publishing interface is used.

### The caption

Built from public data only (`archive_pages.caption`):

```
Is a hot dog a sandwich?
Replies collected: 19 Nov 2026
See the answers over time: https://harshsrigh.github.io/live-genesis/archive/c/C001.html
```

- The dates are the collection dates of the answers shown now, oldest first, never the date of an export.
- It never says an answer changed.
- A sample build starts the caption with "Sample only: we wrote these answers ourselves."
- Without a public address the last line is left out.

## What was asked for and is not built

A "Share card…" control that hands the PNG file to the phone's own share menu (`navigator.share({ files })`), and a "Copy caption" button that puts the caption on the clipboard. Both need a script, and this project's pages carry none:

- `CLAUDE.md`, rule 9: "Pages carry no script."
- `CLAUDE.md`, architecture map: `archive_pages.py` renders pages with "No script, no inline style".
- `tests/test_archive.py`: the page-safety check allows a closed list of elements, which has neither `script` nor `button`; several page tests assert that `<script` is absent.

The request itself said not to override such a rule silently, so the static flow above is what was built.

## The decision that would unlock it (owner's)

Allow **one** small script file of the project's own on the chat-app pages, for sharing only. The changes that decision would need:

1. `CLAUDE.md` rule 9 and the `archive_pages.py` line: from "no script" to "no script except `static/share.js`, which only prepares a card's picture for the device's share menu and copies a caption; no other script, no inline script, no handler attribute, nothing from another site".
2. `tests/test_archive.py`: admit `script` (only with `src` pointing at `static/share.js`, no inline content) and `button` (type `button` only) in the allow-lists, and replace the "`<script` is absent" assertions with "exactly that one script".
3. No change to the content security policy: it already has `script-src 'self'` and `connect-src 'self'`, which is what a same-site script fetching a same-site PNG needs. Nothing from another origin would be allowed.
4. One new static file and its tests (run in a browser under test with the share and clipboard interfaces replaced): sharing supported, sharing unsupported, picture fails to load, the visitor cancels, clipboard refused, and the page with scripts switched off.

What the script would do, if allowed: on a card's page or share line, fetch that one card's PNG ahead of the tap, enable "Share card…" only where the browser says it can share that file, pass the file to the share menu on the tap, stay quiet when the visitor cancels, fall back to the save links on any other error, and never claim anything was posted. The save links and the caption block stay as they are, so the page works the same with scripts off.

What it would cost: the pages stop being script-free, which is one of the plain promises of this site and part of why untrusted answer text is low-risk here. It is a small, contained exception, but it is an exception.

## What has been checked, and what has not

| Check | Result |
|---|---|
| Offline tests (`python -m pytest -q`) | Pass; see the pull request for the count |
| Built from `published/` as the workflow does, addresses under the `/live-genesis/` prefix | Checked: Facebook target, caption address and preview tags use `https://harshsrigh.github.io/live-genesis/archive/...`; the picture links are relative |
| Desktop Chromium at phone width (390, 360, 320): no sideways scroll, no console error, no script on the page | Checked |
| iPhone Safari, save then upload to Instagram and TikTok | **Not tested** (no device) |
| Android Chrome, save then upload to Instagram and TikTok | **Not tested** (no device) |
| Whether "Save card" saves, opens or asks on each phone browser | **Not tested**; it differs by browser, which is why "Open picture" sits beside it |
| Share menu, cancelling, no destination available | Not applicable: not built |

A desktop browser at phone size says nothing reliable about what a phone does with a download link. The first real check is the owner's: open a question page on their own phone, tap "Save card", and see where the picture goes.
