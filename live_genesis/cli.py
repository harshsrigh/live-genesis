"""Operator commands. The web side is read-only; everything that writes goes through here."""
from __future__ import annotations

import argparse
import json

from . import archive, controls, providers, trends
from .core import budget_status, db_path, init_db, record_reconciliation


def main():
    parser=argparse.ArgumentParser(description="Answers Over Time operations")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("init",help="Create the database, or add the tables a newer version needs (free; no model call; safe to repeat)")
    rec=sub.add_parser("reconcile",help="Compare local spend estimates with the provider's billing page (free; contacts no provider)")
    rec.add_argument("--provider-usd",type=float,help="Cumulative spend to date as shown on the provider's billing page")
    rec.add_argument("--note",default="",help="Where the figure came from, e.g. 'OpenAI usage page, 12 Nov'")
    rec.add_argument("--release-open",action="store_true",help="Also clear held/unconfirmed reservations (the entered figure already covers them)")
    rec.add_argument("--receipt",type=int,help="Reconcile ONE held/unconfirmed reservation by id (use with --actual-usd)")
    rec.add_argument("--actual-usd",type=float,help="Charge for that one call as verified on the provider's bill (0 if it was not billed)")
    bak=sub.add_parser("backup",help="SQLite online backup to a NEW file (the copy holds everything the database holds)")
    bak.add_argument("destination")
    providers.add_parser(sub)  # check-provider and archive-check-endpoints (free; no model call)
    archive.add_parsers(sub)  # archive-lock, archive-plan, archive-dry-run, archive-status, demo-archive, the chat-app commands (free) and archive-run (PAID)
    trends.add_parsers(sub)  # archive-trends (free; no model; prints candidates only)
    args=parser.parse_args()
    if trends.handle_command(args,parser): return  # before init_db(): it reads a public feed and never touches the database
    init_db()  # idempotent; also adds tables introduced after a database was first created
    if archive.handle_command(args,parser): return
    if providers.handle_command(args,parser): return
    if args.command=="init":
        print(f"Database ready: {db_path()}. No model was called.")
    if args.command=="backup":
        try: saved=controls.backup(args.destination)
        except (FileExistsError,FileNotFoundError) as err: parser.error(str(err))
        print("Backup saved:",saved)
        print("WARNING: the copy holds everything the database holds (for a database from before the agent society was "
              "removed: private agent memory and agent key hashes too). Keep it out of git and public folders.")
    if args.command=="reconcile":
        if (args.receipt is None)!=(args.actual_usd is None): parser.error("--receipt and --actual-usd must be given together")
        if args.receipt is not None:
            if args.provider_usd is not None or args.release_open or args.note: parser.error("--receipt/--actual-usd cannot be combined with --provider-usd, --release-open or --note")
            try: report=controls.reconcile_reservation(args.receipt,args.actual_usd)
            except ValueError as err: parser.error(str(err))
        elif args.provider_usd is None:
            if args.release_open or args.note: parser.error("--release-open and --note require --provider-usd")
            report=budget_status()
        else:
            if args.provider_usd<0: parser.error("--provider-usd must be zero or more")
            report=record_reconciliation(args.provider_usd,args.note,release_open=args.release_open)
        report["spend_limit_usd"]=report["ceilings"].get("spend_limit_usd")
        report["reminder"]="Local figures are estimates entered or computed here; only the provider's billing page shows real charges."
        print(json.dumps(report,indent=2))

if __name__=="__main__": main()
