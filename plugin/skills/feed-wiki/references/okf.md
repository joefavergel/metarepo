# OKF working subset used by feed-wiki (009/ADR-001)

Every page of `30_wiki/` except `index.md`, `log.md` and `WIKI.md` is Markdown with YAML frontmatter.

| Key | Use |
|---|---|
| `type` | required; one of the types declared in `WIKI.md` (`Source` and `Skill` are structural) |
| `title`, `description` | `description` is reused verbatim in `index.md` |
| `sources[]` | `{id, title, path}`; `id` is the source id (`src-...`) and keys the footnotes; `path` links the source page |
| `tags` | merged, never replaced |
| `status` | `draft`, `stable`, `deprecated` |
| `stale_after` | optional date after which readers are warned |
| `generated` | `{by: human:<actor>, at, assisted_by}` set by the feed |
| `verified`, `audited` | set only by `sign` on an explicit statement from the operator |

Source pages (`sources/<source-id>.md`) add `source_id`, `resource` (relative link to the original), `extract`
(relative link to the extraction), `content_sha256`, `extracted_at`, `last_modified`. The engine writes the
skeleton (`wiki source-page`); you complete `description`, `tags` and the body.

Extra keys are preserved by the engine; unknown keys are never removed.

## Footnotes

`Claim text.[^src-0123456789ab]` in the body and `[^src-0123456789ab]: locator, if the extraction has one` at the
end. Every referenced key has a definition and a matching `sources[].id`.

## Index and log

`index.md`: one section per group (the areas, or the page types, then `Sources` and `Skills`); one entry per page
`* [Title](relative/path.md) - description`. A new page goes under its group; an existing entry is refreshed, never
duplicated.

`log.md`: grouped by `## YYYY-MM-DD`, newest first, one bullet per pass: `* **Creation** ...`, `* **Update** ...`,
`* **Deprecation** ...`, `* **Contradiction** ...`. The label is English; the sentence is in `language.content`.

## Checks (when lint is not available)

Every link in the pages you touched resolves to a file; every footnote key is defined and in `sources[]`; every new
page is linked from `index.md` and from another page; `type` is declared in `WIKI.md`.
