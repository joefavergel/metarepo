---
name: intake
description: "Process the inbox into registered sources: de-duplicate, unpack containers, apply policy gates, read cheaply, propose a triage decision per item (wiki, reference, defer, reject), place approved items with their extractions, then hand the wiki items to feed-wiki in the same run. Use when material was dropped into 00_inbox/, or with --retriage <id> to change a placed source's decision."
---

# intake

Turns whatever members dropped into `00_inbox/` into registered sources: original in `10_sources/`,
Markdown extraction in `20_extracts/`, triage decision in the extraction frontmatter. It never writes the
wiki itself; when placement ends it hands over to `feed-wiki` for the items approved as `wiki`.

Engine resolution rules: see `references/engine.md`. Always call the engine with `--json` and read
`ok`, `data`, `errors`. Related references: `references/triage-rubric.md` (how to judge and phrase
reasons), `references/proposal-format.md` (table and approval prompts). Containers are handled by the
internal `unbundle` skill.

You do the judgment (triage, one-line reasons, category proposals). The engine does everything mechanical
(hashing, ids, placement, naming, depth, lease checks, extraction, logging). Never move, rename, copy or
delete files in the metarepo yourself and never edit frontmatter by hand.

Flags: `--no-feed` (do not chain into feed-wiki), `--retriage <source-id>` (see the last section).
Settings come from `project.yaml` (`intake.*`, `policy.*`, `objective`, `scope.in`, `scope.out`); read them with
`<engine> config get <key> --json`.

## 0. Preconditions

1. `<engine> status --json`: stop if it is not a metarepo (exit 3) or the toolkit is outside the project's
   range (exit 4); the message says what to do.
2. Decide whether you can ask questions (interactive) or not (headless: no way to ask the operator).
   Headless runs never place anything (P5): they stop after step 6 and save the proposal (step 7b).

## 1. Lease (R1)

```
<engine> lease acquire intake --operation intake --json
```

Keep `data.lease.nonce`; pass it as `--lease <nonce>` to every `place` call. If the exit code is 3 with
`lease_held`, tell the operator who holds it and until when (`errors[0].message`) and stop; do not retry in
a loop. Renew with `lease renew intake --lease <nonce>` when the run is long (at half the TTL). **Release the
lease at the end and on every abort** (`lease release intake --lease <nonce>`), including when the
operator cancels.

## 2. Scan (R2, R3, R4)

```
<engine> inbox scan --provisional <staging> --json
```

`<staging>` is a new local temp directory **outside** the metarepo (for example from `mktemp -d`; on Windows
a folder under `%TEMP%`). The engine refuses staging inside the tree. The output lists every item with
`sha256`, `id`, `media_type`, `size`, `duplicate_of`, `duplicate_in_inbox_of`, `bundle`, `cloud_native`,
`not_local`, and a `junk` list. Handle by category:

* **junk** (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `~$*`, `__MACOSX/`): summarize, never triage. Remove with
  `place <path> --discard --lease <nonce>` once the operator approved the summary.
* **duplicate** (`duplicate_of`): report it. On confirmation:
  `place <path> --duplicate-of <id> --lease <nonce>`; the engine appends the file's name to the existing
  extraction's `aliases` when it differs and removes the inbox copy. A duplicate is never placed twice.
  `duplicate_in_inbox_of`: keep one, handle the other the same way after it is placed.
* **cloud_native** (`.gdoc`, `.gsheet`, ...): never placed. Ask the operator to drop an exported copy
  (PDF, DOCX or XLSX) and leave the pointer file in the inbox.
* **not_local** (online-only sync placeholder): do not read it. Tell the operator to make it available
  offline, wait, and rerun; leave it in the inbox.
* **bundle**: go to step 3.
* everything else: a candidate with a provisional extraction in `item.provisional`.

## 3. Containers (R5, R5a, R6): the `unbundle` skill

For each item with `bundle: true` (archive or folder) follow the `unbundle` skill. It returns a manifest
whose members are ordinary candidates (each with `staged` path, `member_path`, `duplicate_of`, `hint`,
`provisional`). If a bundle is refused (zip-slip, bomb, encrypted, ...), report the exact reason code and
message, leave the container in the inbox and continue with the rest.

## 4. Light read (R7)

For each candidate, read **at most `intake.triage_budget_tokens`** (default 3000, roughly 4 characters per
token) of its provisional extraction file (`provisional.path`): title, metadata, headings and the first
pages or minutes. Never open the original unless the extraction is `failed` or `needs-review` and the file
is an image, a scan or short audio; then use the `extract` skill's agent path only after placement, not here:
triage such items on name, media type and size.

## 5. Policy gate (R9)

`provisional.policy.blocked` is true when `policy.block_patterns` or `policy.block_terms` matched. Then:

* the proposal can only be `reference` or `reject`, never `wiki` (the engine enforces this too);
* show the rule name and location only (`rule`, `location`); **never repeat or reconstruct the matched
  value**;
* placing it at all needs an explicit extra confirmation from the operator; pass `--confirm-policy` only
  after that confirmation.

## 6. Proposal (R8, R10, R11)

Evaluate each candidate with `references/triage-rubric.md` against `objective`, `scope.in`, `scope.out`,
novelty vs the wiki (`30_wiki/index.md` plus `<engine> search "<title or topic>" --scope wiki --json`, only
when the wiki is configured), authority (official vs draft, as the material states), recency and
supersession (similar title to an existing source: propose `supersedes`), and synthesizability. Decisions:

| decision | placed | fed to the wiki |
|---|---|---|
| `wiki` | yes | yes (queued, then step 10) |
| `reference` | yes | no, searchable through the extraction |
| `defer` | yes | no, revisit with `--retriage` |
| `reject` | per `intake.on_reject` | no |

Present one table (columns in `references/proposal-format.md`): original name, decision, one-line reason,
proposed category and placed name, `supersedes`, bundle parent, affected wiki pages (when the wiki exists).
Junk and duplicates are summarized separately. Propose categories from `intake.categories` when set, else
from the folder or bundle name; categories are kebab-case.

Approval:

* `intake.confirm: always`: every row needs approval; offer Approve all / Review one by one / Cancel.
* `intake.confirm: when-uncertain`: rows you mark `confidence: high` proceed; ask only about the rest.
* **Headless:** write the proposal JSON to a scratch file and run
  `<engine> place --proposal <file> --json`. It is saved under `personal/<actor>/intake-proposals/<timestamp>.json`
  for a later session. Place nothing, release the lease, and say where the proposal is. (A staging manifest
  from `unbundle` stays in staging; nothing else persists.)

## 7. Placement (R12, R13, R14)

For each approved `wiki`/`reference`/`defer` row, in this order (containers last):

```
<engine> place <staged-or-inbox-path> --category <cat> [--name <placed-name>] --lease <nonce> \
  --triage '{"decision":"wiki","reason":"<one line>","confidence":"high|medium|low"}' \
  [--supersedes <id>]... [--parent <container-id> --member-path <p> --group <slug> --bundle-slug <slug>] \
  [--confirm-policy] --json
```

`place` computes the id, applies naming (ASCII kebab-case, 80-character stems, 200-character paths,
`intake.max_source_depth` flattening), moves the original, writes the triage data and runs the full
extraction (008). Read the result: `status` (`placed`, `duplicate`, `planned`), `quality`, `warnings`. If an
error says `path_too_long`, choose a shorter `--name` or category. If the extraction quality is
`needs-review` or `failed` and `agent_assist` applies, follow the `extract` skill for that source.

* **Bundle members** carry `--parent` (the container's `id` from the manifest, when the container is
  archived), `--member-path` and `--bundle-slug`; folder bundles also carry `--group <slug>` and no
  `--parent` (no container file exists).
* **Archived containers** (`intake.archive_bundles: true`, archives only): after all members are placed,
  write a JSON list of `{id, member_path, decision}` (every member, including rejected ones) and run
  `place <archive> --container --members <file> --lease <nonce>`; it goes to `10_sources/_bundles/` with its
  own extraction listing the members. With `archive_bundles: false`, remove the archive after its members
  are placed: `place <archive> --discard --lease <nonce>`.
* **Rejects** follow `intake.on_reject`: `place <path> --reject --lease <nonce>` (add `--bundle-slug` and
  `--member-path` for a staged member). `leave-in-inbox` leaves the file untouched and lists it in the
  summary; `move-to-shared` moves it to `shared/_from-inbox/`; `delete` needs the operator's explicit
  confirmation and `--confirm <sha256>`.
* **Code folders** (`hint: code` in a manifest): propose `reject` and suggest `move-to-shared`; the engine
  already excluded `.git/`, dependency folders and secret files from staging.

After the last member of a **folder bundle** is placed (and nothing was left in the inbox on purpose),
remove the emptied tree: `place 00_inbox/<folder> --discard --lease <nonce>`. The engine refuses when it
would delete content that is not a registered source (`not_captured`); only add `--include-unplaced` after the
operator confirmed deleting rejected files. If any member was rejected with `leave-in-inbox`, do not
discard the folder.

## 8. Inbox check (R15)

`<engine> inbox scan --json` again. The inbox must contain only items left on purpose: rejected-left,
deferred by the operator before a decision, skipped, refused bundles, cloud-native pointers, not-local
files. List each with the reason. Delete the staging directory (it is a local temp folder, not part of the
metarepo).

## 9. Summary (R16)

Show counts per decision, the `wiki` items about to be fed, quality warnings and anything left in the inbox.
Log the event: `<engine> place --completed '{"wiki":N,"reference":N,"defer":N,"reject":N,"duplicates":N,"left":N}' --json`.

## 10. Chain into feed-wiki (R18, R19)

Release the `intake` lease **first** (never hold `intake` and `wiki` together).

Run the chain WHEN `intake.auto_feed` is true (the default), at least one item was placed with
`triage.decision: wiki`, and none of the stop conditions below apply. Invoke the `feed-wiki` skill
restricted to the source ids of those items (sibling-skill invocation; if the runtime cannot invoke a
sibling skill, continue by following the installed `feed-wiki` skill in this same session). Approval of a
`wiki` row is the approval to feed that source: do not ask again per source. The feed itself still stops for
decisions that change existing knowledge (contradictions, deprecations).

Stop after placement instead, leaving the items queued (`wiki_status: pending`), and say why and what to do
next, WHEN:

* the wiki is not configured: suggest `/init-wiki`, which will feed them as its first pass;
* the `wiki` lease is held by another session: say who and until when; the items stay queued;
* owner mode is on and the operator is not a wiki owner: offer the proposal path (`feed-wiki` explains it);
* the operator passed `--no-feed`, or `intake.auto_feed` is false;
* the run is headless.

A failed or impossible feed never undoes a correct placement: placed sources stay registered and queued.

## Re-triage (R17)

`/intake --retriage <source-id>`: take the lease, show the current decision (`registry --filter id=<id>`),
agree the new decision and a reason with the operator, then:

```
<engine> place <source-id> --retriage --lease <nonce> \
  --triage '{"decision":"wiki","reason":"<why>","confidence":"high"}' --json
```

It only updates the extraction frontmatter; the original never moves. A source cannot be re-triaged to
`reject`, and the policy gate blocks `wiki` for matched material. Release the lease, then apply step 10 for a
change to `wiki`.
