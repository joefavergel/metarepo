# Remedies in plain language

One entry per finding id. Say the "Means" in your own words; use the "Do" for the next step. Provider names
come from `sync.provider` in `project.yaml` (`google-drive`, `onedrive`, `dropbox`, `none`).

## contract-missing (error)
- **Means:** a folder or file that every metarepository must have is missing or has a different name.
  Programs and teammates look for these exact names.
- **Do:** restore the original name (a rename is the usual cause: the finding says what it found instead). If it
  was deleted, restore it from the provider's version history or trash. A missing `AGENTS.md`, `README.md`
  or `code/README.md` can be recreated (action `render-template`). Folders are never recreated automatically,
  because a renamed folder would end up duplicated.
- **Provider:** Google Drive: right-click the folder, "Manage versions" / Trash. OneDrive: Recycle bin on the web.
  Dropbox: "Deleted files" on the website.

## forbidden-content (error)
- **Means:** something that does not belong in a shared drive (a `.git` folder, `node_modules`, a virtual
  environment, `.env` files, keys, system junk such as `.DS_Store`). Cloud drives cannot filter these out, so
  doctor checks for them.
- **Do:** delete the file (action `remove`) or, for a folder, move it out of the synced folder (`move-out`).

## agents-md-shadowed (warn)
- **Means:** a `CLAUDE.md` / `.claude/CLAUDE.md` / `CLAUDE.local.md` in the project, or in a folder above it on this
  computer, makes Claude read that file instead of the project's `AGENTS.md`.
- **Do:** move the useful lines into the notes block of `AGENTS.md` and remove the other file; for a file above the
  project, move it or place the metarepository elsewhere. Doctor does not touch these files.

## nested-git (error)
- **Means:** a git repository's `.git` is inside the synced folder. Syncing it corrupts the repository and the
  drive.
- **Do:** move the `.git` entry out (action `move-out`, member picks the folder). The working files stay. For a
  real code repository, clone it outside the drive instead (`repos clone`).

## conflict-copy (warn)
- **Means:** two people changed the same file at once and the drive kept both. The copy has a marker in its name
  such as "conflicted copy", "[Conflict]", "(1)" or the other computer's name. "possible" means the match is
  only a guess from the name and time.
- **Do:** open both, keep what is right, then delete the losing copy (action `remove`, "likely" matches only).
  Never overwrite one with the other without reading both.
- **Provider:** Dropbox: "(<name>'s conflicted copy <date>)". OneDrive: "-<computer name>" suffix or "(1)". Google
  Drive for desktop: "[Conflict]" or "(1)". The exact names vary; trust the doctor message.

## cloud-native (warn)
- **Means:** `.gdoc`, `.gsheet`, ... files are only shortcuts to an online document; they contain no text, so
  nothing can be read or extracted from them.
- **Do:** in the browser, export the document (File > Download, as PDF or Word/Excel) and drop the exported file in
  `00_inbox/`. Then the shortcut can be deleted by its owner.

## not-local (info)
- **Means:** the file is listed but not downloaded to this computer ("online only"), so tools would see an empty
  stub.
- **Do:** make the folder available offline in the sync app, wait for the download, run again.
- **Provider:** Google Drive for desktop: right-click > Offline access > Available offline. OneDrive: "Always keep
  on this device". Dropbox: "Make available offline".

## path-too-long (warn)
- **Means:** the full path is longer than `policy.path_limit`; Windows and some sync apps fail above that.
- **Do:** shorten the file or folder names, or move it higher in the tree. Intake proposes short names itself.

## bad-name (warn)
- **Means:** the name is not portable: letters stored in a different Unicode form (looks equal, does not match),
  a Windows-reserved name (CON, NUL, COM1...), a trailing dot or space, or characters Windows rejects.
- **Do:** rename it with letters, digits, dashes and underscores.

## temp-stragglers (info)
- **Means:** a half-written temporary file left by an interrupted save.
- **Do:** safe to delete once nobody is writing (action `remove`).

## stale-lease (info)
- **Means:** a write lock whose time ran out and was never released (a crashed session). It blocks nobody; it is
  only untidy. An unreadable lock file is reported the same way.
- **Do:** release it (action `release-lease`), or delete an unreadable lock file once nobody is writing.

## toolkit-incompatible (error)
- **Means:** the installed toolkit version is outside the range the project declares (`toolkit.requires`).
  Writing commands refuse to run until that is fixed.
- **Do:** if the toolkit is older than the range, update it (plugin update or the newer bundle). If it is newer,
  run `migrate --dry-run`, show the result to the member and, after confirmation, `migrate --yes`.

## toolkit-artifacts (warn)
- **Means:** engine, skill or plugin files were copied into the project. Projects hold only content; the toolkit is
  installed per computer.
- **Do:** delete the copies after checking nothing else of value is in that folder; install the toolkit normally.

## runtime (error / info)
- **Means (error):** Python 3.10+ or PyYAML is missing, so the engine cannot work fully.
- **Do:** show the install command from the finding (`python3 -m pip install pyyaml`) and let the member run it.
  The toolkit never installs anything itself.
- **Means (info):** an optional text extractor is not installed; some file types are extracted less well or need
  the agent. **Do:** nothing required; show the install command if the member wants it.

## code-folder-content (error)
- **Means:** `code/` may only contain `README.md`; a code clone or other file was placed there.
- **Do:** move it out of the drive (action `move-out`, member picks the folder) and clone repositories outside
  the synced folder (`repos clone`).

## repo-inside-tree (error) / repo-missing (info)
- **Means:** a repository's local clone sits inside the synced folder (error), or the member has no local clone of
  a configured repository yet (info). Both depend on per-member settings; the check is skipped when those
  settings are not available yet.
- **Do:** move the clone out of the drive (action `move-out`); or clone it with `repos clone <name>`.

## large-file (info)
- **Means:** a very large file outside `10_sources/` slows sync for everyone.
- **Do:** keep it outside the shared drive, or add it as a source through `/intake`.

## inbox-age (info)
- **Means:** material has waited in `00_inbox/` longer than `doctor.inbox_max_age_days`.
- **Do:** run `/intake`, or delete what is not needed.

## secrets-suspect (warn)
- **Means:** the file name looks like a password, key or certificate (`*.pem`, `id_rsa`, `.env*`...). Anyone with
  access to the drive can read it.
- **Do:** move it to a private place. If it was shared, treat the credential as exposed and change it. Never open
  or print the file's content.

## 10_sources/
Anything doctor finds under `10_sources/` is reported without a fix: sources are never modified or deleted by the
toolkit. A wrong source is corrected by adding a new source that supersedes it.
