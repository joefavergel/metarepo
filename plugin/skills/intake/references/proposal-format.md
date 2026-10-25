# Proposal format

## Table (one row per candidate)

| # | Original name | Decision | Reason (one line) | Category / placed name | Supersedes | Bundle | Wiki pages likely affected |
|---|---|---|---|---|---|---|---|

* `Decision`: `wiki`, `reference`, `defer` or `reject`; add a flag when the policy gate matched
  ("policy: rule-name at line N", never the value).
* `Category / placed name`: `category/placed-name.ext` as `place` will name it (ASCII kebab-case). For
  rejects: the `on_reject` action ("left in inbox", "move to shared", "delete").
* `Supersedes`: source ids, or empty.
* `Bundle`: container name and `member_path`, or the folder group.
* `Wiki pages likely affected`: only for `wiki` rows and only when the wiki exists.
* Show `confidence` in a trailing column when `intake.confirm` is `when-uncertain`.

Below the table, in separate short lists: **Junk** (count and names), **Duplicates** (name, existing source,
alias that would be added), **Not placed** (cloud-native pointers, not-local files, refused bundles with the
reason code), **Code folders** (suggested `move-to-shared`).

## Approval prompts

When `AskUserQuestion` is available use it, plain questions otherwise.

1. **Approve all** (apply the table as shown), **Review one by one**, **Cancel** (release the lease, place
   nothing).
2. In review: for each row offer the proposed decision first, then the other three decisions, then "edit
   category/name". Changing a decision to or from `wiki` changes what step 10 will feed.
3. Policy matches: a separate question per file, "Place this file as `reference`?" with the rule names; the
   answer is the explicit confirmation required by the policy gate.
4. Duplicates and junk: one question each, "Remove N junk files?" and "Merge N duplicates?".
5. Deletions (`delete` mode, `--include-unplaced`): list the files and ask explicitly; default is not to delete.

## Headless proposal file

JSON written to a scratch file and saved with `place --proposal <file>`:

```json
{
  "created_at": "<ISO timestamp>",
  "rows": [{"name": "...", "path": "00_inbox/...", "id": "src-...", "decision": "wiki",
            "reason": "...", "confidence": "high", "category": "...", "placed_name": "...",
            "supersedes": [], "bundle": null, "policy": null}],
  "junk": [], "duplicates": [], "not_placed": []
}
```
