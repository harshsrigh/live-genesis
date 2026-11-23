"""The encrypted backup (`cli backup-encrypted`, `cli backup-check`).

Offline. gpg is replaced by a deterministic stand-in everywhere except in one test, which uses the real
program when it is installed, with an isolated GNUPGHOME and a throwaway passphrase fed through the
test-only parameter. No test asks for a passphrase on a terminal; every database is a scratch file.
"""
import argparse
import hashlib
import inspect
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from live_genesis import archive, cli, config, encrypted_backup as eb
from live_genesis.core import connect

MAGIC=b'STAND-IN-GPG\n'
WHEN=datetime(2026,12,3,9,8,7,tzinfo=timezone.utc)
NAME='answers-over-time-2026-12-03-090807.sqlite.gpg'


def fake_encrypt(source,destination,**_):
    Path(destination).write_bytes(MAGIC+bytes(b^0x5A for b in Path(source).read_bytes()))


def fake_decrypt(source,destination,**_):
    data=Path(source).read_bytes()
    if not data.startswith(MAGIC):
        raise eb.BackupError('This backup is damaged: gpg could not decrypt it. Use another copy of the file.')
    Path(destination).write_bytes(bytes(b^0x5A for b in data[len(MAGIC):]))


@pytest.fixture
def stand_in(isolated_db,tmp_path,monkeypatch):
    """A scratch database with scripted answers, a stand-in for gpg, a fixed time and a watched temporary folder."""
    archive.demo_archive()
    monkeypatch.setattr(eb,'_gpg_encrypt',fake_encrypt)
    monkeypatch.setattr(eb,'_gpg_decrypt',fake_decrypt)
    monkeypatch.setattr(eb,'_utc',lambda: WHEN)
    monkeypatch.setattr(shutil,'which',lambda name: '/usr/bin/gpg' if name=='gpg' else None)
    private=tmp_path/'private'; private.mkdir()
    monkeypatch.setattr(eb.tempfile,'tempdir',str(private))
    cloud=tmp_path/'cloud'; cloud.mkdir()
    return cloud


def private_left(tmp_path):
    return sorted(p.name for p in (tmp_path/'private').iterdir())


def run_cli(monkeypatch,*argv):
    monkeypatch.setattr(sys,'argv',['live_genesis.cli',*argv])
    cli.main()


def cli_code(monkeypatch,capsys,*argv):
    """(exit code, everything printed). 0 when the command returned normally."""
    code=0
    try:
        run_cli(monkeypatch,*argv)
    except SystemExit as stop:
        code=stop.code
    printed=capsys.readouterr()
    text=printed.out+printed.err
    assert 'Traceback' not in text and 'Error:' not in text, text
    return code,text


def repo_state():
    done=subprocess.run(['git','status','--porcelain','--ignored'],cwd=config.ROOT,capture_output=True,text=True)
    return done.stdout


def audit_rows():
    with connect() as cx:
        return [r[0] for r in cx.execute('SELECT operation FROM operator_audit ORDER BY id')]


def recorded_text():
    """Every answer and question text the scratch database holds."""
    with connect() as cx:
        words=[r[0] for r in cx.execute('SELECT answer_text FROM archive_answers WHERE answer_text IS NOT NULL')]
        words+=[r[0] for r in cx.execute('SELECT answer_text FROM archive_chatbot_answers WHERE answer_text IS NOT NULL')]
        words+=[r[0] for r in cx.execute('SELECT text FROM archive_chatbot_questions')]
    return [w for w in words if w and len(w)>8]


# ---- backup-encrypted -----------------------------------------------------------------------

def test_backup_encrypted_writes_the_file_and_a_receipt_with_counts_only(stand_in,tmp_path,monkeypatch,capsys):
    before_repo=repo_state()
    before_audit=audit_rows()
    code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
    assert code==0, out
    target=stand_in/NAME
    assert sorted(p.name for p in stand_in.iterdir())==[NAME,NAME+'.txt']
    assert private_left(tmp_path)==[]  # the plain copy and its folder are gone
    assert repo_state()==before_repo  # nothing written to the repository
    # What the owner is told.
    assert str(target) in out and 'KB' in out and eb.KEEP_SAFE in out
    assert f'python -m live_genesis.cli backup-check "{target}"' in out
    # The encrypted file is the database, and only through the stand-in.
    assert not target.read_bytes().startswith(b'SQLite format 3')
    plain=tmp_path/'plain.sqlite'
    fake_decrypt(target,plain)
    copy=sqlite3.connect(plain)
    assert copy.execute('SELECT COUNT(*) FROM archive_answers').fetchone()[0]>0
    assert copy.execute('SELECT operation FROM operator_audit ORDER BY id DESC').fetchone()[0]==f'backup-encrypted: copy taken for {NAME}'
    copy.close()
    # The audit row: the fact and the file name, never the folder.
    assert audit_rows()==before_audit+[f'backup-encrypted: copy taken for {NAME}']
    assert str(stand_in) not in ' '.join(audit_rows())
    # The receipt.
    receipt=(stand_in/(NAME+'.txt')).read_text(encoding='utf-8')
    facts=eb.read_receipt(stand_in/(NAME+'.txt'))
    assert facts['file']==NAME and facts['size-bytes']==str(target.stat().st_size)
    assert facts['sha256']==hashlib.sha256(target.read_bytes()).hexdigest()
    assert facts['made-utc']=='2026-12-03T09:08:07+00:00' and facts['user-version']=='0'
    with connect() as cx:
        assert facts['counts']==eb.table_counts(cx) and facts['counts']['archive_answers']>0
    assert int(facts['tables'])==len(facts['counts'])
    assert str(stand_in) not in receipt and str(tmp_path) not in receipt
    words=recorded_text()
    assert len(words)>50
    for word in words:  # counts only: no answer and no question can be in it
        assert word not in receipt
    for line in receipt.splitlines()[1:receipt.splitlines().index('')]:
        key,_,value=line.rpartition(': ')
        assert key.startswith('rows ') and value.isdigit() or key in ('file','size-bytes','sha256','made-utc','user-version','tables'), line


def test_a_marker_in_an_answer_never_reaches_the_receipt(stand_in,monkeypatch,capsys):
    marker='MARKER-zebra-7f3a'
    with connect() as cx:
        cx.execute('CREATE TABLE notes_for_this_test (answer_text TEXT)')
        cx.execute('INSERT INTO notes_for_this_test VALUES (?)',(marker,))
    assert cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))[0]==0
    receipt=(stand_in/(NAME+'.txt')).read_text(encoding='utf-8')
    assert 'rows notes_for_this_test: 1' in receipt and marker not in receipt
    plain=stand_in.parent/'plain.sqlite'
    fake_decrypt(stand_in/NAME,plain)
    assert marker.encode() in plain.read_bytes()  # it is in the backup itself, and only there


def test_backup_encrypted_refuses_before_writing_anything(stand_in,tmp_path,monkeypatch,capsys):
    before_repo=repo_state()
    before_audit=audit_rows()
    work_tree=tmp_path/'elsewhere'; (work_tree/'deep'/'er').mkdir(parents=True); (work_tree/'.git').write_text('gitdir: x',encoding='utf-8')
    locked=tmp_path/'locked'; locked.mkdir()
    refusals=[(tmp_path/'no-such-folder','does not exist'),
              (tmp_path/'a-file','does not exist'),
              (config.ROOT/'docs','inside the project or another git folder'),
              (config.ROOT,'inside the project or another git folder'),
              (work_tree/'deep'/'er','inside the project or another git folder')]
    (tmp_path/'a-file').write_text('x',encoding='utf-8')
    if os.geteuid()!=0:  # root may write anywhere, so the check cannot be shown as root
        locked.chmod(0o500)
        refusals.append((locked,'cannot be written to'))
    for folder,words in refusals:
        code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(folder))
        assert code==1 and words in out and 'Nothing was written' in out, (folder,out)
    locked.chmod(0o700)
    # An output file that already exists is never overwritten (same second, same name).
    for existing in (NAME,NAME+'.txt'):
        (stand_in/existing).write_text('older',encoding='utf-8')
        code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
        assert code==1 and 'never overwritten' in out
        assert (stand_in/existing).read_text(encoding='utf-8')=='older'
        (stand_in/existing).unlink()
    # gpg is not installed: one sentence that says how to install it.
    monkeypatch.setattr(shutil,'which',lambda name: None)
    code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
    assert code==1 and 'sudo apt install gnupg' in out and out.count('\n')==1
    monkeypatch.setattr(shutil,'which',lambda name: '/usr/bin/gpg')
    # A database that does not exist is refused and not created.
    missing=tmp_path/'not-there.sqlite'
    monkeypatch.setenv('LIVE_DB',str(missing))
    code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
    assert code==1 and 'no database to back up' in out and not missing.exists()
    monkeypatch.setenv('LIVE_DB',str(tmp_path/'genesis.sqlite'))
    assert list(stand_in.iterdir())==[] and private_left(tmp_path)==[]
    assert audit_rows()==before_audit and repo_state()==before_repo
    assert not list(work_tree.rglob('*.gpg')) and not list(locked.iterdir())


@pytest.mark.parametrize('failure',[eb.BackupError('gpg could not encrypt the copy (it stopped with code 2). Nothing was written.'),
                                    KeyboardInterrupt(),RuntimeError('unexpected')])
def test_the_plain_copy_is_removed_when_encryption_fails_or_is_interrupted(stand_in,tmp_path,monkeypatch,capsys,failure):
    seen={}
    def failing(source,destination,**_):
        seen['plain']=Path(source)
        seen['bytes']=Path(source).read_bytes()[:15]
        seen['mode']=Path(source).stat().st_mode&0o777
        seen['folder_mode']=Path(source).parent.stat().st_mode&0o777
        Path(destination).write_bytes(b'half written')
        raise failure
    monkeypatch.setattr(eb,'_gpg_encrypt',failing)
    if isinstance(failure,RuntimeError):
        with pytest.raises(RuntimeError):
            eb.make(stand_in)
    else:
        code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
        assert code==(130 if isinstance(failure,KeyboardInterrupt) else 1) and 'Encrypted backup saved' not in out
    assert seen['bytes']==b'SQLite format 3' and seen['mode']==0o600 and seen['folder_mode']==0o700
    assert not eb.in_git_work_tree(seen['plain'])  # the plain copy was made outside the repository
    assert not seen['plain'].exists() and private_left(tmp_path)==[]
    assert list(stand_in.iterdir())==[]  # no half-written file, no receipt


def test_a_copy_that_fails_its_checks_is_not_encrypted(stand_in,tmp_path,monkeypatch,capsys):
    for result,words in ((('row 3 missing from index',0,0,{}),'integrity check'),(('ok',2,0,{}),'2 broken link(s)')):
        monkeypatch.setattr(eb,'_examine',lambda path,result=result: result)
        code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
        assert code==1 and words in out and 'no backup was written' in out
        assert list(stand_in.iterdir())==[] and private_left(tmp_path)==[]


def test_a_temporary_folder_inside_a_git_folder_is_refused(stand_in,tmp_path,monkeypatch,capsys):
    (tmp_path/'private'/'.git').mkdir()
    code,out=cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))
    assert code==1 and 'TMPDIR' in out
    assert list(stand_in.iterdir())==[] and private_left(tmp_path)==['.git']


# ---- backup-check ---------------------------------------------------------------------------

@pytest.fixture
def made(stand_in,monkeypatch,capsys):
    assert cli_code(monkeypatch,capsys,'backup-encrypted','--to',str(stand_in))[0]==0
    return stand_in/NAME


def test_backup_check_proves_the_backup_opens_without_any_database(made,tmp_path,monkeypatch,capsys):
    live=tmp_path/'genesis.sqlite'
    live_before=live.read_bytes()
    nowhere=tmp_path/'another-computer'/'live.db'  # a computer that has only the backup
    monkeypatch.setenv('LIVE_DB',str(nowhere))
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==0, out
    lines=out.splitlines()
    assert lines[0].startswith('gpg will now open') and lines[1].split()==['Check','Result']
    assert [line.split('  ')[0].strip() for line in lines[2:7]]==['receipt beside the file','sha256 of the encrypted file',
        'opens with your passphrase','database integrity','rows in each table']
    assert 'matches the receipt' in lines[3] and lines[4].endswith('yes') and lines[5].endswith('ok')
    assert 'tables match the receipt' in lines[6]
    assert 'This backup opens and holds what the receipt says.' in out and 'Your database was not touched.' in out
    assert not nowhere.exists() and not nowhere.parent.exists()  # nothing created
    assert live.read_bytes()==live_before and private_left(tmp_path)==[]
    for word in recorded_text_of(live)[:40]:
        assert word not in out


def recorded_text_of(path):
    cx=sqlite3.connect(path)
    words=[r[0] for r in cx.execute('SELECT answer_text FROM archive_answers WHERE answer_text IS NOT NULL')]
    cx.close()
    return [w for w in words if len(w)>8]


def test_backup_check_without_a_receipt_says_so_and_continues(made,tmp_path,monkeypatch,capsys):
    made.with_name(NAME+'.txt').unlink()
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==0 and 'receipt beside the file' in out and 'missing' in out
    assert 'opens with your passphrase' in out and 'no receipt to compare' in out
    assert 'There was no receipt' in out and private_left(tmp_path)==[]


def test_backup_check_refuses_a_changed_file_before_opening_it(made,tmp_path,monkeypatch,capsys):
    def never(*args,**kwargs): raise AssertionError('a changed file is never decrypted')
    monkeypatch.setattr(eb,'_gpg_decrypt',never)
    data=bytearray(made.read_bytes()); data[-5]^=1
    made.write_bytes(bytes(data))
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==1 and 'PROBLEM: does not match the receipt' in out and 'It was not opened' in out
    assert private_left(tmp_path)==[]


def test_backup_check_reports_a_count_that_differs_from_the_receipt(made,tmp_path,monkeypatch,capsys):
    receipt=made.with_name(NAME+'.txt')
    text=receipt.read_text(encoding='utf-8')
    with connect() as cx:
        real=eb.table_counts(cx)['archive_answers']
    receipt.write_text(text.replace(f'rows archive_answers: {real}',f'rows archive_answers: {real+3}')
                       .replace('rows api_usage: 0\n',''),encoding='utf-8')
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==1 and f'archive_answers: receipt says {real+3}, backup holds {real}' in out
    assert 'api_usage: not in the receipt' in out and 'did NOT pass' in out
    assert private_left(tmp_path)==[]


def test_backup_check_on_a_damaged_or_wrong_file_ends_with_a_sentence(made,tmp_path,monkeypatch,capsys):
    receipt=made.with_name(NAME+'.txt')
    def rewrite(data):
        made.write_bytes(data)
        text=receipt.read_text(encoding='utf-8').splitlines()
        text[3]='sha256: '+hashlib.sha256(data).hexdigest()
        receipt.write_text('\n'.join(text)+'\n',encoding='utf-8')
    good=made.read_bytes()
    # Not something gpg can open (the receipt agrees with the file, so only decryption can tell).
    rewrite(b'not an encrypted file')
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==1 and out.rstrip().endswith('backup-check: This backup is damaged: gpg could not decrypt it. Use another copy of the file.')
    # It decrypts, but what is inside is not a database.
    rewrite(MAGIC+bytes(b^0x5A for b in b'just some words, long enough to be read as a file'*40))
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==1 and 'what is inside is not a database' in out
    # A wrong passphrase, as the real gpg seam reports it.
    rewrite(good)
    def wrong(source,destination,**_):
        Path(destination).write_bytes(b'partial')
        raise eb.BackupError('That passphrase did not open this backup. Nothing was changed; try again with the '
                             'passphrase you chose when the backup was made.')
    monkeypatch.setattr(eb,'_gpg_decrypt',wrong)
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==1 and 'That passphrase did not open this backup' in out
    # Ctrl+C while gpg is asking.
    def interrupted(source,destination,**_):
        Path(destination).write_bytes(b'partial')
        raise KeyboardInterrupt()
    monkeypatch.setattr(eb,'_gpg_decrypt',interrupted)
    assert cli_code(monkeypatch,capsys,'backup-check',str(made))[0]==130
    # A receipt that is not a receipt is a problem, and the check still goes on.
    monkeypatch.setattr(eb,'_gpg_decrypt',fake_decrypt)
    receipt.write_text('something else\n',encoding='utf-8')
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made))
    assert code==1 and 'PROBLEM: The receipt beside the backup is not a receipt' in out and 'database integrity' in out
    # A file that is not there.
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(tmp_path/'nothing.gpg'))
    assert code==1 and 'does not exist' in out
    assert private_left(tmp_path)==[]  # no decrypted file is left behind in any of these cases


def test_compare_live_same_more_and_backup_has_more(made,tmp_path,monkeypatch,capsys):
    live=tmp_path/'genesis.sqlite'
    def compared(out):
        table=out[out.index('Compared with the current database'):].splitlines()[1:]
        return {line.split()[0]:line.split(None,1)[1] for line in table if line and line[0].islower()}
    # Straight after the backup: everything is the same, the audit note included.
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made),'--compare-live')
    assert code==0 and set(compared(out).values())=={'same'} and 'WARNING' not in out
    # A later collection: the live database has more. Normal.
    with connect() as cx:
        for n in range(3):
            cx.execute("INSERT INTO operator_audit(operation,created_at) VALUES (?,?)",(f'later {n}','2026-12-04T00:00:00+00:00'))
    before=live.read_bytes()
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made),'--compare-live')
    assert code==0 and compared(out)['operator_audit']=='live has 3 more' and compared(out)['archive_answers']=='same'
    assert 'WARNING' not in out
    assert live.read_bytes()==before  # read-only
    # The live database is older than the backup: a warning.
    older=tmp_path/'older.sqlite'
    monkeypatch.setenv('LIVE_DB',str(older))
    from live_genesis.core import init_db
    init_db()
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made),'--compare-live')
    assert code==0 and compared(out)['archive_answers']=='backup has more than live'
    assert 'WARNING: the backup holds more than the current database' in out
    # No live database at all: said before gpg is asked for anything, and nothing is created.
    def never(*args,**kwargs): raise AssertionError('not reached')
    monkeypatch.setattr(eb,'_gpg_decrypt',never)
    monkeypatch.setenv('LIVE_DB',str(tmp_path/'gone.sqlite'))
    code,out=cli_code(monkeypatch,capsys,'backup-check',str(made),'--compare-live')
    assert code==1 and 'no current database to compare with' in out and not (tmp_path/'gone.sqlite').exists()
    assert private_left(tmp_path)==[]


# ---- The passphrase is nobody's business ----------------------------------------------------

def test_no_command_line_option_takes_a_passphrase(isolated_db,monkeypatch,capsys):
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command')
    eb.add_parsers(sub)
    options=set()
    for name,command in sub.choices.items():
        for action in command._actions:
            options|={(name,text) for text in (*action.option_strings,action.dest,str(action.metavar))}
    assert {text for _,text in options}=={'-h','--help','help','None','--to','to','DIR','file','FILE.gpg','--compare-live','compare_live'}
    for name,text in options:
        assert not any(word in text.lower() for word in ('pass','phrase','secret','key','fd')), (name,text)
    for extra in (['--passphrase','x'],['--passphrase-file','x'],['--passphrase-fd','3'],['--batch']):
        for command in (['backup-encrypted','--to','.'],['backup-check','x.gpg']):
            with pytest.raises(SystemExit) as stop:
                run_cli(monkeypatch,*command,*extra)
            assert stop.value.code==2
    capsys.readouterr()
    # The code reads no passphrase from the environment, and the only way in is the tests' parameter.
    source=Path(eb.__file__).read_text(encoding='utf-8')
    assert 'getpass' not in source and 'input(' not in source and '--passphrase ' not in source
    assert source.count('--passphrase-fd')==1
    for function in (eb.make,eb.check,eb.handle_command,eb.add_parsers):  # only the two seams and _gpg know the parameter
        assert 'passphrase_fd' not in inspect.getsource(function)
    assert '_passphrase_fd' not in Path(cli.__file__).read_text(encoding='utf-8')
    for banned in ('urllib','http','socket','requests','boto','azure','google'):
        assert f'import {banned}' not in source and f'from {banned}' not in source


def test_gpg_missing_is_one_plain_sentence(monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda name: None)
    with pytest.raises(eb.BackupError) as err:
        eb._gpg(['--version'])
    assert str(err.value)==eb.NO_GPG


@pytest.mark.skipif(shutil.which('gpg') is None,reason='gpg is not installed here')
def test_real_gpg_round_trip_with_a_throwaway_passphrase(isolated_db,tmp_path,monkeypatch):
    home=Path(os.environ.get('TMPDIR','/tmp'))/f'aot-gnupg-{os.getpid()}'  # short: gpg dislikes a long home path
    home.mkdir(mode=0o700)
    try:
        monkeypatch.setenv('GNUPGHOME',str(home))
        archive.demo_archive()
        marker='MARKER-quokka-91c2 '*20
        with connect() as cx:
            cx.execute('CREATE TABLE notes_for_this_test (answer_text TEXT)')
            cx.execute('INSERT INTO notes_for_this_test VALUES (?)',(marker,))
        def feeding(real,words):
            def run(source,destination,**_):
                reader,writer=os.pipe()
                os.write(writer,words+b'\n'); os.close(writer)
                try: real(source,destination,_passphrase_fd=reader)
                finally: os.close(reader)
            return run
        real_encrypt,real_decrypt=eb._gpg_encrypt,eb._gpg_decrypt
        monkeypatch.setattr(eb,'_gpg_encrypt',feeding(real_encrypt,b'throwaway words for one test'))
        monkeypatch.setattr(eb,'_gpg_decrypt',feeding(real_decrypt,b'throwaway words for one test'))
        monkeypatch.setattr(eb,'_utc',lambda: WHEN)
        cloud=tmp_path/'cloud'; cloud.mkdir()
        done=eb.make(cloud)
        target=cloud/NAME
        assert done['file']==target
        blob=target.read_bytes()
        assert not blob.startswith(b'SQLite format 3') and b'SQLite format 3' not in blob
        assert b'MARKER-quokka' not in blob and b'CREATE TABLE' not in blob
        with pytest.raises(sqlite3.DatabaseError):
            cx=sqlite3.connect(target)
            try: cx.execute('SELECT COUNT(*) FROM sqlite_master').fetchall()
            finally: cx.close()
        # gpg itself says what the file is: symmetric, AES256, no public key involved.
        listed=subprocess.run(['gpg','--batch','--list-packets','--pinentry-mode','cancel',str(target)],capture_output=True,text=True)
        assert 'symkey enc packet' in listed.stdout and 'cipher 9' in listed.stdout and 'hash 10' in listed.stdout
        assert 'count 65011712' in listed.stdout and 'pubkey enc packet' not in listed.stdout
        report=eb.check(target,with_live=True)
        assert report['ok'] and not report['warn'] and {value for _,value in report['live']}=={'same'}
        # Decrypt by hand and compare with the database, row for row.
        plain=tmp_path/'restored.sqlite'
        eb._gpg_decrypt(target,plain)
        assert marker.encode() in plain.read_bytes()
        restored=sqlite3.connect(plain)
        with connect() as cx:
            for table in eb.table_counts(cx):
                assert restored.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()==[tuple(r) for r in cx.execute(f'SELECT * FROM "{table}" ORDER BY 1')]
        restored.close()
        # A wrong passphrase and a cut-off file each end with a sentence and leave nothing behind.
        monkeypatch.setattr(eb,'_gpg_decrypt',feeding(real_decrypt,b'not the passphrase'))
        with pytest.raises(eb.BackupError,match='That passphrase did not open this backup'):
            eb.check(target)
        monkeypatch.setattr(eb,'_gpg_decrypt',feeding(real_decrypt,b'throwaway words for one test'))
        short=cloud/'cut-off.sqlite.gpg'
        short.write_bytes(blob[:len(blob)//2])
        with pytest.raises(eb.BackupError,match='damaged'):
            eb.check(short)
        other=cloud/'words.sqlite.gpg'
        other.write_bytes(b'these are just words, not an encrypted file\n'*10)
        with pytest.raises(eb.BackupError,match='not an encrypted backup'):
            eb.check(other)
        assert not list(Path(eb.tempfile.gettempdir()).glob('aot-backup-*'))
    finally:
        subprocess.run(['gpgconf','--kill','gpg-agent'],capture_output=True,env=dict(os.environ,GNUPGHOME=str(home)))
        shutil.rmtree(home,ignore_errors=True)


def test_the_guides_name_the_commands_and_no_way_to_hand_over_a_passphrase():
    docs=config.ROOT/'docs'
    backup=(docs/'BACKUP.md').read_text(encoding='utf-8')
    monthly=(docs/'MONTHLY-ROUND.md').read_text(encoding='utf-8')
    for guide in (backup,monthly):
        assert 'python -m live_genesis.cli backup-encrypted --to ~/cloud-backups' in guide
        assert 'python -m live_genesis.cli backup-check ~/cloud-backups/answers-over-time-' in guide
        assert '--passphrase' not in guide and 'pinentry-mode' not in guide and '--batch' not in guide
    assert monthly.index('### Step 9. Store it')<monthly.index('### Step 9a.')<monthly.index('### Step 9b.')<monthly.index('### Step 10.')
    assert 'python -m live_genesis.cli backup ../backup-before-' in monthly  # the plain copy before upgrades stays
    for words in (eb.KEEP_SAFE.split(':')[1].strip(),'sudo apt install gnupg','/mnt/c/Users','--compare-live','backup has more than live',
                  'gpg --no-symkey-cache --output ~/answers-restored.sqlite --decrypt','There is no "restore" command',
                  'What this does NOT protect against','Losing the passphrase','Not run:'):
        assert words in backup, words
    assert 'encrypted_backup' in (docs/'SECURITY.md').read_text(encoding='utf-8')
    for name in ('ROADMAP.md','COMPACT.md'):
        assert 'backup-encrypted' in (docs/name).read_text(encoding='utf-8'), name
