# Triage rubric

Judge each candidate on its provisional extraction (title, metadata, headings, first pages), within
`intake.triage_budget_tokens`. All examples below are neutral placeholders.

## The five criteria

1. **Scope.** Compare with `objective`, `scope.in` and `scope.out` from `project.yaml`. Clearly inside
   `scope.in`: leaning `wiki` or `reference`. Matching `scope.out` or unrelated to the objective: `reject`.
2. **Novelty.** When the wiki is configured, read `30_wiki/index.md` and run
   `search "<topic>" --scope wiki`. Mostly new knowledge: `wiki`. Restates what the wiki already says:
   `reference` (say which page already covers it). Contradicts a page: `wiki`, and note it, because the feed
   will stop to ask about the contradiction.
3. **Authority.** Use only what the material itself says: "draft", "final", "approved", version, date, issuer.
   A draft of a document that also exists as final: `reference` or `defer`. Never infer authority from the
   file name alone.
4. **Recency and supersession.** A similar title or name to an existing source (check `registry`): propose
   `supersedes: [<id>]` when the new item replaces the old one; the old source stays where it is (sources are
   immutable). Say which is newer and on what evidence.
5. **Synthesizability.** Prose, reports, minutes, specifications, transcripts can feed the wiki. Raw
   data tables, backups, annexes, binaries, images without text are `reference`. Tools, installers, junk,
   code repositories are `reject` (suggest `move-to-shared` for code).

## Decisions

| decision | use when |
|---|---|
| `wiki` | relevant and adds knowledge worth synthesizing now |
| `reference` | relevant, but nothing to synthesize now (annex, raw data, backup, already covered) |
| `defer` | undecided; you cannot judge it with the budget. Never use `defer` to avoid a clear `reject` |
| `reject` | out of scope or not a source |

A policy-gate match caps the proposal at `reference` or `reject`.

## Reasons

One line, factual, present tense, naming the criterion that decided it:

* "Describes the onboarding process for `example-topic`; not yet in the wiki." (`wiki`)
* "Annex of `<source id>` with raw figures; no prose to synthesize." (`reference`)
* "Draft marked 'not for distribution'; a final version may follow." (`defer`)
* "Unrelated to the project objective; matches `scope.out`." (`reject`)

Do not copy long passages or quote the matched value of a policy rule.

## Confidence

* `high`: the extraction is `high` quality, the criteria agree, and no policy match.
* `medium`: one criterion is unclear, or the extraction is `partial`.
* `low`: extraction `needs-review` or `failed`, conflicting criteria, or a very short read.

Only `high` rows may skip approval under `intake.confirm: when-uncertain`. A policy match is never `high`.
