"""Export the read-only observatory as a static folder (no server, no API, no database).

    python -m live_genesis.snapshot OUTDIR

The result can be opened from any static host. It is a frozen copy: the page labels itself
"STATIC SNAPSHOT · NOT RUNNING" and never polls. By default only a database that holds
nothing but scripted demo data may be exported, so a public snapshot cannot quietly
contain real agent output; pass --allow-non-demo only after reviewing that content.
Reads the database through a read-only connection and calls no model.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .core import overview, utcnow
from .web import CONTENT_SECURITY_POLICY

STATIC = Path(__file__).resolve().parent / "static"


def build(outdir: str | Path, *, allow_non_demo: bool = False) -> dict:
    out = Path(outdir)
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
    # A static host sends no headers of ours, so the policy travels in the page itself.
    marker = '<meta charset='
    if html.count(marker) != 1:
        raise RuntimeError("index.html must contain exactly one <meta charset>; update snapshot.py")
    html = html.replace(marker, f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}" />\n  ' + marker)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, out / "static", ignore=shutil.ignore_patterns("index.html"))
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "overview.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages
    return data["snapshot"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a static, read-only observatory snapshot")
    parser.add_argument("outdir")
    parser.add_argument("--allow-non-demo", action="store_true",
                        help="Export even if the database holds non-demo actions or model usage")
    args = parser.parse_args()
    try:
        info = build(args.outdir, allow_non_demo=args.allow_non_demo)
    except RuntimeError as err:
        parser.error(str(err))
    print(f"Static snapshot written to {args.outdir} (captured {info['generated_at']}, "
          f"{'scripted demo data only' if info['demo_only'] else 'INCLUDES NON-DEMO DATA'}). No model was called.")


if __name__ == "__main__":
    main()
