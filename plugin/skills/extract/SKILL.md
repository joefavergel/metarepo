---
name: extract
description: "Internal step of intake: produce the Markdown extraction of a source, using the engine's adapters first and, when none fits or quality is poor, extracting images, scans and short audio yourself and recording the result with the engine. Not a user command."
---

# extract

Every source in `10_sources/` has exactly one Markdown extraction in `20_extracts/`. Agents read the
extraction, not the original (P7). The engine does the mechanical work; you do judgment only where no
extractor can.

Engine resolution rules: see `references/engine.md`. Always call the engine with `--json`.

## 1. Run the engine first

```
<engine> extractors --json                      # what is available (never install anything yourself)
<engine> extract <source-path-or-id> --json     # pick the adapter, extract, score quality, write
```

Read `data.quality` and `data.agent_assist.suggested`:

| quality | meaning | next step |
|---|---|---|
| `high` | measured signals look good | done |
| `partial` | some pages empty, low OCR confidence, partial transcript | done; mention it in the intake summary |
| `needs-review` | little or no text (scan, image-only) | agent-assisted extraction if `agent_assist.suggested` |
| `failed` | no adapter, or the adapter crashed (reason in `quality_notes`) | agent-assisted extraction if possible; else leave the record and report it |

`extract` without `--force` never overwrites an existing extraction. A `stale_extraction` warning means the
source hash differs or the extractor is older than the configured minimum: report it, and use
`--force` only when the operator agrees. A cloud adapter is never used unless the project enabled
`extraction.allow_cloud`; do not try to bypass that.

## 2. Agent-assisted extraction (only when step 1 cannot do it)

Use it for images, scanned pages and short audio you can transcribe through a connected tool. It is the
only case where you open the original (P7, R8). Anything you cannot read faithfully (unknown binary,
very long media) stays `failed`; say so instead of guessing.

1. Work in chunks: one page, one image or one few-minute audio segment at a time. Write each chunk to
   a temp file in your scratch directory, never inside the metarepo.
2. Transcribe, do not summarize. Copy visible text verbatim, in reading order, in its own language.
3. Use the body conventions: `## Page N` per page (or `## Slide N`), tables as Markdown tables, audio as
   `[hh:mm:ss] text` lines with speaker labels when known, images as a short description followed by the
   visible text.
4. Describe figures neutrally and factually, as `> [Figure: what is shown]`. Do not interpret, evaluate or
   add knowledge that is not on the page.
5. Mark uncertainty inline instead of resolving it: `[unclear]`, `[illegible]`, or `[?: best reading]`.
   Never invent a word, a number or a name to fill a gap.
6. Join the chunks into one body file, then record it:

```
<engine> extract-record <source-path-or-id> --body <body.md> --extractor agent:<your model id> \
    --quality <partial|needs-review|high> --quality-notes "<what you could not read>" \
    --structure '{"pages": N}' --json
```

State your own confidence in `--quality`: use `partial` when anything was unclear, `needs-review` when
you doubt the result, and `high` only for clean, fully legible content. `extract-record` records
`mode: agent`, keeps triage and lineage, and refuses to replace a `high` or `partial` extraction unless the
operator approves `--force`.

7. Say in the intake summary which sources were extracted by you (mode `agent`), with their quality.

## 3. Rules

* Call the engine for hashing, ids, placement, scoring and writing. Never write `20_extracts/` by hand.
* Never send content to a cloud service on your own; only engine adapters allowed by the project may.
* Large spreadsheets are summarized by the engine (schema, statistics, first and last rows); the full data
  is in the original and is opened only on explicit request.

## Deferred decisions

Not decided by the specs, so not done here: keyframe descriptions for video (audio transcript only for
now), and any OCR fallback recipe. If the project configured an `extraction.commands` entry for these,
the engine uses it like any other adapter.
