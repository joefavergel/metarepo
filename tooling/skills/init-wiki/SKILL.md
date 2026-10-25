---
name: init-wiki
description: "One-time guided setup of the project wiki in 30_wiki/: survey the extractions, interview the owner, propose a taxonomy, write WIKI.md, scaffold the OKF bundle, run the first feed and open the configuration gate. Use when the project has no wiki yet or the owner wants to reconfigure it (/init-wiki [--reconfigure])."
---

# init-wiki

Set up the project wiki once. You do the judgment (survey, interview, proposal); the engine does the
deterministic work (`wiki scaffold`, `wiki gate`). The order matters because the taxonomy cannot be decided from
the first document: **survey, interview, propose, scaffold, first feed, lint, gate.**

Engine resolution and conventions: `references/engine.md`. Survey method and axis mechanics:
`references/initiation.md`. Question set, defaults and the answers file: `references/interview.md`.
Speak to the operator in the language of their first message and follow switches; write the wiki prose in
`language.content` from `project.yaml`.

## Workflow

1. **Preconditions (R1).** Run `<engine> status --json`. The metarepository gate must be open
   (`data.gates.metarepo_configured`); otherwise point to `/create-metarepo`. If `data.gates.wiki_configured` is
   already true, stop: explain that the wiki exists and point to `/feed-wiki` (add material) and `/consult-wiki`
   (ask questions). Only continue when the operator passed `--reconfigure` or explicitly asks to reconfigure;
   reconfiguring never deletes pages.
2. **Survey (R2).** Follow `references/initiation.md`. Read `project.yaml` (`config get`) and sample
   `20_extracts/` through the engine (`registry`, `search`) within a token budget. If there are no extractions,
   recommend running `/intake` first, but allow continuing with an empty survey.
3. **Can you ask questions?** If you cannot ask the operator and no answers file was supplied, create nothing:
   explain that this skill needs an interview, or that `wiki scaffold --answers <file>` can be run directly.
4. **Interview (R3).** Ask the questions in `references/interview.md` (objective, profile, organizing axis, areas,
   page types, what earns a page, ingest emphasis, governance). Use `AskUserQuestion` when available, plain
   questions otherwise. Offer the neutral starter page types as suggestions only.
5. **Answers file.** Write `answers.json` to a temporary location **outside** the synced folder.
6. **Proposal (R4).** Run `<engine> wiki scaffold --answers <answers.json> --dry-run --json`. Show the operator
   the highlights of the planned `WIKI.md` (objective, axis, page types, threshold), the folder tree
   (`data.folders`) and an estimate of first-pass pages from the survey. Wait for explicit confirmation: this is
   the cheapest moment to change the design. Surface every warning.
7. **Scaffold (R5).** On confirmation run the same command without `--dry-run`. Report `data.created` and
   `data.kept`. Never retry by deleting files. A re-run with the same answers is a no-op.
8. **First feed (R7).** Chain to `/feed-wiki` if the runtime can invoke sibling skills; otherwise end with the exact
   phrase to say next (for example "run /feed-wiki"). Recommend starting with the sources that define the project's
   vocabulary, and checking page granularity after the first two or three sources; adjust `WIKI.md` with the
   operator if pages come out too coarse or too fine. The feed is allowed between scaffold and gate.
9. **Lint and gate (R8).** After the first feed run `<engine> lint --json` when it is available and fix what it
   reports with the operator. Then run `<engine> wiki gate --complete --json`. It validates every page (parseable,
   `type` present) and sets `wiki.configured`. Exit 1 means invalid pages: fix them, run again. Do not set the
   gate any other way.
10. **Close (R9).** Tell the operator, in their language: add material by dropping files in `00_inbox/` and running
    `/intake`; ask questions with `/consult-wiki`; `30_wiki/WIKI.md` is theirs to edit (the YAML block between the
    `wiki-config` markers must stay valid).

## Rules

- The engine is the only writer of the bundle scaffold, `WIKI.md` and `_system/state.json`. Do not create or edit
  these files by hand, and do not copy toolkit files into the project.
- Do not assume a domain, company, industry or language. Page types, areas and thresholds come from the
  operator's answers; the starter list is a suggestion.
- Do not invent answers for questions the operator skipped; use the defaults in `references/interview.md`.
- Wiki writes need the `wiki` lease; the engine takes it. In owner mode only the listed owners can scaffold; others
  are told who can.
