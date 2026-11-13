#!/usr/bin/env node
// Answers Over Time: capture share cards as 1080 x 1920 PNG files.
//
//   node scripts/capture-cards.mjs --site ./site --out ./cards
//   node scripts/capture-cards.mjs --url https://example.org/project/ --out ./cards --ids Q11,T001
//   node scripts/capture-cards.mjs --site ./site --out ./cards --set trending
//
// Opens archive/card/<ID>.html at 540 x 960 CSS pixels with a device scale factor of 2 and writes
// <out>/<ID>.png. It reads the local site folder (--site) or the one base URL given (--url) and
// nothing else: every other request is blocked. It calls no model, opens no database, posts
// nowhere, and writes only the PNG files.
//
// Needs the `playwright` package and its Chromium, which the operator installs separately
// (the project itself has no Node dependency):  npm install -g playwright && npx playwright install chromium
//
// A card that carries the "Scripted demo" label is captured and reported as SCRIPTED DEMO.
// Never post such a card as a real answer. See docs/SOCIAL-POSTS.md.

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

const WIDTH = 540, HEIGHT = 960, SCALE = 2;
const ID = /^(Q[0-9]{2}|T[0-9]{3})$/;
const SETS = { core: 'Q', trending: 'T' };
const USAGE = `Usage: node scripts/capture-cards.mjs (--site DIR | --url BASE) --out DIR [--ids Q11,T001] [--set core|trending]

  --site DIR   a built site folder (the output of: python -m live_genesis.snapshot DIR)
  --url BASE   the address of a served site, e.g. http://127.0.0.1:8000/ or the published site
  --out DIR    where the PNG files go (created if missing)
  --ids LIST   comma-separated question ids; default: every card of the site
  --set NAME   core (Q..) or trending (T..) only`;

class Problem extends Error {}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '-h' || arg === '--help') { console.log(USAGE); process.exit(0); }
    const name = { '--site': 'site', '--url': 'url', '--out': 'out', '--ids': 'ids', '--set': 'set' }[arg];
    if (!name) throw new Problem(`Unknown option: ${arg}`);
    if (name in options) throw new Problem(`${arg} given twice`);
    const value = argv[i + 1];
    if (value === undefined || value.startsWith('--')) throw new Problem(`${arg} needs a value`);
    options[name] = value;
    i += 1;
  }
  if (!options.site === !options.url) throw new Problem('Give exactly one of --site DIR or --url BASE');
  if (!options.out) throw new Problem('--out DIR is required');
  if (options.set && !(options.set in SETS)) throw new Problem('--set must be core or trending');
  if (options.ids !== undefined) {
    options.ids = options.ids.split(',').map((id) => id.trim()).filter(Boolean);
    const bad = options.ids.filter((id) => !ID.test(id));
    if (bad.length || !options.ids.length) throw new Problem(`--ids takes ids such as Q11 or T001${bad.length ? `; not: ${bad.join(', ')}` : ''}`);
    options.ids = [...new Set(options.ids)];
  }
  return options;
}

function loadPlaywright() {
  // Look next to this script, in the current folder, and wherever NODE_PATH / a global install points.
  const tried = [import.meta.url, pathToFileURL(path.join(process.cwd(), 'x.js')).href];
  for (const from of tried) {
    try { return createRequire(from)('playwright'); } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND' || !String(error.message).includes("'playwright'")) throw error;
    }
  }
  throw new Problem('The "playwright" package was not found. It is not a dependency of this project; install it yourself:\n'
    + '    npm install -g playwright\n    npx playwright install chromium\n'
    + 'If it is installed globally and still not found, set NODE_PATH to the global folder:  export NODE_PATH="$(npm root -g)"');
}

function pngSize(file) {
  const head = Buffer.alloc(24);
  const fd = fs.openSync(file, 'r');
  try { fs.readSync(fd, head, 0, 24, 0); } finally { fs.closeSync(fd); }
  return [head.readUInt32BE(16), head.readUInt32BE(20)];
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  let base, allowed, siteRoot = null, origin = null, hostRules = 'MAP * ~NOTFOUND';
  if (options.site) {
    if (!fs.existsSync(options.site) || !fs.statSync(options.site).isDirectory()) throw new Problem(`--site: not a folder: ${options.site}`);
    siteRoot = fs.realpathSync(options.site);
    if (!fs.existsSync(path.join(siteRoot, 'archive', 'card'))) {
      throw new Problem(`--site: no archive/card/ folder in ${options.site}. Build the site first:  python -m live_genesis.snapshot DIR`);
    }
    base = pathToFileURL(siteRoot + path.sep);
    const rootHref = base.href;
    allowed = (url) => url.protocol === 'file:' && url.href.startsWith(rootHref);
  } else {
    try { base = new URL(options.url); } catch { throw new Problem(`--url: not a URL: ${options.url}`); }
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) throw new Problem('--url must be a plain http(s) address');
    base.search = ''; base.hash = '';
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    origin = base.origin;
    allowed = (url) => url.origin === origin;
    hostRules = null;   // a proxy between here and the site must stay resolvable; the request filter below does the blocking
  }

  const { chromium } = loadPlaywright();
  let browser;
  try {
    browser = await chromium.launch({ args: [...(hostRules ? [`--host-resolver-rules=${hostRules}`] : []),
      '--disable-background-networking', '--disable-component-update', '--no-first-run'] });
  } catch (error) {
    throw new Problem(`Chromium could not be started: ${String(error.message).split('\n')[0]}\n`
      + 'Install the browser Playwright uses:  npx playwright install chromium');
  }
  const failures = [];
  let written = 0, demo = 0;
  try {
    const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: SCALE,
      javaScriptEnabled: false, serviceWorkers: 'block', acceptDownloads: false });
    const blocked = new Set();
    const note = (url) => blocked.add(url ? (url.protocol === 'file:' ? url.pathname : url.origin) : 'unreadable address');
    await context.route('**/*', async (route) => {
      let url = null;
      try { url = new URL(route.request().url()); } catch { /* not a URL: blocked below */ }
      if (!url || !allowed(url)) { note(url); return route.abort(); }
      if (siteRoot) return route.continue();
      // A served site: fetch here and follow redirects by hand, so that a redirect cannot lead to another address.
      try {
        for (let hop = 0; hop < 5; hop += 1) {
          const response = await route.fetch({ url: url.href, maxRedirects: 0, timeout: 30000 });
          const location = response.headers().location;
          if (response.status() < 300 || response.status() > 399 || !location) return await route.fulfill({ response });
          url = new URL(location, url);
          if (!allowed(url)) { note(url); return await route.abort(); }
        }
      } catch { /* fall through */ }
      return route.abort().catch(() => {});
    });

    let ids = options.ids;
    if (!ids) {
      if (siteRoot) {
        ids = fs.readdirSync(path.join(siteRoot, 'archive', 'card')).filter((name) => name.endsWith('.html'))
          .map((name) => name.slice(0, -5)).filter((id) => ID.test(id));
      } else {
        // The site's own data file lists the questions; it is on the same address as the cards.
        const response = await context.request.get(new URL('archive.json', base).href, { maxRedirects: 0, timeout: 20000 }).catch(() => null);
        let data = null;
        if (response && response.ok()) { try { data = await response.json(); } catch { data = null; } }
        if (!data || !Array.isArray(data.questions)) {
          throw new Problem(`Could not read the question list at ${new URL('archive.json', base).href}. Name the cards yourself with --ids Q01,T001`);
        }
        const trending = data.trending && Array.isArray(data.trending.questions) ? data.trending.questions : [];
        ids = [...data.questions, ...trending].map((q) => String(q && q.id)).filter((id) => ID.test(id));
      }
    }
    if (options.set) ids = ids.filter((id) => id.startsWith(SETS[options.set]));
    ids.sort();
    if (!ids.length) throw new Problem('No cards to capture.');

    fs.mkdirSync(options.out, { recursive: true });
    const page = await context.newPage();
    for (const id of ids) {
      const target = new URL(`archive/card/${id}.html`, base);
      const file = path.join(options.out, `${id}.png`);
      try {
        if (siteRoot && !fs.existsSync(path.join(siteRoot, 'archive', 'card', `${id}.html`))) throw new Error('no such card in the site folder');
        const response = await page.goto(target.href, { waitUntil: 'load', timeout: 30000 });
        if (response && !siteRoot && !response.ok()) throw new Error(`the server answered ${response.status()}`);
        if (!allowed(new URL(page.url()))) throw new Error('the address redirected somewhere else');
        const card = page.locator('main.card');
        if (await card.count() !== 1) throw new Error('the page is not a share card');
        await page.evaluate(() => document.fonts.ready.then(() => null));
        const label = (await page.locator('.card-foot').innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
        await page.screenshot({ path: file, type: 'png', fullPage: false, animations: 'disabled' });
        const [w, h] = pngSize(file);
        if (w !== WIDTH * SCALE || h !== HEIGHT * SCALE) throw new Error(`the image is ${w} x ${h}, not ${WIDTH * SCALE} x ${HEIGHT * SCALE}`);
        const scripted = /scripted demo/i.test(label);
        if (scripted) demo += 1;
        written += 1;
        console.log(`${id}  ${file}${scripted ? '  SCRIPTED DEMO: not model output, do not post as real' : ''}`);
      } catch (error) {
        fs.rmSync(file, { force: true });
        failures.push(`${id}: ${String(error.message).split('\n')[0]}`);
      }
    }
    if (blocked.size) console.error(`Blocked requests to: ${[...blocked].sort().join(', ')}`);
  } finally {
    await browser.close();
  }
  console.log(`${written} card(s) written to ${options.out} at ${WIDTH * SCALE} x ${HEIGHT * SCALE}.`
    + (demo ? ` ${demo} of them show SCRIPTED DEMO data: never post those as real answers.` : ''));
  if (failures.length) {
    console.error(`Not captured:\n  ${failures.join('\n  ')}`);
    process.exit(1);
  }
}

main().catch((error) => {
  if (error instanceof Problem) {
    console.error(error.message);
    if (/^(Unknown option|Give exactly|--\w+ (given|needs|is required|must be|takes))/.test(error.message)) console.error(`\n${USAGE}`);
    process.exit(2);
  }
  console.error(`capture-cards failed: ${String(error && error.message ? error.message : error).split('\n')[0]}`);
  process.exit(1);
});
