"""An encrypted copy of the database for a folder that a cloud-sync program uploads, and the check that it opens.

`cli backup-encrypted --to DIR` and `cli backup-check FILE.gpg`. Local files only: nothing here makes a
network request, talks to a cloud service or holds a credential. Uploading is the job of whatever sync
program watches the folder the owner names.

The passphrase belongs to the owner alone. GnuPG (`gpg`, an external program, not a Python dependency)
asks for it on the terminal by itself; this code never sees it. It is never an argument, an environment
variable, a file, a log line or a database row. Both `gpg` invocations go through `_gpg`, and the only
other way in is `_passphrase_fd`, a parameter for the tests that no command-line option reaches.

There is no command that restores over the live database: `docs/BACKUP.md` gives the steps by hand.

Honest limits: the plain copy made on the way sits for a moment in a private temporary folder (mode 0700,
file 0600) and is deleted, not shredded. The receipt beside the backup is plain text and holds table names
and row counts. An encrypted file is only as strong as its passphrase.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config, controls

PREFIX = "answers-over-time-"
SUFFIX = ".sqlite.gpg"
RECEIPT_SUFFIX = ".txt"
RECEIPT_TITLE = "Answers Over Time: receipt for an encrypted backup"
NO_GPG = ("GnuPG (the program 'gpg') is not installed, so nothing was written. Install it with: "
          "sudo apt install gnupg")
KEEP_SAFE = ("Keep the passphrase somewhere safe that is not this computer: without it the backup cannot be "
             "opened, and nobody can recover it for you.")
ENCRYPT_OPTIONS = ("--symmetric", "--cipher-algo", "AES256", "--s2k-digest-algo", "SHA512", "--s2k-count", "65011712")


class BackupError(RuntimeError):
    """One plain sentence for the owner. Never carries a passphrase."""


def _utc() -> datetime:
    """The time used in the file name and the receipt. Replaced in tests."""
    return datetime.now(timezone.utc)


# ---- GnuPG: the one place a passphrase is asked for --------------------------------------

def _gpg(arguments: list[str], _passphrase_fd: int | None = None) -> tuple[int, str]:
    """Run gpg and return its exit code and its status lines. gpg asks for the passphrase itself."""
    program = shutil.which("gpg")
    if not program:
        raise BackupError(NO_GPG)
    command = [program, "--no-symkey-cache", "--status-fd", "2"]
    pass_fds: tuple[int, ...] = ()
    if _passphrase_fd is not None:  # tests only: no command-line option leads here
        command += ["--batch", "--pinentry-mode", "loopback", "--passphrase-fd", str(_passphrase_fd)]
        pass_fds = (_passphrase_fd,)
    env = dict(os.environ)
    if "GPG_TTY" not in env:  # where gpg's own passphrase window is drawn; not a secret
        try:
            env["GPG_TTY"] = os.ttyname(0)
        except OSError:
            pass
    done = subprocess.run(command + arguments, stderr=subprocess.PIPE, env=env, pass_fds=pass_fds)
    return done.returncode, done.stderr.decode("utf-8", "replace")


def _could_not_ask(status: str) -> bool:
    return "get_passphrase" in status or "CANCELED_BY_USER" in status


def _gpg_encrypt(source: Path, destination: Path, *, _passphrase_fd: int | None = None) -> None:
    """Encrypt `source` into `destination` with a passphrase gpg asks for (twice). Replaced in tests."""
    code, status = _gpg([*ENCRYPT_OPTIONS, "--output", str(destination), str(source)], _passphrase_fd)
    if code != 0 or not destination.is_file():
        destination.unlink(missing_ok=True)
        if _could_not_ask(status):
            raise BackupError("No passphrase was given (the passphrase window was closed, or gpg could not open "
                              "it: run this in an ordinary terminal window). Nothing was written.")
        raise BackupError(f"gpg could not encrypt the copy (it stopped with code {code}). Nothing was written.")


def _gpg_decrypt(source: Path, destination: Path, *, _passphrase_fd: int | None = None) -> None:
    """Decrypt `source` into `destination` with the passphrase gpg asks for. Replaced in tests."""
    code, status = _gpg(["--output", str(destination), "--decrypt", str(source)], _passphrase_fd)
    if code == 0 and destination.is_file():
        return
    destination.unlink(missing_ok=True)
    if "NODATA" in status:
        raise BackupError("This file is not an encrypted backup, or it is damaged: gpg found nothing it could read.")
    if "BAD_PASSPHRASE" in status and not _could_not_ask(status):
        raise BackupError("That passphrase did not open this backup. Nothing was changed; try again with the "
                          "passphrase you chose when the backup was made.")
    if _could_not_ask(status):
        raise BackupError("No passphrase was given (the passphrase window was closed, or gpg could not open "
                          "it: run this in an ordinary terminal window). Nothing was changed.")
    raise BackupError("This backup is damaged: gpg could not decrypt it. Use another copy of the file.")


# ---- Small helpers ------------------------------------------------------------------------

def in_git_work_tree(path: Path) -> bool:
    """True inside this repository or any git work tree (a `.git` entry in the folder or above it)."""
    resolved = path.resolve()
    root = config.ROOT.resolve()
    for folder in (resolved, *resolved.parents):
        if folder == root or (folder / ".git").exists():
            return True
    return False


def _private_dir() -> Path:
    """A fresh folder only this user can read, outside every git work tree."""
    folder = Path(tempfile.mkdtemp(prefix="aot-backup-"))
    if in_git_work_tree(folder):
        shutil.rmtree(folder, ignore_errors=True)
        raise BackupError("The temporary folder of this computer is inside a git folder (see TMPDIR). "
                          "Nothing was written.")
    return folder


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def size_words(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB ({size} bytes)"
    return f"{size / (1024 * 1024):.1f} MB ({size} bytes)"


def _open_copy(path: Path) -> sqlite3.Connection:
    """Open a private copy strictly read-only (nobody else has it open, so `immutable` is safe)."""
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def table_counts(cx: sqlite3.Connection) -> dict[str, int]:
    """Rows per table. Table names and numbers only: no row is read."""
    names = [r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                      "ORDER BY name")]
    return {name: int(cx.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0])
            for name in names}


def _examine(path: Path) -> tuple[str, int, int, dict[str, int]]:
    """(integrity, foreign-key problems, user_version, counts) of a private copy."""
    try:
        cx = _open_copy(path)
    except sqlite3.Error:
        raise BackupError("The copy could not be opened as a database.") from None
    try:
        integrity = "; ".join(str(r[0]) for r in cx.execute("PRAGMA integrity_check"))
        foreign = len(cx.execute("PRAGMA foreign_key_check").fetchall())
        version = int(cx.execute("PRAGMA user_version").fetchone()[0])
        return integrity, foreign, version, table_counts(cx)
    except sqlite3.Error:
        raise BackupError("The copy is not a readable database.") from None
    finally:
        cx.close()


# ---- The receipt --------------------------------------------------------------------------

def receipt_text(name: str, size: int, digest: str, made: datetime, version: int, counts: dict[str, int]) -> str:
    lines = [RECEIPT_TITLE,
             f"file: {name}",
             f"size-bytes: {size}",
             f"sha256: {digest}",
             f"made-utc: {made.isoformat(timespec='seconds')}",
             f"user-version: {version}",
             f"tables: {len(counts)}"]
    lines += [f"rows {table}: {count}" for table, count in counts.items()]
    lines += ["",
              "The sha256 is of the encrypted file. This receipt holds table names and row counts only:",
              "no question, no answer, no passphrase. Check the backup with:",
              f"  python -m live_genesis.cli backup-check {name}"]
    return "\n".join(lines) + "\n"


def read_receipt(path: Path) -> dict:
    """Parse a receipt. Raises BackupError when it is not one."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise BackupError("The receipt beside the backup could not be read.") from None
    if not lines or lines[0] != RECEIPT_TITLE:
        raise BackupError("The receipt beside the backup is not a receipt written by this program.")
    facts: dict = {"counts": {}}
    for line in lines[1:]:
        if not line.strip():
            break
        key, _, value = line.rpartition(": ")
        if key.startswith("rows "):
            try:
                facts["counts"][key[5:]] = int(value)
            except ValueError:
                raise BackupError("The receipt beside the backup has a row count that is not a number.") from None
        elif key:
            facts[key] = value
    if len(facts.get("sha256", "")) != 64:
        raise BackupError("The receipt beside the backup has no sha256.")
    return facts


# ---- backup-encrypted ---------------------------------------------------------------------

def make(directory: str | os.PathLike[str], announce=lambda: None) -> dict:
    """Write DIR/answers-over-time-<UTC>.sqlite.gpg and its receipt. Refuses before writing anything."""
    from .core import connect, db_path, init_db
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise BackupError("That folder does not exist. Make it first, or name another one. Nothing was written.")
    folder = folder.resolve()
    if not os.access(folder, os.W_OK | os.X_OK):
        raise BackupError("That folder cannot be written to. Nothing was written.")
    if in_git_work_tree(folder):
        raise BackupError("That folder is inside the project or another git folder. A backup, even an encrypted "
                          "one, must never end up in git: name a folder outside it. Nothing was written.")
    if not db_path().is_file():
        raise BackupError("There is no database to back up (LIVE_DB points at a file that does not exist). "
                          "Nothing was written.")
    if not shutil.which("gpg"):
        raise BackupError(NO_GPG)
    made = _utc()
    name = f"{PREFIX}{made.strftime('%Y-%m-%d-%H%M%S')}{SUFFIX}"
    target, receipt, part = folder / name, folder / (name + RECEIPT_SUFFIX), folder / (name + ".part")
    if target.exists() or receipt.exists() or part.exists():
        raise BackupError(f"{name} already exists in that folder and is never overwritten. Wait a second and "
                          "run the command again. Nothing was written.")
    init_db()  # as every operator command does: the audit log exists before it is written to
    private = _private_dir()
    try:
        with connect() as cx:
            # Written before the copy is taken, so the backup holds its own note and a later comparison
            # with the live database says "same". The file name only: never the folder, never a passphrase.
            controls.audit(cx, f"backup-encrypted: copy taken for {name}")
        plain = controls.backup(private / "copy.sqlite")
        integrity, foreign, version, counts = _examine(plain)
        if integrity != "ok":
            raise BackupError("The copy of the database did not pass SQLite's integrity check, so no backup was "
                              "written. Stop and ask for help; do not delete older backups.")
        if foreign:
            raise BackupError(f"The copy of the database has {foreign} broken link(s) between tables (foreign key "
                              "check), so no backup was written. Stop and ask for help; do not delete older backups.")
        try:
            announce()  # every refusal is behind us: gpg asks next
            _gpg_encrypt(plain, part)
            if not part.is_file() or part.stat().st_size == 0:
                raise BackupError("gpg wrote no encrypted file. Nothing was kept.")
            if target.exists():
                raise BackupError(f"{name} appeared in that folder meanwhile and is never overwritten. Nothing was kept.")
            os.rename(part, target)
        finally:
            part.unlink(missing_ok=True)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        size, digest = target.stat().st_size, sha256_of(target)
        with open(receipt, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(receipt_text(name, size, digest, made, version, counts))
    finally:
        shutil.rmtree(private, ignore_errors=True)  # the plain copy: also on an error and on Ctrl+C
    return {"file": target, "receipt": receipt, "size": size, "sha256": digest, "tables": len(counts),
            "rows": sum(counts.values())}


# ---- backup-check -------------------------------------------------------------------------

def _compare_counts(expected: dict[str, int], found: dict[str, int]) -> list[str]:
    """Differences between two row-count tables, as plain phrases."""
    problems = []
    for table in sorted(set(expected) | set(found)):
        if table not in found:
            problems.append(f"{table}: missing from the backup")
        elif table not in expected:
            problems.append(f"{table}: not in the receipt")
        elif expected[table] != found[table]:
            problems.append(f"{table}: receipt says {expected[table]}, backup holds {found[table]}")
    return problems


def compare_live(backup: dict[str, int]) -> tuple[list[tuple[str, str]], bool]:
    """Row counts of the backup against the current database, read-only. Returns the lines and whether to warn."""
    from .core import DatabaseUnavailable, connect
    try:
        with connect(readonly=True) as cx:
            live = table_counts(cx)
    except (DatabaseUnavailable, sqlite3.Error):
        raise BackupError("There is no current database to compare with (LIVE_DB points at a file that does not "
                          "exist or cannot be read). Run the check again without --compare-live.") from None
    lines, warn = [], False
    for table in sorted(set(backup) | set(live)):
        if table not in live:
            lines.append((table, "backup has this table, live does not")); warn = True
        elif table not in backup:
            lines.append((table, f"live has this table ({live[table]} rows), backup does not"))
        elif live[table] == backup[table]:
            lines.append((table, "same"))
        elif live[table] > backup[table]:
            lines.append((table, f"live has {live[table] - backup[table]} more"))
        else:
            lines.append((table, "backup has more than live")); warn = True
    return lines, warn


def check(file: str | os.PathLike[str], *, with_live: bool = False, announce=lambda: None) -> dict:
    """Prove a backup opens. Reads the backup and, when asked, the live database; writes to neither."""
    source = Path(file).expanduser()
    if not source.is_file():
        raise BackupError("That backup file does not exist.")
    source = source.resolve()
    if with_live:
        from .core import db_path
        if not db_path().is_file():  # said before gpg asks for anything
            raise BackupError("There is no current database to compare with (LIVE_DB points at a file that does not "
                              "exist). Run the check again without --compare-live.")
    rows: list[tuple[str, str]] = []
    ok = True
    receipt_path = source.with_name(source.name + RECEIPT_SUFFIX)
    receipt: dict | None = None
    if not receipt_path.is_file():
        rows.append(("receipt beside the file", "missing (the file's sha256 and row counts cannot be compared)"))
    else:
        try:
            receipt = read_receipt(receipt_path)
            rows.append(("receipt beside the file", "found"))
        except BackupError as err:
            rows.append(("receipt beside the file", f"PROBLEM: {err}")); ok = False
    if receipt is not None:
        if sha256_of(source) != receipt["sha256"]:
            rows.append(("sha256 of the encrypted file", "PROBLEM: does not match the receipt"))
            return {"ok": False, "rows": rows, "live": [], "warn": False,
                    "message": "This file is not the one the receipt was written for: it was changed, damaged or "
                               "swapped after the backup was made. It was not opened. Use another copy."}
        rows.append(("sha256 of the encrypted file", "matches the receipt"))
    private = _private_dir()
    try:
        plain = private / "restored.sqlite"
        announce()
        _gpg_decrypt(source, plain)
        if not plain.is_file():
            raise BackupError("gpg wrote no decrypted file.")
        os.chmod(plain, 0o600)
        rows.append(("opens with your passphrase", "yes"))
        try:
            integrity, _foreign, version, counts = _examine(plain)
        except BackupError:
            rows.append(("database integrity", "PROBLEM: what is inside is not a database"))
            return {"ok": False, "rows": rows, "live": [], "warn": False,
                    "message": "The file opened, but what is inside is not a database. Use another backup."}
        if integrity == "ok":
            rows.append(("database integrity", "ok"))
        else:
            rows.append(("database integrity", "PROBLEM: SQLite's integrity check failed")); ok = False
        if receipt is not None:
            problems = _compare_counts(receipt["counts"], counts)
            if str(version) != receipt.get("user-version", str(version)):
                problems.append("the schema marker (user_version) differs from the receipt")
            if problems:
                rows.append(("rows in each table", "PROBLEM: " + "; ".join(problems))); ok = False
            else:
                rows.append(("rows in each table", f"all {len(counts)} tables match the receipt"))
        else:
            rows.append(("rows in each table", f"{len(counts)} tables, {sum(counts.values())} rows (no receipt to compare)"))
        live, warn = compare_live(counts) if with_live else ([], False)
    finally:
        shutil.rmtree(private, ignore_errors=True)  # the decrypted copy: also on an error and on Ctrl+C
    message = ("This backup opens and holds what the receipt says." if ok and receipt is not None else
               "This backup opens. There was no receipt, so its contents could not be compared with one." if ok else
               "This backup did NOT pass. Keep it, do not rely on it, and make a new one.")
    return {"ok": ok, "rows": rows, "live": live, "warn": warn, "message": message}


# ---- The command line -----------------------------------------------------------------------

def add_parsers(sub) -> None:
    make_parser = sub.add_parser("backup-encrypted", help="Encrypted copy of the database into a folder you name, for example "
                                                          "one your cloud-sync program uploads (free; no network; gpg asks "
                                                          "for your passphrase)")
    make_parser.add_argument("--to", required=True, metavar="DIR", help="An existing folder outside the project")
    check_parser = sub.add_parser("backup-check", help="Prove an encrypted backup opens: decrypts a private copy, checks it, "
                                                       "deletes it (free; changes nothing; gpg asks for your passphrase)")
    check_parser.add_argument("file", metavar="FILE.gpg")
    check_parser.add_argument("--compare-live", action="store_true",
                              help="Also compare row counts with the current database (read-only)")


def _table(rows: list[tuple[str, str]], left: str, right: str) -> str:
    width = max(len(left), *(len(name) for name, _ in rows)) if rows else len(left)
    return "\n".join(f"{name.ljust(width)}  {value}" for name, value in [(left, right), *rows])


def handle_command(args, parser) -> bool:
    """Runs before `init_db`: `backup-check` must work on a computer that has only the backup."""
    if args.command not in ("backup-encrypted", "backup-check"):
        return False
    try:
        if args.command == "backup-encrypted":
            done = make(args.to, lambda: print("gpg will now open a small box and ask you for a passphrase, twice. "
                                               "Type it and press Enter.", flush=True))
            print(f"Encrypted backup saved: {done['file']}")
            print(f"Size: {size_words(done['size'])}. Receipt (counts only): {done['receipt'].name}")
            print(KEEP_SAFE)
            print("Now prove that it opens:")
            print(f'  python -m live_genesis.cli backup-check "{done["file"]}"')
        else:
            report = check(args.file, with_live=args.compare_live,
                           announce=lambda: print("gpg will now open a small box and ask for the passphrase of this "
                                                  "backup. Type it and press Enter.", flush=True))
            print(_table(report["rows"], "Check", "Result"))
            if report["live"]:
                print()
                print(_table(report["live"], "Table", "Compared with the current database"))
                if report["warn"]:
                    print("WARNING: the backup holds more than the current database. The current database may be "
                          "older than this backup. Do not collect or import anything until you know why.")
            print(report["message"])
            print("The decrypted copy was deleted. Your database was not touched.")
            if not report["ok"]:
                parser.exit(1)
    except BackupError as err:
        parser.exit(1, f"{args.command}: {err}\n")
    except KeyboardInterrupt:
        parser.exit(130, f"\n{args.command}: stopped. The unencrypted copy was deleted.\n")
    return True
