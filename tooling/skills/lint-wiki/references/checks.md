# Lint check catalog

`<engine> lint --json` runs every check below. Severities are the defaults; a project can change them (see
"Overrides"). Only `block` makes the exit status `1`.

| Check | Default | Finding | Fixed by |
|---|---|---|---|
| `conformance` | error | Wiki page without parseable frontmatter or `type`, an invalid `status` or malformed `sources[]`; an extraction that is unreadable, not `type: Extract`, lacks `source_id`/`source_path`/`source_sha256` or fails the extraction schema | wiki page: `/feed-wiki`; extraction: `extract --force <path>` |
| `policy-violation` | **block** | A wiki page, or the extraction (and continuation parts) of a source triaged `wiki`, matches `policy.block_patterns` / `policy.block_terms`. The value is masked; the rule name and location are given | wiki page: `/feed-wiki` (rewrite); extraction: `/intake --retriage` and do not feed |
| `source-unregistered` | error | File in `10_sources/` with no extraction recording it | `/intake` or `extract` |
| `extract-orphan` | warn | Extraction whose `source_path` does not exist | restore the source, or `remove <extraction> --confirm <sha256>` |
| `extract-hash-mismatch` | error | `source_sha256` differs from the current hash of the source (tampering; sources are immutable) | restore the original; `extract --force` only if a human decides the new file is the source |
| `extract-failed` | warn | `quality: failed` | `extract --force <path>` |
| `extract-needs-review` | info | `quality: needs-review` | review by hand |
| `triage-missing` | warn | Extraction without `triage.decision` | `/intake --retriage` |
| `not-fed` | info | Triaged `wiki` and no source page yet (pending work) | `/feed-wiki` |
| `source-page-stale` | warn | Source page `content_sha256` differs from the extraction's `source_sha256`, or the extraction was regenerated after the page | `/feed-wiki` (stale source) |
| `source-page-dangling` | error | Source page whose `source_id` matches no extraction, or whose `extract` / `resource` link does not resolve | `/feed-wiki` |
| `drift` | warn | Non-structural page whose provenance never bottoms out in a source page: no `sources[]` id is a source page, and none of its `sources[].path` links leads (recursively, e.g. a file-back page citing wiki pages) to a page that does | `/feed-wiki` |
| `no-sources` | warn | Non-structural page with empty `sources[]` | `/feed-wiki` |
| `dangling-footnote` | warn | `[^id]` in the body with no matching `sources[].id` | `/feed-wiki` |
| `unused-source` | info | `sources[]` entry never cited by a footnote (source pages are exempt: their own entry is the page itself) | `/feed-wiki` |
| `orphan` | warn | Page nothing links to (body links and `sources[].path` of other pages count; `Skill` pages are exempt) | `/feed-wiki` |
| `broken-link` | error | Relative link to a missing file (`index.md` and `log.md` are reported as `index-drift` / `log-drift`) | `/feed-wiki` |
| `index-drift` | warn | A page missing from `index.md`; an entry pointing nowhere; an entry whose description differs from the page's `description`; an entry under the wrong group; `index.md` missing | `/feed-wiki` |
| `log-drift` | info | `log.md` date headings not `YYYY-MM-DD`, duplicated or not newest first; an entry linking to a missing file | `/feed-wiki` |
| `no-description` | warn | Page without `description` | `/feed-wiki` |
| `expired` | info | `stale_after` has passed | `/feed-wiki` to refresh, or set a new date |
| `sign-coverage` | info | Team profile only: no `verified` (reviewer) sign-off, or no `audited` (auditor) one when `audit_expected` is set in `WIKI.md` or `project.yaml` (`Source`, `Skill` and deprecated pages are exempt) | `sign <page> --role ...` when a person has reviewed it |
| `duty-overlap` | info | The same actor holds more than one of `generated`, `verified`, `audited` on a page | a different person signs one role |
| `output-stale` | info | An output (`type: Output`, in its frontmatter or in the `<stem>.meta.yaml` sidecar of a non-Markdown file) whose `based_on[]` page (root-relative `path` + `sha256`, spec 014/R7) changed or disappeared since it was generated; reported on the output file | `/make-output` |

`log-drift` is not in the original catalog of spec 012: `/feed-wiki` edits `index.md` and `log.md` as skill-level
steps (010), so lint needs a way to see when the log disagrees with the bundle. `index-drift` covers the index the
same way.

## Where a finding is configured

A finding located under `30_wiki/` follows `lint.severity` in `30_wiki/WIKI.md`'s configuration block; every other
finding (sources, extractions, outputs) follows `lint.severity` in `project.yaml`.

```yaml
# project.yaml
lint:
  severity: {extract-failed: error, not-fed: warn}
  allow_policy_downgrade: false
```

```yaml
# 30_wiki/WIKI.md configuration block
lint:
  severity: {no-description: error, expired: warn}
```

Values: `block`, `error`, `warn`, `info`. An unknown check id or a bad value is ignored and reported in `warnings[]`.
`policy-violation` keeps `block` unless `project.yaml` sets `lint.allow_policy_downgrade: true`.

## Output shape

```
data.summary    {block, error, warn, info, total}
data.findings[] {check, severity, where, detail, fix}   sorted block, error, warn, info
data.checks_run the ids that ran (`--only` narrows them)
data.scanned    {pages, extractions}
```

`lint` records `state.json -> last_lint` (`at`, `toolkit`, `scope: full|partial`, `counts`) under the `config`
lease; when the lease is busy it skips the record and says so in `warnings[]`.
