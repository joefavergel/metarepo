---
name: consult-wiki
description: "Answer a question from the project wiki, read-only: walk index, pages, footnotes, source pages and extractions only as far as the question needs, cite pages as links, say how trustworthy and fresh they are, and say so when the wiki does not know. Never writes. Use when the operator asks what the project knows about something (/consult-wiki <question>)."
---

# consult-wiki

Answer from the wiki by traversal, never by re-deriving from originals. This skill is **read-only**: it creates,
edits, moves and deletes nothing in the metarepository, takes no lease, and does not update `log.md`. The engine
does the searching; you do the reading and the judgment.

Engine resolution and conventions: `references/engine.md`. Ladder detail, escalation triggers, honesty rules and
the file-back wording: `references/query.md`. Answer in the language of the operator's question.

## Workflow

1. **Gate (R1).** Run `<engine> status --json`. If `data.gates.wiki_configured` is false, stop and point to
   `/init-wiki`. Do not read around the gate.
2. **Code question? (R9).** If the question is about the project's code, run `<engine> repos path <name> --json`,
   point the operator to that repository and answer there. Code documentation is not wiki content.
3. **Enter at `30_wiki/index.md` (R2).** Pick candidate pages by title and description; descend into area or type
   indexes when the index links to them. When the index is too large to scan, run
   `<engine> search <terms> --scope wiki --json` and read the best hits.
4. **Read pages, then stop if they answer (R3).** Read only the sections that matter and follow links only as far
   as the question requires. A question answered by one page cites that page and opens nothing deeper (AC1).
5. **Escalate only for what the wiki lacks (R3).** Footnote `[^id]` to its `sources[].id` in the page frontmatter,
   then to the source page in `sources/`, then to the extraction. For detail the wiki lacks, a figure to verify, or
   a literal question (exact phrase, a number mentioned once) run
   `<engine> search <terms> --scope extracts --json` and read **only the matching section** (each hit gives
   `path`, `section` and `line`). Answer from it, citing the source page and the locator (AC2).
6. **Originals (R4).** Never open a file in `10_sources/` on your own. If the extraction is not enough, say so and
   offer to open the original; open it only after the operator explicitly asks.
7. **Not covered (R7).** If neither the wiki nor the extractions answer, say "not covered", name the closest
   material (links), and offer to find a source: suggest dropping it in `00_inbox/` and running `/intake`. Do not
   assemble a fluent answer from adjacent pages (AC3).
8. **Answer (R5, R6).** Cite every page used as a relative link; for extraction use cite the source page and the
   locator (page, timestamp or sheet). Add a trust note when the reader will act on the answer: `status: draft`,
   past `stale_after`, no human `verified`, and sources reported by
   `<engine> registry --filter wiki_status=stale --json`. Report a recorded contradiction with both sides,
   attributed (AC4). See `references/query.md` for the exact checks.
9. **File-back offer (R8).** If the answer is substantial and could recur (comparison, analysis, cross-page
   synthesis), offer in one line to file it via `/feed-wiki`. After two declines in a session, stop offering.
10. **Keep the walk recoverable (R11).** Note, as you go, the pages traversed, the source leaves reached and the
    locators used; you can list them if asked. Emitting a structured trace is out of scope for v0.1.

## Rules

- Read-only, always: no file creation (not even notes or scratch files in the metarepository), no lease, no
  `--fix`, no `feed`, no sign-off. If something is wrong in the wiki, tell the operator and point to `/feed-wiki`
  or `/lint-wiki`.
- Honesty over fluency: separate what pages state from your own inference, and label the inference.
- Search before reading; read sections, not whole extractions.
- Do not assume a domain, company, industry or language.
