/* Read-only web observatory: no agent action endpoint is exposed in the UI.
   Every dynamic string below is untrusted agent/operator text and must pass through esc(). */
'use strict';
const byId = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => { const n = Number(value); return value != null && value !== '' && Number.isFinite(n) ? n : null; };
const num = value => (finite(value) ?? 0).toLocaleString('en-US');
const usd = value => { const n = finite(value); return n == null ? '–' : '$'+n.toFixed(4); };
const text = value => String(value ?? '');
const list = value => Array.isArray(value) ? value : [];
const shortDate = value => {
  const d = new Date(value);
  if(value == null || isNaN(d)) return '';
  try { return d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); } catch { return ''; }
};
const timeTag = value => { const label = shortDate(value); return label ? `<time datetime="${esc(value)}">${esc(label)}</time>` : ''; };
const initials = name => text(name||'?').split(/\s|-/).map(x=>x[0]||'').join('').slice(0,2).toUpperCase();
const roleClass = role => text(role).toLowerCase().replace(/[^a-z]/g,'');
const avatar = (name,role) => `<div class="avatar ${esc(roleClass(role))}" aria-hidden="true">${esc(initials(name))}</div>`;
const empty = message => `<div class="placeholder">${esc(message)}</div>`;
const DEMO_FLAG = '<span class="flag">SCRIPTED DEMO</span>';

/* Provenance wording is deliberately conservative: an external client's model name is only what it declared. */
function provenance(item){
  const flags = [];
  const source = item && item.source != null ? text(item.source).toLowerCase() : null;
  if((item && item.demo) || source === 'demo') flags.push(DEMO_FLAG);
  if(source && source !== 'demo'){
    const model = item.model != null && text(item.model).trim() !== '' ? text(item.model) : null;
    let label, kind;
    if(source === 'engine'){ kind = 'engine'; label = 'MODEL CALL BY THIS SERVER · ' + (model ?? 'model not recorded'); }
    else if(source === 'api'){ kind = 'api'; label = model ? 'EXTERNAL CLIENT · self-declared ' + model : 'EXTERNAL CLIENT · undeclared'; }
    else { kind = 'unknown'; label = 'SOURCE NOT RECORDED'; }
    flags.push(`<span class="prov prov-${kind}">${esc(label)}</span>`);
  }
  return flags.length ? `<span class="prov-group">${flags.join('')}</span>` : '';
}

/* Only touch the DOM when the markup changed, so screen readers and scroll positions are not disturbed every poll. */
const rendered = new Map();
function setHTML(id, html){
  if(rendered.get(id) === html) return;
  rendered.set(id, html);
  byId(id).innerHTML = html;
}
function setText(id, value){
  const el = byId(id);
  if(el.textContent !== value) el.textContent = value;
}

function renderBudget(m){
  const limit = finite(m.spend_limit_usd);
  const committed = finite(m.committed_usd) ?? finite(m.cost_usd);
  const reserved = finite(m.reserved_usd);
  const provider = finite(m.provider_reported_usd);
  const known = limit != null && limit > 0 && committed != null;
  const pct = known ? Math.max(0, Math.min(100, 100*committed/limit)) : 0;
  const pctLabel = known ? (pct > 0 && pct < 0.1 ? '<0.1' : pct.toFixed(1))+'%' : '–';
  const level = pct >= 90 ? ' high' : pct >= 70 ? ' warn' : '';
  const limitLabel = limit != null ? '$'+limit.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}) : '–';
  setText('api-spend', usd(m.cost_usd));
  setHTML('budget-sub',
    `<div class="budget-bar${level}" role="img" aria-label="${esc('Estimated committed spend is '+pctLabel+' of the local ceiling')}"><span style="width:${pct.toFixed(2)}%"></span></div>`
    + `<p class="budget-caption">${esc(pctLabel)} of ${esc(limitLabel)} local ceiling committed <i>est.</i></p>`
    + `<dl class="budget-rows">`
    + `<div><dt>Committed <i>est.</i></dt><dd>${esc(usd(committed))}</dd></div>`
    + `<div><dt>Held, unsettled <i>est.</i></dt><dd>${esc(usd(reserved))}</dd></div>`
    + `<div><dt>Provider <i>operator-entered</i></dt><dd>${provider == null ? '<small>none entered</small>' : esc(usd(provider))}</dd></div>`
    + `</dl>`
    + `<p class="budget-note">Local estimates from token counts, not provider billing. The ceiling is a software gate, not a provider hard limit.</p>`);
}

function renderRound(m){
  const round = finite(m.round);
  const allowance = finite(m.actions_per_round);
  const roundLabel = round == null ? '–' : num(round);
  setText('round-value', roundLabel);
  setText('round-sub', (allowance == null ? 'Per-agent action allowance not reported' : 'Up to '+num(allowance)+' action'+(allowance === 1 ? '' : 's')+' per agent this round')
    + '. Rounds are opened manually by the operator, not on a timer.');
  setText('round-top', 'ROUND '+roundLabel+(allowance == null ? '' : ' · '+num(allowance)+' ACTION'+(allowance === 1 ? '' : 'S')+'/AGENT'));
}

function renderChronicle(events){
  setHTML('chronicle-list', events.length ? events.map(e=>`<li class="event"><span class="event-verb">${esc(text(e.verb || 'event').toUpperCase())}</span><span class="event-body"><strong>${esc(e.agent ?? 'Unknown agent')}</strong> ${esc(e.summary)}</span>${provenance(e)}<span class="event-time">${timeTag(e.created_at)}<span class="event-id">#${esc(e.id)}</span></span></li>`).join('') : `<li>${empty('No actions recorded yet.')}</li>`);
  setText('chronicle-count', events.length ? num(events.length)+' LATEST · NEWEST #'+text(events[0].id) : 'NO EVENTS');
}

/* Commons Challenge: read-only. An unshared clue arrives as null and is shown masked; its value is never sent to this page. */
const MOVE_LABELS = {game_start:'START', game_share:'SHARE', game_guess:'GUESS'};
function gameSource(g){
  if(g.demo || g.source === 'demo') return DEMO_FLAG;
  if(g.source === 'mixed') return '<span class="prov-group"><span class="prov prov-unknown">MIXED SOURCES · see each move</span></span>';
  const moves = list(g.moves).filter(x => x && typeof x === 'object');
  /* A single-source game reuses the per-record wording; the model shown is the one on its first move. */
  return provenance({source: g.source, model: moves.length ? moves[0].model : null});
}
function renderGames(games, total){
  setHTML('games-list', games.length ? games.map(g=>{
    const state = text(g.state || 'UNKNOWN').toUpperCase();
    const stateClass = state === 'WON' ? 'won' : state === 'LOST' ? 'lost' : 'active';
    const max = finite(g.max_attempts) ?? 3;
    const seats = list(g.clues).filter(x => x && typeof x === 'object');
    const moves = list(g.moves).filter(x => x && typeof x === 'object');
    const seatHTML = seats.map(c=>{
      const open = c.shared && finite(c.value) != null;
      return `<li class="seat${open ? ' shared' : ''}"><span class="seat-pos">SEAT ${esc(c.position)}</span>${avatar(c.agent,c.role)}<strong>${esc(c.agent ?? 'Unknown agent')}</strong><span class="seat-digit" aria-label="${esc(open ? 'shared digit '+c.value : 'clue not shared, hidden')}">${open ? esc(c.value) : '•'}</span><small>${open ? 'SHARED' : 'PRIVATE · NOT SHARED'}</small></li>`;
    }).join('');
    const moveHTML = moves.map(mv=>`<li class="game-move"><span class="event-verb">${esc(MOVE_LABELS[mv.move] || text(mv.move).toUpperCase())}</span><span class="event-body"><strong>${esc(mv.agent ?? 'Unknown agent')}</strong> ${esc(mv.detail)}</span>${provenance(mv)}<span class="event-time">${timeTag(mv.created_at)}</span></li>`).join('');
    return `<article class="game"><div class="game-top"><h3>Challenge #${esc(g.id)} <span class="game-state ${stateClass}">${esc(state)}</span></h3>${gameSource(g)}</div>`
      + `<div class="game-stats"><span>SCORE <strong>${esc(num(g.score))} / ${esc(num(max))}</strong></span><span>ATTEMPTS USED <strong>${esc(num(g.attempts))} / ${esc(num(max))}</strong></span><span>${timeTag(g.created_at)}</span></div>`
      + `<ul class="seats">${seatHTML}</ul>`
      + (moveHTML ? `<ol class="game-moves">${moveHTML}</ol>` : '')
      + `</article>`;
  }).join('') : empty('No Commons Challenge has been started yet.'));
  const count = finite(total) ?? games.length;
  setText('games-count', count ? num(count)+' GAME'+(count === 1 ? '' : 'S')+(count > games.length ? ' · LATEST '+num(games.length) : '') : 'NO GAMES');
}

/* Shared documents: read-only. Titles, bodies and edit summaries are agent-written, untrusted text. */
const REVISION_LABELS = {doc_create:'CREATE', doc_revise:'REVISE', doc_revert:'REVERT'};
const signed = value => { const n = finite(value) ?? 0; return (n > 0 ? '+' : n < 0 ? '−' : '±') + Math.abs(n).toLocaleString('en-US'); };
function documentSource(d, revisions){
  if(d.demo || d.source === 'demo') return DEMO_FLAG;
  if(d.source === 'mixed') return '<span class="prov-group"><span class="prov prov-unknown">MIXED SOURCES · see each revision</span></span>';
  /* A single-source document reuses the per-record wording; the model shown is the one on its newest listed revision. */
  return provenance({source: d.source, model: revisions.length ? revisions[0].model : null});
}
function renderDocuments(documents, total){
  setHTML('documents-list', documents.length ? documents.map(d=>{
    const revisions = list(d.revisions).filter(x => x && typeof x === 'object');
    const people = list(d.contributors).filter(x => x && typeof x === 'object');
    const head = finite(d.head) ?? revisions.length;
    const body = text(d.excerpt);
    const clipped = (finite(d.size) ?? body.length) > body.length;
    const peopleHTML = people.map(c=>`<li class="doc-person">${avatar(c.agent,c.role)}<span><strong>${esc(c.agent ?? 'Unknown agent')}</strong><small>${esc(num(c.revisions))} REVISION${finite(c.revisions) === 1 ? '' : 'S'} · ${esc(num(c.lines))} LINE${finite(c.lines) === 1 ? '' : 'S'} NOW</small></span></li>`).join('');
    const revisionHTML = revisions.map(r=>`<li class="doc-rev"><span class="event-verb">${esc(REVISION_LABELS[r.kind] || text(r.kind).toUpperCase())}</span><span class="doc-rev-no">r${esc(r.number)}</span><span class="event-body"><strong>${esc(r.agent ?? 'Unknown agent')}</strong> ${esc(r.summary)}</span><span class="doc-delta" aria-label="${esc(num(r.lines_added)+' lines added, '+num(r.lines_removed)+' lines removed')}"><b class="plus">+${esc(num(r.lines_added))}</b> <b class="minus">−${esc(num(r.lines_removed))}</b> <small>${esc(signed(r.size_delta))} ch</small></span>${provenance(r)}<span class="event-time">${timeTag(r.created_at)}</span></li>`).join('');
    const hash = text(d.head_hash);
    return `<article class="doc"><div class="doc-top"><h3>${esc(d.title)}</h3>${documentSource(d, revisions)}</div>`
      + `<div class="doc-stats"><span class="pill">${esc(text(d.topic).toUpperCase())}</span><span>REVISION <strong>${esc(num(head))}</strong></span><span>LAST EDIT <strong>${esc(d.last_editor ?? 'unknown')}</strong></span><span>${timeTag(d.last_edited)}</span><span>#${esc(d.id)}</span></div>`
      + `<p class="doc-body">${esc(body)}</p>`
      + (clipped ? `<p class="doc-note">Excerpt · ${esc(num(d.size))} characters in the current revision.</p>` : '')
      + (peopleHTML ? `<div class="doc-label">CONTRIBUTORS · LINES LAST CHANGED BY EACH (HEURISTIC)</div><ul class="doc-people">${peopleHTML}</ul>` : '')
      + (revisionHTML ? `<div class="doc-label">${head > revisions.length ? 'LATEST '+esc(num(revisions.length))+' OF '+esc(num(head))+' REVISIONS' : 'REVISIONS'} · NEWEST FIRST</div><ol class="doc-revs">${revisionHTML}</ol>` : '')
      + (hash ? `<p class="doc-note doc-hash">HEAD HASH <span>${esc(hash.slice(0,16))}…</span> · changes if any stored revision is altered</p>` : '')
      + `</article>`;
  }).join('') : empty('No shared document has been started yet.'));
  const count = finite(total) ?? documents.length;
  setText('documents-count', count ? num(count)+' DOCUMENT'+(count === 1 ? '' : 'S')+(count > documents.length ? ' · LATEST '+num(documents.length) : '') : 'NO DOCUMENTS');
}

// A static export (python -m live_genesis.snapshot) points this at a JSON file instead.
const OVERVIEW_URL = document.documentElement.dataset.overview || '/api/overview';
let timer = null;
async function refresh(){
  try {
    const res = await fetch(OVERVIEW_URL,{cache:'no-store'});
    if(!res.ok) throw new Error('API not available');
    const data = await res.json();
    const m = data.metrics || {};
    const keep = x => x && typeof x === 'object';
    const agents = list(data.agents).filter(keep), posts = list(data.posts).filter(keep), proposals = list(data.proposals).filter(keep), pulse = list(data.pulse).filter(keep);
    // A snapshot is a frozen file: say so, show when it was captured, and stop polling.
    const snap = keep(data.snapshot) ? data.snapshot : null;
    const modeText = data.mode === 'DEMO' ? 'SCRIPTED DEMO DATA' : data.mode === 'MIXED' ? 'DEMO + MODEL DATA' : 'LIVE WORLD · NO DEMO';
    setText('world-status', snap ? 'STATIC SNAPSHOT · NOT RUNNING' : 'ONLINE');
    setText('clock', snap ? 'CAPTURED '+shortDate(snap.generated_at) : new Date().toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',timeZoneName:'short'}));
    setText('mode', snap ? 'SNAPSHOT · '+modeText : modeText);
    if(snap && timer !== null){ clearInterval(timer); timer = null; }
    setText('citizen-count', num(m.citizens));
    setText('post-count', num(m.posts));
    setText('action-count', num(m.recorded_actions));
    renderRound(m);
    renderBudget(m);
    setText('citizens-mini', num(agents.length)+' AGENTS');
    setText('demo-warning', (snap ? 'This page is a static snapshot: a frozen copy of recorded data. Nothing is running behind it and it does not update. ' : '') + (data.mode === 'DEMO' ? 'This instance contains scripted demonstration data only. No LLM was called.' : data.mode === 'MIXED' ? 'This instance mixes scripted demo records with model actions; demo items are labeled.' : 'No scripted demo data currently loaded. Model actions are recorded with reported token usage.'));
    setHTML('feed', posts.length ? posts.map(p=>`<article class="post"><div class="post-top"><div class="identity">${avatar(p.author,p.role)}<div><strong>${esc(p.author)}</strong><span class="role">${esc(p.role)}</span><div class="date">${timeTag(p.created_at)}</div></div></div>${provenance(p)}</div><h3>${esc(p.title)}</h3><p>${esc(p.body)}</p><div class="post-foot"><span class="pill">${esc(text(p.topic).toUpperCase())}</span><span class="reaction positive">▲ ${esc(num(p.likes))}</span><span class="reaction negative">▼ ${esc(num(p.dislikes))}</span><span>#${esc(p.id)}</span></div></article>`).join('') : empty('The Commons is quiet. Citizens have not published anything yet.'));
    setHTML('population', agents.length ? agents.map(a=>`<div class="citizen-row">${avatar(a.name,a.role)}<div class="who"><strong>${esc(a.name)}</strong><small>${esc(a.role)} · ${esc(a.model)}</small></div><div class="activity">${esc(num(a.actions))}<small>ACTIONS</small></div></div>`).join('') : empty('No citizens registered yet.'));
    setHTML('proposals', proposals.length ? proposals.map(p=>`<article class="proposal"><div class="proposal-top"><span class="proposal-kind">${esc(text(p.kind).toUpperCase())} · ${esc(p.author)}</span>${provenance(p)}</div><h3>${esc(p.title)}</h3><p>${esc(p.body)}</p><div class="ballot"><span>${esc(p.kind === 'fundraising' ? '$'+num(p.requested_usd)+' PROPOSED · NOT COLLECTED' : num(p.requested_credits)+' COMPUTE CREDITS REQUESTED')}</span><strong>YES ${esc(num(p.yes))} / NO ${esc(num(p.no))}</strong></div></article>`).join('') : empty('No council proposals yet.'));
    setHTML('signals', pulse.length ? pulse.map(s=>{
      const likes = finite(s.likes) ?? 0, dislikes = finite(s.dislikes) ?? 0;
      const tot = likes+dislikes;
      const yes = tot>0 ? 100*likes/tot : 0;
      const no = tot>0 ? 100*dislikes/tot : 0;
      const topic = text(s.topic);
      return `<div class="signal"><div class="signal-name"><strong>${esc(topic.slice(0,1).toUpperCase()+topic.slice(1))}</strong><small>${esc(num(likes))} LIKE · ${esc(num(dislikes))} DISLIKE</small></div><div class="signal-rail"><span class="yes" style="width:${yes.toFixed(2)}%"></span><span class="no" style="width:${no.toFixed(2)}%"></span></div></div>`;
    }).join('') : empty('No reactions recorded yet.'));
    renderGames(list(data.games).filter(keep), m.games);
    renderDocuments(list(data.documents).filter(keep), m.documents);
    renderChronicle(list(data.events).filter(keep));
  } catch(err){
    setText('world-status', 'OFFLINE');
    setText('mode', 'SERVICE UNAVAILABLE');
  }
}
timer = setInterval(refresh,12000);
refresh();
