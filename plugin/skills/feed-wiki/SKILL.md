---
name: feed-wiki
description: "Fold approved sources into the project wiki from their extractions, with a compounding cascade: source page, topic pages, updates, recorded contradictions, index, log and a verification pass. The only skill that writes wiki structure. Also files answers back, records governance sign-offs and handles owner-mode proposals. Use after /intake approves sources as wiki, for queued, promoted or stale sources, or to sign off a page (/feed-wiki [source-id ...] [--batch] [--file-back] [--sign <page> --role author|reviewer|auditor] [--apply-proposal <path>])."
---

# feed-wiki

The single writer of `30_wiki/` (010/ADR-001). You do the judgment (reading, synthesis, contradictions, wording);
the engine does the mechanical work (queue, hashes, skeletons, policy gate, leases, sign-offs). Never copy
toolkit files into the project and never edit `_system/` by hand.

Engine resolution and conventions: `references/engine.md`. Craft: `references/ingest.md` (cascade, contradictions,
batch order, verification). Page format: `references/okf.md`. Roles and sign-offs: `references/govern.md`.
Owner-mode proposals: `references/proposals.md`. Speak to the operator in the language of their first message;
write wiki prose in `language.content` (`config get language.content`), with English structural labels
(log event labels, frontmatter keys).

Modes: **direct** (the operator invoked you: discuss each source first), **chained** (called by `/intake` for the
sources it just approved: the triage approval already covers the go-ahead), **batch** (`--batch`: same as chained).
In every mode stop and ask before recording a contradiction that changes an existing claim's standing or
deprecates a page.

## Workflow

1. **Preconditions (R1).** `<engine> status --json`. The wiki gate must allow feeding: `data.gates.wiki_configured`
   is true, or the wiki was scaffolded and `/init-wiki` is running its first feed (the engine answers
   `wiki_not_configured`, exit 3, otherwise: point to `/init-wiki`). Read `30_wiki/WIKI.md` (rulebook: page types,
   threshold, ingest emphasis, governance) and `config get language.content --json`.
2. **Find the work (R3).** Chained mode: exactly the source ids passed by `/intake`. Otherwise
   `<engine> registry --filter wiki_status=pending,stale --json`, or the ids the operator named. `pending` is
   triaged `wiki` with no source page; `stale` means the original or its extraction changed since the page was
   written. Nothing to do: say so and stop (R4). Sources named by the operator that are triaged `reference` or
   `defer` are allowed, but tell the operator the triage decision first.
3. **Mode check (R2, R16, R18).** Run `<engine> wiki source-page <id> --json` for the first source and read
   `data.governance`. If `can_write` is false (owner mode and the operator is not an owner), or you cannot ask
   or approve anything (headless), do not write the wiki: follow `references/proposals.md` to produce a proposal
   under `personal/<actor>/wiki-proposals/<timestamp>/` and say so. `--apply-proposal <path>` is for owners only.
4. **Lease (R2).** `<engine> lease acquire wiki --operation feed-wiki --json`; keep `data.lease.nonce` and pass
   `--lease <nonce>` to every engine write. Exit 3 with `lease_held`: tell the operator who holds it and until
   when, leave the sources queued, stop. Renew with `lease renew wiki --lease <nonce>` at half the TTL. Release it
   at the end, also on error (`lease release wiki --lease <nonce>`). Never hold `intake` and `wiki` together.
5. **Per source, in batch order** (`references/ingest.md`):
   1. `<engine> wiki source-page <id> --json`. Exit 1 `policy_blocked` (R6): do not feed this source; report the
      rule names and locations from `data.policy`, never the matched value; continue with the others.
      `data.noop` true: report "already ingested, unchanged" and move on (R4/AC3). Surface the warnings.
   2. Read the **extraction** fully (`data.extract`), never the original (R5, P7). Where it marks a figure or table
      as unclear you MAY offer to open the original page; do it only after an explicit yes (AC6).
   3. Direct mode: tell the operator what the source says, which pages you would create or update, and any
      contradictions; wait for the go-ahead (R7).
   4. Write the source page (R8): `wiki source-page <id> --write --lease <nonce> --json` creates the skeleton;
      complete it (`description`, `tags`, body: substance with claims, evidence, method, limits and specifics
      intact, plus a "Pages touched" section in `language.content`). For a `stale` source the page exists and the
      engine reports `data.refresh`: update the page body and set `content_sha256` and `extracted_at` to those values.
   5. Cascade (R9 to R11, `references/ingest.md`): create topic pages only when the `WIKI.md` threshold is met;
      update existing pages without reshaping them; attribute weight-bearing claims with footnotes; record
      contradictions under a "Disputed: <topic>" heading with both claims attributed; deprecate, never delete.
   6. Author stamp (R13): `generated: {by: human:<actor>, at, assisted_by: <harness/model>}` on every page you
      create or substantively change. `<actor>` is the claimed id from `<engine> config user --json` or
      `--actor`; never write `verified` or `audited` yourself.
6. **Index and log (R12).** Under the lease, once per pass: `index.md` gains one line per new page
   (`* [Title](relative/path.md) - <description verbatim>`) under the page's group (`references/okf.md`);
   `log.md` gains one entry per pass, newest first, event label in English (`**Creation**`, `**Update**`,
   `**Deprecation**`, `**Contradiction**`), content in `language.content`.
7. **Verification pass (R14).** Before releasing the lease re-read every weight-bearing claim you wrote against
   the extraction; remove or mark unclear anything not confirmed. Then run `<engine> lint --json` when it is
   available and fix the advisory findings your pass introduced. If `lint` answers `not_implemented`, say that
   lint did not run and re-check links and footnotes by hand (`references/okf.md`, "Checks").
8. **Close (R18).** Release the lease. Summarize per source: pages created and updated, contradictions
   (with what you asked and what was decided), skipped and why (policy, no-op, proposal). In chained mode return
   the summary to `/intake` so it can show one combined result.

## Other entry points

* **File-back (R15, `--file-back`).** The operator asks to keep an answer (usually offered by `/consult-wiki`).
  Take the lease, write the answer as a page of the type `WIKI.md` prescribes (for example `Comparison`,
  `Question`), cite the pages it synthesized as `sources[]` and links, then index, log, author stamp and
  verification as above.
* **Sign-off (R17, `--sign`).** Only when the operator explicitly states who signs in which role:
  `<engine> sign <page> --role author|reviewer|auditor --actor <id> [--stable] --json`. Relay the warnings (a
  `segregation_of_duties` warning is recorded, never blocking). Never sign on your own initiative or as a
  person you were not told about (ADR-002).

## Rules

- Wiki writes need the `wiki` lease; the engine takes or checks it. Do not write under `30_wiki/` without it.
- Re-feeding an unchanged source changes nothing. Pages are never overwritten wholesale: keep their headings and
  order, extend under them, never shrink `sources[]`, leave `verified` and `audited` untouched.
- Never open an original (`10_sources/`) unless the operator approved it for that request.
- Never invent a citation, a figure, a locator or a sign-off. What cannot be traced is marked as such.
- Do not assume a domain, company, industry or language: structure comes from `WIKI.md` and `project.yaml`.
