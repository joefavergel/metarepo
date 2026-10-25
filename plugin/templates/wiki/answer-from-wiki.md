---
type: Skill
title: Answer from the wiki
description: How any agent with file access answers a question from this wiki without writing to it.
status: stable
---
# Answer from the wiki

Use this procedure to answer a question from the project wiki. It is written for any agent that can read
files; it needs no special tooling.

## Rules

1. **Read-only.** Answering never writes: do not edit pages, `index.md`, `log.md`, or anything else.
   If an answer deserves to be kept, say so and let a wiki writer file it.
2. **Honesty.** Answer only from what the pages say. If the wiki does not cover the question, say so; do
   not fill the gap from memory and present it as the project's knowledge. Separate what the wiki states from
   your own inference, and flag contradictions between pages instead of choosing silently.
3. **Cite.** Name every page you used as a relative Markdown link, and say how trustworthy it is.

## The ladder: stop at the first level that answers the question

1. `index.md`: find candidate pages by title and description.
2. The pages themselves. Read only the sections that matter.
3. The footnotes of the claims you use: each key matches a `sources[].id` in the page frontmatter.
4. The source page in `sources/` for that id: it names the extraction and the original.
5. The extraction of that source in `20_extracts/` (one Markdown file per source). Search before reading;
   read only the relevant sections.
6. The original in `10_sources/`: **only on the user's explicit request.** Never open binaries on your own.

## Trust and freshness

Report, for each page you rely on:

- **Sign-off:** `audited` if the frontmatter has `audited`; `verified` if it has `verified` (a `human:`
  actor means human-reviewed); otherwise `unreviewed`.
- **Status:** `draft` and `deprecated` pages are weaker evidence; prefer `stable` pages and mention the
  replacement when a page is deprecated.
- **Freshness:** a page is stale when `stale_after` has passed. Say so.

## Citation format

> The export runs nightly ([Example concept](area/example-concept.md), verified).

Quote sparingly. Give the link, then the trust note.
