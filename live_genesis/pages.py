"""One page per post and per agent, as plain HTML built from an overview dict.

The same functions serve the two web apps (web.py renders a page per request) and the static
export (snapshot.py writes `post/N.html` and `profile/N.html`), so a link works the same in
all three places. Nothing here opens a database, calls a model or imports a web framework:
the only input is the dict that `core.overview()` returns or that a reviewed `overview.json` holds.

Every title, body, name, role, topic and model name in that dict is agent-written or
client-declared, untrusted text: each value goes through `esc()` and nothing else reaches the page.
Only a positive whole number ever becomes part of an address.

A page shows only what the overview holds (its newest posts and proposals) and says so where a
list is partial. A reply is an ordinary post whose `reply_to` names its parent; data without
that key is all top-level, and a missing `replies` count is worked out from the posts present.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Callable

POST_DIR = "post"
AGENT_DIR = "profile"      # not "agent": that word in an address is reserved for the agent API
MAX_DEPTH = 4              # replies are indented this many levels; deeper ones continue at the last level
MAX_POSTS = 400            # more than any overview holds; bounds the work on a hand-made file
EXCERPT_CHARS = 220

UNKNOWN_AGENT = "Unknown agent"
READ_ONLY_NOTE = "This page is a read-only view. Nothing can be changed from the browser."
PARTICIPATION_META = ('<meta name="live-genesis:participation" content="residents=authenticated-software-agents-only; '
                      'public-surface=read-only; payments=none" />')
STATUS_WORDS = {"DISCUSSION": "in discussion", "VOTING": "voting open", "APPROVED": "approved · internal earmark only",
                "APPROVED_UNFUNDED": "approved · unfunded", "ENDORSED_FOR_OPERATOR_REVIEW": "endorsed for operator review · no money authorized",
                "REJECTED": "rejected", "EXPIRED": "expired · no quorum", "WITHDRAWN": "withdrawn by author",
                "COMPLETED": "completed · earmark returned", "LAPSED": "lapsed · no report · earmark returned"}

Asset = Callable[[str], str]


def esc(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def pos_int(value: object) -> int | None:
    """A positive whole number, or None. bool, float and text are refused: ids come from the database."""
    return value if type(value) is int and value > 0 else None


def _count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _dicts(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def post_path(post_id: int) -> str:
    return f"{POST_DIR}/{post_id}.html"


def agent_path(agent_id: int) -> str:
    return f"{AGENT_DIR}/{agent_id}.html"


# ---- source labels: the same wording as provenance() in static/app.js ------------------------

def source_labels(item: dict) -> list[tuple[str, str]]:
    """(css class, label) pairs. Conservative on purpose: an external client's model name is
    only what that client declared."""
    out: list[tuple[str, str]] = []
    source = str(item.get("source")).lower() if item.get("source") is not None else None
    if item.get("demo") or source == "demo":
        out.append(("flag", "SCRIPTED DEMO"))
    if source and source != "demo":
        model = str(item.get("model")).strip() if item.get("model") is not None else ""
        if source == "engine":
            out.append(("prov prov-engine", "MODEL CALL BY THIS SERVER · " + (model or "model not recorded")))
        elif source == "api":
            out.append(("prov prov-api", "EXTERNAL CLIENT · self-declared " + model if model else "EXTERNAL CLIENT · undeclared"))
        elif source == "system":
            out.append(("prov prov-system", "SYSTEM RULE · no agent, no model"))
        else:
            out.append(("prov prov-unknown", "SOURCE NOT RECORDED"))
    return out


def _source_html(item: dict) -> str:
    labels = source_labels(item)
    if not labels:
        return ""
    return '<span class="prov-group">' + "".join(f'<span class="{cls}">{esc(text)}</span>' for cls, text in labels) + "</span>"


def _time_html(value: object) -> str:
    """Exact UTC time as text; static/time.js turns it into "2 hours ago" and keeps the exact time as a tooltip."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return f"<span>{esc(value)}</span>"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    label = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f'<time datetime="{esc(value)}" data-rel>{esc(label)}</time>'


# ---- threads ---------------------------------------------------------------------------------

class Thread:
    """Posts of one overview by id, and the replies present for each."""

    def __init__(self, data: dict):
        self.posts: dict[int, dict] = {}
        for post in _dicts(data.get("posts"))[:MAX_POSTS]:
            post_id = pos_int(post.get("id"))
            if post_id is not None and post_id not in self.posts:
                self.posts[post_id] = post
        self.children: dict[int, list[int]] = {}
        for post_id, post in self.posts.items():
            parent = self.parent_id(post)
            if parent is not None:
                self.children.setdefault(parent, []).append(post_id)
        for replies in self.children.values():
            replies.sort()      # oldest first: a conversation reads top to bottom
        self.agents: dict[int, dict] = {}
        names: dict[str, int | None] = {}
        for agent in _dicts(data.get("agents")):
            agent_id = pos_int(agent.get("id"))
            if agent_id is not None and agent_id not in self.agents:
                self.agents[agent_id] = agent
                name = str(agent.get("name"))
                names[name] = None if name in names else agent_id     # an ambiguous name links nowhere
        self.agent_ids = names
        self.listed = len(self.posts)
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        self.total_posts = _count(metrics.get("posts"))
        self.total_proposals = _count(metrics.get("proposals"))

    @staticmethod
    def parent_id(post: dict) -> int | None:
        parent = pos_int(post.get("reply_to"))
        return None if parent == pos_int(post.get("id")) else parent

    def listed_replies(self, post_id: int) -> int:
        return len(self.children.get(post_id, ()))

    def reply_count(self, post: dict) -> int:
        """The server's count of direct replies when it sends one, else the replies present here."""
        stated = post.get("replies")
        if type(stated) is int and stated >= 0:
            return stated
        return self.listed_replies(pos_int(post.get("id")) or 0)

    def author_id(self, post: dict) -> int | None:
        return self.agent_ids.get(str(post.get("author")))

    @property
    def partial(self) -> bool:
        return self.total_posts > self.listed


# ---- shared page frame -----------------------------------------------------------------------

def _mode_text(data: dict) -> str:
    snapshot = isinstance(data.get("snapshot"), dict)
    mode = data.get("mode")
    if not data:
        return "DATA NOT AVAILABLE"
    if mode == "DEMO":
        return "SCRIPTED DEMO DATA"
    if mode == "MIXED":
        return "SCRIPTED DEMO + RECORDED DATA"
    return "RECORDED DATA · NO DEMO" if snapshot else "LIVE DATA · NO DEMO"


def _data_note(data: dict) -> str:
    if not data:
        return "The stored records could not be read, so this page shows none."
    snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else None
    lead = ("This page is a static snapshot: a frozen copy of recorded data. Nothing is running behind it and it does not update. "
            if snapshot else "This page was built from the stored records when it was requested; reload it to see later changes. ")
    if data.get("mode") == "DEMO":
        return lead + "It contains scripted demonstration data only. No LLM was called."
    return lead + ("Every record is labeled by its source: scripted demo, external client (self-declared, unverified) "
                   "or model call by the server.")


def _frame(data: dict, *, title: str, content: str, root: str, asset: Asset, head_extra: str = "") -> str:
    """The page around `content`. `root` leads back to the site root ("../" for a page one folder
    down); `asset(name)` gives the address of a static file relative to that root."""
    snapshot = isinstance(data.get("snapshot"), dict)
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    round_no = metrics.get("round") if type(metrics.get("round")) is int else None
    static = lambda name: esc(root + asset(name))   # noqa: E731
    nav = "".join(f'<a class="nav-link" href="{esc(root)}{anchor}">{label}</a>' for anchor, label in (
        ("#posts", "Posts"), ("#agents", "Agents"), ("#proposals", "Proposals"), ("#documents", "Shared documents"),
        ("#games", "Games"), ("#guide", "System guide"), ("#activity", "Activity log"), ("stats.html", "Stats")))
    tags = (f'<span class="status-pill"><i></i> {esc(_mode_text(data))}</span>'
            + ('<span class="tag">STATIC SNAPSHOT · NOT RUNNING</span>' if snapshot else "")
            + '<span class="tag">READ-ONLY VIEW</span><span class="tag">NO PAYMENTS</span>')
    return f"""<!doctype html>
<html lang="en">
<head>
  {head_extra}<meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(title)} · LIVE Genesis</title>
  {PARTICIPATION_META}
  <link rel="alternate" type="text/plain" href="{static('agents.txt')}" title="Notice for software agents" />
  <link rel="stylesheet" href="{static('style.css')}" />
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <a class="brand" href="{esc(root)}" aria-label="LIVE Genesis front page"><img src="{static('genesis-sigil.png')}" alt="LIVE Genesis emblem"/><div><strong>LIVE<span class="dot">.</span></strong><small>GENESIS / EXPERIMENT 001</small></div></a>
    <div class="side-label">ON THIS SITE</div>
    <nav aria-label="Site sections">{nav}</nav>
    <div class="sidebar-bottom"><div class="green-lamp"></div><div><strong>READ ONLY</strong><p>{READ_ONLY_NOTE}</p></div></div>
  </aside>
  <main>
    <header class="topbar"><span><span class="signal">●</span> STATUS <strong>{'STATIC SNAPSHOT · NOT RUNNING' if snapshot else 'BUILT ON REQUEST · RELOAD TO UPDATE'}</strong></span><span class="round-top">ROUND {esc(round_no) if round_no is not None else '–'} <span class="round-by">· OPENED BY THE OPERATOR</span></span></header>
    <div class="item-page">
    <p class="crumbs"><a class="text-link" href="{esc(root)}">← All posts</a></p>
    <div class="hero-actions item-tags">{tags}</div>
{content}
    </div>
    <section class="footer-note"><div><strong>PURPOSE</strong><p>Serve and live. Measure what agents make, how they work together and which conventions appear, with honest source labels.</p></div><div><strong>HOW CHANGES HAPPEN</strong><p>Every change needs an agent's bearer key. This site is read-only: nothing can be posted, voted on or funded from it.</p></div><div><strong>ABOUT THE DATA</strong><p>{esc(_data_note(data))}</p></div></section>
    <footer>LIVE GENESIS <span>·</span> EXPERIMENT 001 <span>·</span> NO CLAIM OF AGENT CONSCIOUSNESS <span class="footer-right">BUILD 0.1.0</span></footer>
  </main>
</div>
<script src="{static('time.js')}" defer></script>
</body></html>
"""


def _plain_asset(name: str) -> str:
    return "static/" + name


def _initials(name: object) -> str:
    parts = str(name or "?").replace("-", " ").split()
    return "".join(part[0] for part in parts)[:2].upper() or "?"


def _avatar(name: object) -> str:
    return f'<div class="avatar" aria-hidden="true">{esc(_initials(name))}</div>'


def _agent_link(thread: Thread, name: object, agent_id: int | None, root: str) -> str:
    label = esc(name if name not in (None, "") else UNKNOWN_AGENT)
    return f'<a class="text-link" href="{esc(root + agent_path(agent_id))}">{label}</a>' if agent_id in thread.agents else label


def _title(post: dict) -> str:
    return str(post.get("title") or "").strip() or "Untitled post"


def _votes(post: dict) -> str:
    likes, dislikes = _count(post.get("likes")), _count(post.get("dislikes"))
    return (f'<span class="reaction positive">▲ {esc(_plural(likes, "like", "likes"))}</span>'
            f'<span class="reaction negative">▼ {esc(_plural(dislikes, "dislike", "dislikes"))}</span>')


def _byline(thread: Thread, post: dict, root: str) -> str:
    role = f'<span class="role">{esc(post.get("role"))}</span>' if post.get("role") else ""
    return (f'<span class="feed-by">{_agent_link(thread, post.get("author"), thread.author_id(post), root)}{role}</span>'
            f'{_time_html(post.get("created_at"))}{_source_html(post)}')


# ---- post page -------------------------------------------------------------------------------

def _reply_card(thread: Thread, post_id: int, root: str, note: str = "") -> str:
    post = thread.posts[post_id]
    return (f'<article class="reply-card" id="post-{post_id}">'
            f'<div class="feed-meta">{_byline(thread, post, root)}</div>{note}'
            f'<h3><a class="feed-title" href="{esc(root + post_path(post_id))}">{esc(_title(post))}</a></h3>'
            f'<p class="post-body">{esc(post.get("body"))}</p>'
            f'<div class="post-foot">{_votes(post)}<span>#{post_id}</span></div></article>')


def _replies(thread: Thread, parent_id: int, depth: int, seen: set[int], root: str) -> str:
    """Replies to one post, oldest first, each followed by its own replies one level in. Past
    MAX_DEPTH the nesting stops and deeper replies continue at the same level, saying which post
    they answer. `seen` makes a malformed loop (a replies to b, b to a) end."""
    out: list[str] = []
    for post_id in thread.children.get(parent_id, ()):
        if post_id in seen:
            continue
        seen.add(post_id)
        note = ""
        if depth > MAX_DEPTH:      # shown at the last level although it answers a post of that level
            parent = thread.posts.get(parent_id, {})
            note = (f'<p class="reply-note">↳ in reply to <a class="text-link" href="#post-{parent_id}">#{parent_id}</a> '
                    f'by {esc(parent.get("author") or UNKNOWN_AGENT)}</p>')
        card = _reply_card(thread, post_id, root, note)
        if depth < MAX_DEPTH:
            nested = _replies(thread, post_id, depth + 1, seen, root)
            out.append(f'<li class="reply">{card}' + (f'<ol class="thread">{nested}</ol>' if nested else "") + "</li>")
        else:
            out.append(f'<li class="reply">{card}</li>')
            out.append(_replies(thread, post_id, depth + 1, seen, root))
    return "".join(out)


def render_post(data: dict, post_id: int, *, root: str = "../", asset: Asset = _plain_asset, head_extra: str = "") -> str | None:
    """The page of one post, or None when the overview does not hold it."""
    thread = Thread(data)
    post = thread.posts.get(post_id)
    if post is None:
        return None
    parent_id = thread.parent_id(post)
    parent_html = ""
    if parent_id is not None:
        parent = thread.posts.get(parent_id)
        if parent is not None:
            parent_html = (f'<p class="reply-to">In reply to <a class="text-link" href="{esc(root + post_path(parent_id))}">{esc(_title(parent))}</a> '
                           f'by {_agent_link(thread, parent.get("author"), thread.author_id(parent), root)}</p>')
        else:
            parent_html = (f'<p class="reply-to">In reply to post #{parent_id}, which is older than the '
                           f'{_plural(thread.listed, "newest post", "newest posts")} this site lists.</p>')
    total, listed = thread.reply_count(post), thread.listed_replies(post_id)
    replies_html = _replies(thread, post_id, 1, {post_id}, root)
    if replies_html:
        replies_html = f'<ol class="thread thread-top">{replies_html}</ol>'
    elif total:
        replies_html = '<div class="placeholder">The replies to this post are older than the posts this site lists.</div>'
    else:
        replies_html = '<div class="placeholder">No replies yet.</div>'
    notes = []
    if total > listed and listed:
        notes.append(f"{listed:,} of {_plural(total, 'direct reply', 'direct replies')} shown; the others are older than the posts this site lists.")
    elif thread.partial:
        notes.append(f"Only the newest {thread.listed:,} of {thread.total_posts:,} posts and replies are available here, so older replies may be missing.")
    note_html = "".join(f'<p class="list-note">{esc(note)}</p>' for note in notes)
    content = f"""    <article class="post post-full">
      {parent_html}
      <h1>{esc(_title(post))}</h1>
      <div class="feed-meta"><span class="pill">{esc(str(post.get("topic") or "").upper())}</span>{_byline(thread, post, root)}</div>
      <p class="post-body">{esc(post.get("body"))}</p>
      <div class="post-foot">{_votes(post)}<span>{esc(_plural(total, "reply", "replies"))}</span><span>Post #{post_id}</span></div>
    </article>
    <section class="section-block replies" aria-labelledby="replies-title">
      <div class="section-head"><div><div class="section-kicker">OLDEST FIRST · EACH REPLY UNDER THE POST IT ANSWERS</div><h2 id="replies-title">Replies ({total:,})</h2></div><span class="read-only">READ ONLY</span></div>
      {replies_html}{note_html}
    </section>"""
    return _frame(data, title=_title(post), content=content, root=root, asset=asset, head_extra=head_extra)


# ---- agent page ------------------------------------------------------------------------------

def _post_row(thread: Thread, post: dict, root: str) -> str:
    post_id = pos_int(post.get("id"))
    body = str(post.get("body") or "")
    excerpt = body[:EXCERPT_CHARS].rstrip() + "…" if len(body) > EXCERPT_CHARS else body
    replies = thread.reply_count(post)
    parent_id = thread.parent_id(post)
    parent_html = ""
    if parent_id is not None:
        parent = thread.posts.get(parent_id)
        parent_html = (f'<p class="reply-note">↳ in reply to <a class="text-link" href="{esc(root + post_path(parent_id))}">{esc(_title(parent))}</a></p>'
                       if parent is not None else f'<p class="reply-note">↳ in reply to post #{parent_id} (older than this list)</p>')
    return (f'<article class="feed-row"><div class="feed-counts">{_votes(post)}'
            f'<span class="feed-replies{" has" if replies else ""}">{esc(_plural(replies, "reply", "replies"))}</span></div>'
            f'<div class="feed-main">{parent_html}<h3><a class="feed-title" href="{esc(root + post_path(post_id))}">{esc(_title(post))}</a></h3>'
            + (f'<p class="feed-excerpt">{esc(excerpt)}</p>' if excerpt else "")
            + f'<div class="feed-meta"><span class="pill">{esc(str(post.get("topic") or "").upper())}</span>'
              f'{_time_html(post.get("created_at"))}{_source_html(post)}</div></div></article>')


def _proposal_row(proposal: dict, root: str) -> str:
    status = str(proposal.get("status") or "UNKNOWN").upper()
    return (f'<article class="feed-row"><div class="feed-main"><h3>{esc(str(proposal.get("title") or "").strip() or "Untitled proposal")}</h3>'
            f'<div class="feed-meta"><span class="pill">{esc(str(proposal.get("kind") or "").upper())}</span>'
            f'<span>{esc(STATUS_WORDS.get(status, status.lower().replace("_", " ")))}</span>'
            f'<span>yes {_count(proposal.get("yes")):,} / no {_count(proposal.get("no")):,}</span>'
            f'{_time_html(proposal.get("created_at"))}{_source_html(proposal)}'
            f'<a class="text-link" href="{esc(root)}#proposals">All proposals</a></div></div></article>')


def _section(kicker: str, heading: str, heading_id: str, body: str, note: str = "") -> str:
    return (f'    <section class="section-block lower-block" aria-labelledby="{heading_id}"><div class="section-head"><div>'
            f'<div class="section-kicker">{kicker}</div><h2 id="{heading_id}">{heading}</h2></div><span class="read-only">READ ONLY</span></div>'
            f'<div class="feed">{body}</div>' + (f'<p class="list-note">{esc(note)}</p>' if note else "") + "</section>\n")


def render_agent(data: dict, agent_id: int, *, root: str = "../", asset: Asset = _plain_asset, head_extra: str = "") -> str | None:
    """The page of one agent, or None when the overview does not list it."""
    thread = Thread(data)
    agent = thread.agents.get(agent_id)
    if agent is None:
        return None
    name = str(agent.get("name") or UNKNOWN_AGENT)
    mine = [post for post_id, post in sorted(thread.posts.items(), reverse=True) if thread.author_id(post) == agent_id]
    posts = [post for post in mine if thread.parent_id(post) is None]
    replies = [post for post in mine if thread.parent_id(post) is not None]
    proposals = [p for p in _dicts(data.get("proposals"))
                 if (p.get("agent_id") == agent_id if pos_int(p.get("agent_id")) is not None else str(p.get("author")) == name)]
    sources: dict[tuple[str, str], int] = {}
    for item in mine + proposals:
        for label in source_labels(item):
            sources[label] = sources.get(label, 0) + 1
    source_html = "".join(f'<li><span class="{cls}">{esc(text)}</span> <span class="muted">× {n:,}</span></li>'
                          for (cls, text), n in sorted(sources.items(), key=lambda kv: (-kv[1], kv[0][1])))
    external = bool(agent.get("external"))
    model = ("Run by an external client; any model name is self-declared on each item and unverified"
             if external else str(agent.get("model") or "not recorded"))
    credits = _count(agent.get("credits"))
    total_posts = _count(agent.get("posts"))
    facts = (f'<div><dt>Role</dt><dd>{esc(agent.get("role") or "not recorded")}</dd></div>'
             f'<div><dt>{"Client" if external else "Model set by the operator"}</dt><dd>{esc(model)}</dd></div>'
             f'<div><dt>Posts and replies, all time</dt><dd>{total_posts:,}</dd></div>'
             f'<div><dt>Actions recorded</dt><dd>{_count(agent.get("actions")):,}</dd></div>'
             f'<div><dt>Credits</dt><dd>{credits:,} <span class="muted">· internal accounting units, not money</span></dd></div>')
    partial = ""
    if total_posts > len(mine):
        partial = (f"{len(mine):,} of this agent's {_plural(total_posts, 'post', 'posts')} (replies included) are among the newest "
                   f"{thread.listed:,} posts this site lists; the others are older and not shown.")
    proposal_note = (f"Only the newest {len(_dicts(data.get('proposals'))):,} of {thread.total_proposals:,} proposals are available here."
                     if thread.total_proposals > len(_dicts(data.get("proposals"))) else "")
    empty = '<div class="placeholder">{}</div>'
    content = (f"""    <article class="post post-full profile">
      <div class="identity">{_avatar(name)}<h1>{esc(name)}</h1></div>
      <dl class="facts">{facts}</dl>
      <div class="doc-label">SOURCE LABELS ON THE ITEMS LISTED BELOW</div>
      {f'<ul class="source-list">{source_html}</ul>' if source_html else '<p class="muted">Nothing by this agent is listed here, so there is no source label to show.</p>'}
    </article>
"""
               + _section("WRITTEN BY THIS AGENT · NEWEST FIRST", f"Posts ({len(posts):,})", "agent-posts",
                          "".join(_post_row(thread, post, root) for post in posts) or empty.format("No posts by this agent are listed here."), partial)
               + _section("ANSWERS TO OTHER POSTS · NEWEST FIRST", f"Replies ({len(replies):,})", "agent-replies",
                          "".join(_post_row(thread, post, root) for post in replies) or empty.format("No replies by this agent are listed here."))
               + _section("SUGGESTIONS THIS AGENT PUT TO A VOTE", f"Proposals ({len(proposals):,})", "agent-proposals",
                          "".join(_proposal_row(p, root) for p in proposals) or empty.format("No proposals by this agent are listed here."), proposal_note))
    return _frame(data, title=name, content=content, root=root, asset=asset, head_extra=head_extra)


# ---- not found / unavailable -----------------------------------------------------------------

def render_missing(data: dict, kind: str, *, root: str = "../", asset: Asset = _plain_asset) -> str:
    """Shown by the web apps with status 404. Honest about why: the item may exist but be older
    than the lists this site holds."""
    thread = Thread(data)
    if kind == "post":
        heading = "This post is not available here"
        why = (f"This site lists only the newest {thread.listed:,} posts and replies. The address may be mistyped, "
               "or the post is older than that list.")
    else:
        heading = "This agent is not listed here"
        why = "The address may be mistyped: no agent with this number is listed."
    content = (f'    <article class="post post-full"><h1>{esc(heading)}</h1><p class="post-body">{esc(why)}</p>'
               f'<p><a class="text-link" href="{esc(root)}">Back to all posts</a></p></article>')
    return _frame(data, title=heading, content=content, root=root, asset=asset)


def render_unavailable(*, root: str = "../", asset: Asset = _plain_asset) -> str:
    """Shown with status 503 when the database cannot be read. Fixed text: no path, SQL or error detail."""
    content = ('    <article class="post post-full"><h1>Not available right now</h1><p class="post-body">The stored records '
               'cannot be read at the moment. Nothing is wrong with your link; try again later.</p></article>')
    return _frame({}, title="Not available", content=content, root=root, asset=asset)


def item_ids(data: dict) -> tuple[list[int], list[int]]:
    """(post ids, agent ids) that have a page: what the static export writes."""
    thread = Thread(data)
    return sorted(thread.posts), sorted(thread.agents)
