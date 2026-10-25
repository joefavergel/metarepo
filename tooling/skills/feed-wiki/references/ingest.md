# Ingest craft: the compounding cascade

The wiki gets better with each source because every source is folded into what is already there. A feed that only
adds a page is a failed feed.

## Batch ordering

1. Read all queued extractions within the token budget (frontmatter and headings first; read in full the ones
   you will write from). Group sources that touch the same pages.
2. Write source pages (engine skeleton first), then entity and concept pages, then cascade updates to existing
   pages, then `index.md` and `log.md`, then the verification pass, then lint.
3. Foundational sources (those that define the project's vocabulary) first, so later sources link to pages that
   already exist.
4. A source that fails the policy gate is skipped and reported; the rest of the batch continues.

## What goes in a source page

Substance, not a summary of the summary: claims, evidence, method, limits and the specifics (numbers, dates,
names, conditions) kept intact. End with "Pages touched" listing every page you created or updated for this
source, as relative links.

## When to create a page

Only when the threshold in `WIKI.md` ("What earns a page") is met. A topic that recurs across sources, or that a
single source treats at length, earns one; a passing mention does not. A new page needs `type` (declared in
`WIKI.md`), `title`, `description`, `sources[]`, the footnotes, `generated`, and it must be linked from `index.md`
and from at least one existing page in the same pass. Prefer extending an existing page over creating a near
duplicate: search first (`<engine> search "<term>" --scope wiki --json`).

## Updating an existing page

Keep its headings and their order. Extend under the right heading; add a new heading only for a genuinely new
aspect. Append the new source to `sources[]` (never remove entries), merge `tags`, refresh `generated`, and leave
`verified` and `audited` untouched (a human attests those, `references/govern.md`). A changed page that carries
`verified` is now partly unreviewed; say so in the summary.

## Attribution

Every weight-bearing claim (numbers, dates, quotes, causal assertions, decisions, owners) carries a footnote
`[^<source-id>]` whose definition names the locator from the extraction when it has one (page, timestamp, sheet):
`[^src-0123456789ab]: p. 12, table 2`. The id must be in the page's `sources[]`. A claim without a source is
written as an explicit open question, not as fact.

## Contradictions

When a new source disagrees with a claim already in the wiki:

1. Do not overwrite the older claim and do not merge two figures into a third.
2. Under the affected page's "Disputed: <topic>" heading (localized to `language.content`; create it if absent),
   write both claims, each attributed with its footnote, and one sentence on which is better supported and why
   (date, method, directness, authority). If you cannot judge, say that.
3. Stop and ask the operator before the change if it alters an existing claim's standing (the old claim would now
   read as doubtful) or if a page becomes obsolete.
4. A source that supersedes another (the extraction frontmatter lists `supersedes`) deprecates the old page when
   the old page's whole subject is replaced: `status: deprecated`, a forward link to the successor in the first
   paragraph, the successor links back. Never delete a page.

## Stale sources

The original changed or the extraction was regenerated. Re-read the new extraction, diff it mentally against the
source page, update the claims that changed (record contradictions with the earlier version as above when a
figure changed), then set `content_sha256` and `extracted_at` on the source page from `data.refresh`.

## Verification pass

Before releasing the lease, for each weight-bearing claim you wrote: find it again in the extraction. Confirmed:
keep. Not found or different: fix it, or mark it unclear, or remove it. Then `lint`. Report what you changed in
the pass.
