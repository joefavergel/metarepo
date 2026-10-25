# Owner-mode proposals (010/R16, R18)

Used when `governance.wiki_write_mode` is `owner` and the operator is not a wiki owner
(`wiki source-page` reports `data.governance.can_write: false`), and when a run is unattended (no one to discuss
or approve), in which case the same bundle is produced for any mode (P5). Nothing under `30_wiki/` is written.

## Writing a proposal

Folder: `personal/<actor>/wiki-proposals/<UTC timestamp, YYYYMMDDTHHMMSSZ>/`. Contents:

* `proposal.md`: rationale in `language.content`: the sources covered (ids), what each says in two or three
  sentences, the pages to create or update, contradictions found with both attributions, and the questions an owner
  must decide.
* `pages/<path under 30_wiki/>`: the full planned text of every page to create, and the full replacement text of
  every page to update (the source pages included, produced from `wiki source-page <id> --json` frontmatter).
* `index.md.add` and `log.md.add`: the lines to add, ready to paste.

Read-only engine calls (`wiki source-page` without `--write`) are fine; do not use `--write`.

Tell the operator where the bundle is and who the owners are (`config get governance.wiki_owners --json`).

## Applying a proposal (owners)

`/feed-wiki --apply-proposal <path>`: take the `wiki` lease, read `proposal.md`, then for each planned page compare
the target with the current page. A page that changed since the proposal was written is merged by hand with the
operator, not overwritten. Create the source pages with `wiki source-page <id> --write`, write the other pages, add
the index and log lines, run the verification pass and lint, release the lease, and leave the proposal folder in
place (the owner decides when to remove it, with `remove <path> --confirm <sha256>`).
