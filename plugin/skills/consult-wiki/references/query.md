# Consulting the wiki

## The ladder

```
index.md -> pages (follow links) -> footnotes [^id] -> source pages -> extraction (search, then sections) -> original (explicit request only)
```

Stop at the first rung that answers the question. Each rung costs more and is weaker evidence of what the
project currently believes, except the extraction, which is stronger evidence of what a source literally says.

## When to escalate

| Situation | Go to |
|---|---|
| The page answers it | Stop. Cite the page. |
| The page states a figure, date or quote you are asked to verify | Footnote -> source page -> extraction section. |
| The question is literal (exact phrase, a number mentioned once, "where does it say...") | `search --scope extracts`, read the matching section. |
| The wiki lacks the detail but a page links the topic | `search --scope extracts` with the page's vocabulary. |
| The extraction is not enough | Say so; offer the original. Open it only on explicit request. |
| Nothing covers it | "Not covered" (see below). |

Search hints: `search <terms> --scope wiki|extracts --json` returns `path`, `title`, `type`, `score`, `snippet`,
`section` and `line`. Use the section heading as the locator when it is a page, timestamp or sheet marker; otherwise
cite the heading. Try two or three different phrasings before declaring something not covered.

## Trust and freshness checks (report when the reader will act on the answer)

- `status: draft` or `deprecated` (name the replacement if there is one).
- `stale_after` earlier than today.
- No `verified` in the frontmatter, or `verified` not by a human actor: say "unreviewed" / "not human-verified".
- A recorded contradiction (an Open questions / Contradictions section, or conflicting claims between pages):
  give both sides, attributed to their pages and sources; do not pick one.
- Source pages whose source is stale: `registry --filter wiki_status=stale --json` lists them; compare with the
  source pages you used.

## Not covered

Say it plainly: "The wiki does not cover this." Then name the closest material as links, say what they do not
answer, and offer to look for a source: drop it in `00_inbox/` and run `/intake`. Never stitch adjacent pages
into a plausible answer.

## Citation format

Pages: `([Example concept](area/example-concept.md), verified)`. Extractions: cite the source page and the
locator: `([Example source](sources/example-source.md), page 3)`. Keep quotes short.

## File-back offer

Wording, one line: "This comparison could be reused. Want me to file it in the wiki via /feed-wiki?" Count
declines per session; after the second, stop offering. Never file it yourself.

## What a consultation must not do

Write, create or touch any file in the metarepository, take a lease, open an original unasked, or answer from
memory about the project.
