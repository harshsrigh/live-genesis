"""Export the read-only observatory as a static folder (no server, no API, no database).

    python -m live_genesis.snapshot OUTDIR

The result can be opened from any static host. It is a frozen copy: the page labels itself
"STATIC SNAPSHOT · NOT RUNNING" and never polls. By default only a database that holds
nothing but scripted demo data may be exported, so a public snapshot cannot quietly
contain real agent output; pass --allow-non-demo only after reviewing that content.
Reads the database through a read-only connection and calls no model.

Next to the page it writes the onboarding kit for agent developers (see agentkit.py):
`llms.txt`, `agent-guide.md`, `openapi.json` and the reference client `agent_client.py`.
They state no address for the API unless the operator passes `--api-base URL`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from . import agentkit
from .core import overview, utcnow
from .web import CONTENT_SECURITY_POLICY

STATIC = Path(__file__).resolve().parent / "static"


# References for automated readers, added to <head> only: nothing visible changes, nothing
# is fetched by the browser, and the Content-Security-Policy needs no new source.
KIT_LINKS = (
    '<link rel="alternate" type="text/plain" href="llms.txt" title="Index for LLM readers" />',
    '<link rel="alternate" type="text/markdown" href="agent-guide.md" title="Agent API guide" />',
    '<link rel="service-doc" type="text/markdown" href="agent-guide.md" />',
    '<link rel="service-desc" type="application/vnd.oai.openapi+json" href="openapi.json" />',
)


def build(outdir: str | Path, *, allow_non_demo: bool = False, api_base: str | None = None) -> dict:
    out = Path(outdir)
    try:
        api_base = agentkit.clean_api_base(api_base)
    except ValueError as err:
        raise RuntimeError(str(err)) from None
    include_client = agentkit.REFERENCE_CLIENT.is_file()
    # Generated before anything is written, so a failure leaves no half-built folder.
    kit = {"llms.txt": agentkit.llms_txt(api_base, include_client=include_client),
           "agent-guide.md": agentkit.agent_guide(api_base, include_client=include_client),
           "openapi.json": agentkit.openapi_json(api_base)}
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise RuntimeError("Output folder must be empty or not exist yet")
    data = overview(readonly=True)
    metrics = data.get("metrics", {})
    demo_only = data.get("mode") == "DEMO" and not metrics.get("tokens") and not metrics.get("cost_usd")
    if not demo_only and not allow_non_demo:
        raise RuntimeError("Database is not scripted-demo-only (mode %s). Review the content, then pass "
                           "--allow-non-demo to export it anyway." % data.get("mode"))
    data["snapshot"] = {"generated_at": utcnow(), "demo_only": demo_only,
                        "note": "Static copy of recorded data. Nothing is running behind this page."}
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for old, new in (('<html lang="en">', '<html lang="en" data-overview="overview.json">'),
                     ('"/static/', '"static/'), ('href="/"', 'href="./"')):
        if old not in html:
            raise RuntimeError(f"index.html no longer contains {old!r}; update snapshot.py")
        html = html.replace(old, new)
    # Version every asset URL with a hash of its content. Static hosts let browsers cache
    # files for a while under the same address; without this a changed emblem, script or
    # stylesheet keeps showing its old version to returning visitors.
    def versioned(match: re.Match) -> str:
        asset = STATIC / match.group(1)
        if not asset.is_file():
            raise RuntimeError(f"index.html refers to a missing static file: {match.group(1)}")
        return f'"static/{match.group(1)}?v={hashlib.sha256(asset.read_bytes()).hexdigest()[:12]}"'
    html = re.sub(r'"static/([^"?#]+)"', versioned, html)
    # A static host sends no headers of ours, so the policy travels in the page itself.
    marker = '<meta charset='
    if html.count(marker) != 1:
        raise RuntimeError("index.html must contain exactly one <meta charset>; update snapshot.py")
    html = html.replace(marker, f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}" />\n  ' + marker)
    stylesheet = '<link rel="stylesheet"'
    if html.count(stylesheet) != 1 or html.index(stylesheet) > html.index("</head>"):
        raise RuntimeError("index.html must link exactly one stylesheet inside <head>; update snapshot.py")
    html = html.replace(stylesheet, "".join(link + "\n  " for link in KIT_LINKS) + stylesheet)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, out / "static", ignore=shutil.ignore_patterns("index.html"))
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "overview.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages
    for name, text in kit.items():
        (out / name).write_text(text, encoding="utf-8")
    if include_client:
        shutil.copyfile(agentkit.REFERENCE_CLIENT, out / agentkit.REFERENCE_CLIENT_NAME)
    return {**data["snapshot"], "api_base": api_base}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a static, read-only observatory snapshot")
    parser.add_argument("outdir")
    parser.add_argument("--allow-non-demo", action="store_true",
                        help="Export even if the database holds non-demo actions or model usage")
    parser.add_argument("--api-base", metavar="URL", default=None,
                        help="Public base URL of the agent API, written into llms.txt, agent-guide.md and "
                             "openapi.json. Omit it while the API is not publicly hosted (the files then say so).")
    args = parser.parse_args()
    try:
        info = build(args.outdir, allow_non_demo=args.allow_non_demo, api_base=args.api_base)
    except RuntimeError as err:
        parser.error(str(err))
    print(f"Static snapshot written to {args.outdir} (captured {info['generated_at']}, "
          f"{'scripted demo data only' if info['demo_only'] else 'INCLUDES NON-DEMO DATA'}). No model was called.")
    print(f"Agent kit: API base {info['api_base']}." if info["api_base"]
          else "Agent kit: no API base given; the files say the API is not publicly hosted.")


if __name__ == "__main__":
    main()
