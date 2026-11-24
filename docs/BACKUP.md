# A locked copy of your database, kept in the cloud

This guide is for you, the owner. It takes ten minutes the first time and two minutes after that.

**Why.** Everything you have collected lives in one file on your PC: the database. It is the only complete record. The website holds less than it does (for example, the words of a refused reply are in the database and nowhere else). If the PC breaks, is lost or is stolen, the record is gone.

**What this does.** One command makes a copy of the database, locks it with a passphrase only you know, and puts the locked file into a folder you name. If that folder is one your cloud program (OneDrive, Google Drive, Dropbox) already uploads, the copy ends up in the cloud by itself. A second command proves that the locked copy can be opened again.

**What it does not do.** The software never talks to a cloud service, never signs in anywhere and makes no request over the Internet. It only writes two files into a folder. Your cloud program does the uploading. Nothing here costs money or calls a model.

**Where to type.** As in `docs/MONTHLY-ROUND.md`: in the WSL window, in the project folder, with Python switched on.

```bash
cd ~/live-genesis
. .venv/bin/activate
```

You should see `(.venv)` at the start of the line.

---

## Once: set it up

### Step 1. Check that the locking program is there

The locking is done by GnuPG (`gpg`), a program that comes with Ubuntu.

```bash
gpg --version
```

You should see `gpg (GnuPG)` and a version number on the first line. If you see `command not found` instead, install it and try again:

```bash
sudo apt install gnupg
```

### Step 2. Choose the folder

Pick a folder that your cloud program already keeps in sync. On Windows with OneDrive, that is the OneDrive folder of your Windows user. From WSL, your Windows drive is under `/mnt/c`. Find your Windows user name:

```bash
ls /mnt/c/Users
```

You should see a short list of names. One of them is yours. Then look for the OneDrive folder (put your name in place of `YOURNAME`):

```bash
ls /mnt/c/Users/YOURNAME
```

You should see `OneDrive` in the list (a work account can be called `OneDrive - Something`; Google Drive and Dropbox have their own folders, often `Google Drive` or `My Drive`, and `Dropbox`).

Make a folder for the backups inside it:

```bash
mkdir -p "/mnt/c/Users/YOURNAME/OneDrive/AnswersOverTimeBackups"
```

You should see nothing: no message means it worked.

So that you never have to type that long path again, give it a short name in WSL:

```bash
ln -s "/mnt/c/Users/YOURNAME/OneDrive/AnswersOverTimeBackups" ~/cloud-backups
```

You should see nothing again. From now on `~/cloud-backups` means that folder. Every command below uses it.

If a folder called `cloud-backups` is already in your home folder (for example from an earlier try), the line above does not replace it: it quietly puts the short name *inside* that folder, and your backups stay on the PC. Check first, and move the old folder aside under another name:

```bash
ls -ld ~/cloud-backups
mv ~/cloud-backups ~/local-backups
```

After the `ln -s` line, `ls -ld ~/cloud-backups` should show an arrow (`->`) pointing at the folder in your cloud folder.

The folder must be **outside the project folder**. The command refuses a folder inside the project or inside any other git folder, because a database must never end up on GitHub, locked or not.

### Step 3. Choose the passphrase, and where you keep it

The passphrase is the only key. **Without it the backup cannot be opened, and nobody can recover it for you**: not this software, not whoever helps you with the code, not the cloud company.

- Make it **long**: five or six unrelated words are better than one clever word. A password manager can make one for you.
- Keep it **on paper in a safe place, or in a password manager**. Best: both.
- **Not** in the project folder. **Not** in a chat, with a person or with an AI. **Not** in a file next to the backup or anywhere in the cloud folder. **Not** only on this PC: if the PC is gone, so is the note.
- Use the same passphrase every time, so one note opens every backup.

The software never asks you for the passphrase and never sees it. `gpg` asks, in a small box of its own inside the window. Nothing in this project has an option, a setting or a file for it, and that is on purpose.

---

## Every time: make the copy, then prove it opens

### Step 4. Make the locked copy

```bash
python -m live_genesis.cli backup-encrypted --to ~/cloud-backups
```

A small box appears and asks for the passphrase; type it and press Enter. It asks a second time, to catch a typing slip. Then you should see:

```
gpg will now open a small box and ask you for a passphrase, twice. Type it and press Enter.
Encrypted backup saved: /mnt/c/Users/YOURNAME/OneDrive/AnswersOverTimeBackups/answers-over-time-2026-12-03-090807.sqlite.gpg
Size: 76.1 KB (77945 bytes). Receipt (counts only): answers-over-time-2026-12-03-090807.sqlite.gpg.txt
Keep the passphrase somewhere safe that is not this computer: without it the backup cannot be opened, and nobody can recover it for you.
Now prove that it opens:
  python -m live_genesis.cli backup-check "/mnt/c/Users/YOURNAME/OneDrive/AnswersOverTimeBackups/answers-over-time-2026-12-03-090807.sqlite.gpg"
```

The numbers in the name are the date and the time (in UTC, so the hour can differ from your clock). Two files were written:

- `answers-over-time-...sqlite.gpg`: the locked database. Without the passphrase it is noise.
- `answers-over-time-...sqlite.gpg.txt`: the **receipt**. Plain text you can open in Notepad: the file's name, size and fingerprint (sha256), the time, and how many rows each table holds. Numbers only: no question, no answer, no passphrase.

The command never overwrites a file, and it never changes your database apart from one line in its own log saying that a copy was taken.

### Step 5. Prove that it opens

Copy the last line the command printed (the one starting with `python -m`) and run it. It looks like this:

```bash
python -m live_genesis.cli backup-check ~/cloud-backups/answers-over-time-2026-12-03-090807.sqlite.gpg
```

The box asks for the passphrase once. You should see:

```
gpg will now open a small box and ask for the passphrase of this backup. Type it and press Enter.
Check                         Result
receipt beside the file       found
sha256 of the encrypted file  matches the receipt
opens with your passphrase    yes
database integrity            ok
rows in each table            all 17 tables match the receipt
This backup opens and holds what the receipt says.
The decrypted copy was deleted. Your database was not touched.
```

(The number of tables can differ on your database.) That is the proof: the file is the one that was written, your passphrase opens it, what is inside is a healthy database, and it holds exactly the rows the receipt lists. The check opens a private copy, looks, and deletes it again. It never writes into your database.

If you would also like to see the backup beside your database as it is now, add `--compare-live`:

```bash
python -m live_genesis.cli backup-check ~/cloud-backups/answers-over-time-2026-12-03-090807.sqlite.gpg --compare-live
```

You get a second table with one line per table:

| It says | It means |
|---|---|
| `same` | The backup and your database hold the same number of rows. |
| `live has 3 more` | Your database has grown since this backup. Normal after a later collection. Time for a new backup. |
| `backup has more than live` | **A warning.** Your database may be older than the backup. Stop, collect nothing, and find out why before you go on. |

### Step 6. See that it arrived in the cloud

Your cloud program uploads the two files by itself; give it a minute. It only does that while it is running: a folder called `OneDrive` can sit on the PC while the program itself is switched off (on the first real backup, 24 November 2026, it was). Look for its cloud icon beside the clock, or start it from the Start menu. Then open the cloud's website in your browser (for OneDrive: <https://onedrive.live.com>), sign in, open the folder `AnswersOverTimeBackups` and look for the two files with today's date. Do this at least the first time, and again whenever you change PC or cloud settings. A file that is only on this PC is not a cloud backup.

---

## How often

- **After every collection or import** (step 9 of `docs/MONTHLY-ROUND.md`, and after every ask of the models). A backup taken before a collection does not contain it.
- **Before any upgrade of the code**, together with the plain safety copy of step 1 in `docs/MONTHLY-ROUND.md`. That plain copy stays: it is the quick way back when an import went wrong the same day.
- Keep the older backups. They are small, and an old one can save you if a newer one turns out to be bad.

---

## Getting everything back on a new PC, from nothing

There is no "restore" command, on purpose: putting a backup in place of the real database is something only you should do, step by step, with your eyes open.

1. On the new PC, set up WSL and the project as before (Ubuntu, then `git clone` the project, make the Python environment, `python -m pip install -r requirements.txt`). Install the locking program:
   ```bash
   sudo apt install gnupg
   ```
2. Download the newest `answers-over-time-...sqlite.gpg` **and its `.txt` receipt** from the cloud's website into a folder outside the project, for example your home folder in WSL.
3. Prove that it opens. This works on a PC that has no database at all:
   ```bash
   python -m live_genesis.cli backup-check ~/answers-over-time-2026-12-03-090807.sqlite.gpg
   ```
   You should see the table of step 5, ending with `This backup opens and holds what the receipt says.`
4. Unlock it into a **new** file. The box asks for the passphrase:
   ```bash
   gpg --no-symkey-cache --output ~/answers-restored.sqlite --decrypt ~/answers-over-time-2026-12-03-090807.sqlite.gpg
   ```
   You should see two lines from gpg (`AES256.CFB encrypted data` and `encrypted with 1 passphrase`) and a new file `answers-restored.sqlite` in your home folder. If that file already exists, gpg asks before replacing it: answer no and pick another name.
5. Look at it before you trust it:
   ```bash
   LIVE_DB=~/answers-restored.sqlite python -m live_genesis.cli archive-status
   ```
   You should see a block of text that starts with `{`, and near the end, under `"chatbots"`, your rounds with their dates.
6. Only now make it your database. Either tell the project where it is, by putting this line into the file `.env` in the project folder (with your own WSL user name):
   ```
   LIVE_DB=/home/YOURNAME/answers-restored.sqlite
   ```
   or move it to where the old one was. If a database is already there, do **not** copy over it: move the old one aside first, under a new name.
7. Run `python -m live_genesis.cli archive-status` once more, without anything in front, and check that it shows the same rounds. Then make a fresh backup (step 4).

The unlocked file is your real database again: keep it out of the cloud folder and out of the project's git, like the old one.

---

## What this does NOT protect against

- **Losing the passphrase.** Then every backup is noise, for good. There is no back door.
- **The cloud account being closed, emptied or locked**, or the sync program quietly being switched off. Look at the cloud's website now and then (step 6). A second copy somewhere else (a USB stick in a drawer) costs nothing.
- **Anything collected after the last backup.** A backup holds the database as it was at that moment.
- **A weak passphrase.** The lock is only as strong as the phrase. Someone who gets the file can try guesses for as long as they like.
- **Someone who already has your PC unlocked.** The database on the PC itself is not locked; only the copies are.
- On the way, an unlocked copy sits for a few seconds in a private temporary folder on the PC and is then deleted. Deleted, not shredded.
- The receipt is not locked. It shows table names and row counts to whoever can see the folder. It holds no text from any question or answer.

## If something goes wrong

**"GnuPG (the program 'gpg') is not installed".** Step 1.

**"That folder does not exist."** Check the path (step 2). Is the cloud program installed, and is the name of your Windows user right?

**"That folder is inside the project or another git folder."** Name a folder outside it. This rule has no exception.

**"No passphrase was given".** The box was closed, or it could not open. Run the command in an ordinary WSL window (not inside another program), and try again. Nothing was written.

**"That passphrase did not open this backup."** A typing slip, or the wrong phrase. Nothing was changed. Try again.

**"This file is not the one the receipt was written for"** or **"This backup is damaged".** The file changed after it was made (a broken upload or download is the usual cause). Download it again, or use the backup before it. Do not delete anything.

**"This backup did NOT pass."** Keep the file, do not rely on it, make a new backup now, and tell whoever helps you with the code what the table said.

**Anything else.** Stop, change nothing, and copy the message to whoever helps you with the code. Never paste the passphrase with it.

---

*Checked on 23 November 2026 in a cloud session (Linux, GnuPG 2.4.4), against a scratch database made with `demo-archive` and a throwaway passphrase: `backup-encrypted --to` through a short name made with `ln -s` as in step 2, `backup-check` with and without `--compare-live`, the refusals (a folder inside the project, a folder that does not exist, a wrong passphrase), the `gpg --decrypt` line of the restore and `archive-status` on the restored file. The outputs shown above are the ones those runs printed, with the folder and the date changed. gpg's own passphrase box was typed into through a simulated terminal: both commands, and a wrong passphrase. Not run: anything on the owner's PC, a real OneDrive, Google Drive or Dropbox folder under `/mnt/c`, the upload itself, `sudo apt install gnupg`, and steps 1, 2, 6 and 7 of the restore on a second PC. The first real backup on the owner's PC is the first real check of those.*

*Checked on 24 November 2026 (UTC) on the owner's PC (WSL, GnuPG 2.4.8), on the real database, by the owner, who alone typed the passphrase into gpg's box: steps 1 to 6 with a personal OneDrive folder under `/mnt/c`. `backup-encrypted --to ~/cloud-backups` printed what step 4 shows; `backup-check` printed the table of step 5, with 45 tables; `--compare-live` said `same` for every table; a wrong passphrase gave "That passphrase did not open this backup." and left nothing behind (the backup folder held only `.gpg` files and their receipts, no unlocked copy in `/tmp`, the project's `git status` clean); the two files were seen on the cloud's website (how long the upload took was not noted). gpg's box asked every time, also a few minutes after a right passphrase. Two things did not match the guide and are now in it: a plain folder `~/cloud-backups` was already there, so the `ln -s` line of step 2 could not be used as written; and the OneDrive program was not running although its folder existed. Not run: the restore.*
