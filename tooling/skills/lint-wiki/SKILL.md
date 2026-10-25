---
name: lint-wiki
description: "Check the integrity of the knowledge chain (sources, extractions, wiki pages, outputs): provenance, links, index and log consistency, policy matches, stale source pages, sign-off coverage. Leads with what needs a decision, groups the rest and proposes the command or skill that fixes each finding; it never edits anything itself. Use after a feed, before a handover or whenever the wiki looks wrong (/lint-wiki [--only <check-id,...>]). Environment and sync problems belong to /doctor."
---

# lint-wiki

Run the engine's integrity check and turn the result into a short list of decisions for a non-technical operator.
You report and route; the engine (`lint`) finds, and the owning skill or command fixes. **Lint is read-only and so
are you in this skill**: never edit a page, an extraction, `index.md`, `log.md` or `_system/` to make a finding go
away, and never open an original in `10_sources/`.

Engine resolution and conventions: `references/engine.md`. The check catalog, severities and which fix owns
which finding: `references/checks.md`. Speak in the language of the operator's first message.

Boundary: `/lint-wiki` checks **knowledge integrity**. Sync conflicts, offline files, runtime and toolkit
version are `/doctor`; point there when the operator describes those.

## Workflow

1. **Run (R1).** `<engine> lint --json` (add `--root <path>` outside the metarepository; `--only a,b` to narrow to
   some check ids). Exit `0` = no blocking finding, `1` = at least one `block`, `3` = not a metarepository,
   `2` = unknown check id. Errors do not change the exit code: read `data.summary`, not only the exit status.
   `data.findings[]` entries have `check`, `severity` (`block`, `error`, `warn`, `info`), `where` (a path relative
   to the metarepository), `detail` and `fix`. `warnings[]` reports setup problems (for example an ignored
   severity override); mention them briefly.
2. **Lead with decisions (R4).** Start with the `block` findings, then the `error` findings, each as one sentence:
   what is wrong, where, and what the operator must decide.
   - `policy-violation` is the blocking case. The detail names the rule and the location and a *masked* value.
     Never try to recover or print the value; never suggest lowering the severity unless the project owner
     decides so (it needs `allow_policy_downgrade: true` in `project.yaml`).
   - `extract-hash-mismatch` means a source changed after it was extracted (sources are immutable). Do not
     re-extract on your own; ask who changed it and restore the original from the sync provider history unless the
     operator decides the new file is the source.
3. **Group the rest.** Then `warn`, then `info`, grouped by check id and counted ("12 pages have no description").
   List paths only for the first few of a large group, and offer the full list on request. Say plainly when
   the result is clean.
4. **Route each finding to its owner (R4).** Use the `fix` field and `references/checks.md`:
   - wiki findings (`broken-link`, `orphan`, `index-drift`, `log-drift`, `no-description`, `dangling-footnote`,
     `no-sources`, `drift`, `unused-source`, `expired`, `source-page-stale`, `source-page-dangling`,
     `conformance` on a wiki page): `/feed-wiki`, the only wiki writer.
   - triage findings (`triage-missing`, a wrong `wiki`/`reference` decision): `/intake --retriage`.
   - extraction findings (`extract-failed`, `extract-orphan`, `conformance` on an extraction): `extract --force`
     (or `remove <path> --confirm <sha256>` for an orphan whose source is really gone).
   - `source-unregistered`, `not-fed`: `/intake` or `/feed-wiki` for pending sources.
   - sign-off findings (`sign-coverage`, `duty-overlap`): informational. Offer `/feed-wiki --sign` only when the
     operator states who signs in which role; never sign on their behalf.
   - `output-stale`: regenerate with `/make-output`.
5. **Offer, never apply silently.** Ask once per group which fixes to run. Each fix runs through its owning skill,
   which takes its own lease; do not take one here. After the fixes, run `lint` again and report what changed.
6. **Severity preferences.** If the operator thinks a check is too loud or too quiet, explain that wiki checks are
   tuned in `30_wiki/WIKI.md` and source/extraction checks in `project.yaml`, both under `lint.severity`
   (a map of check id to `block|error|warn|info`). Offer to edit them only as a normal, confirmed edit of those
   files. `policy-violation` cannot be lowered without `allow_policy_downgrade: true`.

## Rules

- Never modify anything to hide a finding; fixes are the owning skills' job.
- Never quote a policy match. Refer to the rule name and location only.
- Do not assume a domain, company, industry or language: judgment about whether a finding matters comes from
  `WIKI.md` and `project.yaml`.
- `lint` records `state.json -> last_lint` itself (best effort). Do not edit that file.
