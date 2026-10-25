---
name: make-output
description: "Produce a deliverable (one-pager, slides, report, brief, FAQ or a project-defined kind) from the project wiki into the outputs folder, cited back to wiki pages and registered with provenance so staleness can be detected. Never invents content the wiki lacks. Use when the operator asks for a document, deck or summary built from what the project knows (/make-output <topic>)."
---

# make-output

Turn what the wiki says into a deliverable. The wiki is the only source of content; an output is a **derived
artifact, never a source** (it is not ingested, and it can be regenerated when the wiki changes). The engine
names, hashes and registers the file; you gather, write and iterate. Engine resolution and conventions:
`references/engine.md`. Structure per kind, citation styles, Marp and single-file HTML rules:
`references/kinds.md`. Answer in the operator's language.

## Workflow

1. **Gate.** Run `<engine> status --json`. If `data.gates.wiki_configured` is false, stop and point to `/init-wiki`.
2. **Collect the inputs (R2).** Topic or question, audience, kind, length/tone limits, language (default
   `language.content`) and format (`markdown` default, `marp`, `html`; the project default is
   `outputs.default_format`). Ask only for what is missing. Run `<engine> output register --kinds --json` to list
   the usable kinds; a `project` entry overrides the built-in one of the same name (R1). Read the kind's
   `template.md` and `instructions.md` from the reported `path`.
3. **Gather content with the consultation ladder (R3).** Follow the `consult-wiki` skill: `30_wiki/index.md`, pages,
   footnotes, source pages, and extractions only when a page lacks a detail you need. Open an original in
   `10_sources/` only when the operator explicitly asks. Note every wiki page you actually use; this list becomes
   `--based-on`. This is read-only: no lease, no writes to the wiki.
4. **Do not fill gaps (R3).** Write only claims that a wiki page or extraction states. Anything the wiki does not
   cover goes into a **Not covered** note to the operator (in the chat, not in the file) with the closest material
   linked and an offer to find a source (`/intake`). If the wiki covers none of the topic, say so, register
   nothing and stop (AC4). Label any inference as inference and keep it out of weight-bearing statements.
5. **Draft with citations (R4).** Follow the template structure. Every weight-bearing statement cites the wiki
   page (footnote or sources section, per the template) as a relative link. Report recorded contradictions with
   both sides and note pages that are `draft`, past `stale_after` or not human-verified. Write the draft to a
   scratch file outside the metarepository (for example the harness scratch directory), never into `outputs/`
   yet.
6. **Review (R10).** Show the operator the draft and the "Not covered" list. Iterate on request. Only after the
   operator approves, finalize.
7. **Register (R6, R7).** Run
   `<engine> output register --kind <kind> --slug <topic-slug> --format <fmt> --content-file <draft>
   --based-on <page> [--based-on <page> ...] [--title ...] [--audience ...] [--language ...]
   --assisted-by <harness/model> --json`. Add `--dry-run` first to show the file name. The engine writes
   `outputs/<yyyy-mm-dd>-<kind>-<slug>.<ext>` with `type: Output`, `generated`, `based_on` (path and sha256 of each
   page) and `wiki_index_sha256` in the frontmatter (a `.meta.yaml` sidecar for html). It never overwrites: a name
   in use becomes `-v2`, `-v3`. Never write into `outputs/` by hand.
8. **Native artifacts (R5).** When the harness offers a native artifact type for documents or slides, you MAY
   create the deliverable there instead; then still register a stub: `output register ... --format artifact
   --artifact-url <link>`. Without such a harness, use the file formats above.
9. **Report.** Give the path, the pages used, the "Not covered" items and a reminder that
   `/lint-wiki` reports `output-stale` when a `based_on` page changes (R8), at which point offer to regenerate it
   as a new revision.

## Rules

- Content comes from the wiki and its extractions only; never from memory, the web or the originals unless
  explicitly asked.
- Outputs are never fed back: do not run `/intake` or `/feed-wiki` on an output. If an approved output should
  become authoritative material, the operator drops a copy in `00_inbox/` deliberately (R9).
- Project kinds live in `outputs/_templates/<kind>/` (`template.md` and `instructions.md`); suggest them, do not
  create domain-specific ones yourself.
- Do not assume a domain, company, industry or language.
