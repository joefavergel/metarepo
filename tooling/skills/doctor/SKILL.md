---
name: doctor
description: "Check the health of a metarepository and this machine: folder contract, forbidden or misplaced content, sync conflict copies, offline files, toolkit compatibility, runtime, code repositories. Explains every finding in plain language and fixes only what the member confirms. Use when something looks wrong, after a sync problem, or before a handover (/doctor [--local])."
---

# doctor

Run the engine's health check and explain the result to a non-technical member. You explain and ask; the engine
(`doctor`) finds, and it is the only thing that fixes. Never delete, move or rename a file yourself, never edit
`10_sources/`, and never merge a conflict copy for the member.

Engine resolution and conventions: `references/engine.md`. Plain-language remedies per finding and provider:
`references/remedies.md`. Speak in the language of the member's first message.

## Workflow

1. **Scope.** Run `<engine> doctor --json` (add `--root <path>` when the current folder is not inside the
   metarepository). Use `--local` when the member only asks about this machine (Python, PyYAML, extractors,
   code clones, files not downloaded, toolkit version). Exit `0` = no errors, `1` = at least one error finding,
   `3` = not a metarepository. Doctor works even when the toolkit is outside `toolkit.requires`.
2. **Read the result.** `data.summary` has the counts. Each entry of `data.findings` has `id`, `severity`
   (`error`, `warn`, `info`), `path`, `message`, `remedy` and `actions`. `data.checks` lists checks that were
   `skipped` with a reason (for example repository clone paths before they are configured): mention them
   briefly, they are not problems.
3. **Explain, grouped by severity (R6).** Errors first, then warnings, then notes. For each finding say what it
   means in everyday words, why it matters, and what to do, using `references/remedies.md` (including the
   provider-specific step when `sync.provider` in the findings or `project.yaml` is known). Do not read
   the JSON back to the member. Say plainly when everything is fine.
4. **Offer fixes, never apply them silently (R3, R4).** Run `<engine> doctor --fix-safe --json`: this changes
   nothing and returns `data.actions` (id, kind, path, description). For each action ask the member one
   question that names the file and what will happen; ask separately for every action. Kinds:
   - `remove`: deletes one file (recoverable from the sync provider's trash). For `conflict-copy`, first show the
     member both versions (the copy and `details.original`) and let them decide which one wins; propose a
     merged text for wiki pages only as a suggestion, and only delete the copy that lost.
   - `move-out`: moves a folder or file out of the synced folder. Ask where to put it (a folder outside the synced
     drive, such as a `code` folder in their home folder); that path goes in `--dest`.
   - `release-lease`: tidies up an expired write lease.
   - `render-template`: recreates a missing `AGENTS.md`, `README.md` or `code/README.md` from the templates.
5. **Apply only what was confirmed.** For each confirmed action run
   `<engine> doctor --fix-safe --yes <action-id> [--dest <action-id>=<folder>] --json` (several `--yes` may be
   combined). Preview with `--dry-run` if the member wants. Report each entry of `data.applied` (`applied`,
   `skipped`, `failed`) honestly; a failed action means nothing was changed for that item.
6. **Re-check.** The apply run already re-scans; summarise what is left and which items need a person (renamed
   folders, cloud-only documents, incompatible toolkit, anything under `10_sources/`).

## Rules

- Anything under `10_sources/` is never fixed: it gets no action. Explain it and tell the member who can decide.
- Cloud-only documents (`.gdoc`, `.gsheet`, ...) cannot be fixed by the engine: tell the member to export the
  document (for example as PDF or `.docx`) and drop the exported copy in `00_inbox/`.
- A secret-looking file (`secrets-suspect`) is only reported: advise moving it to a private place and, if it
  was shared, changing the credential. Never open or print its content.
- Do not run `--fix-safe --yes` for an id the member did not approve, and do not guess `--dest`.
- When the toolkit is out of range, do not try to work around it: follow the remedy (update the toolkit, or
  run `migrate --dry-run` first and let the member confirm).
- Never install software. Missing optional extractors are notes: show the install command and stop.
