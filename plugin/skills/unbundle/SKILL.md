---
name: unbundle
description: "Internal step of intake: validate and expand a container (archive or folder dropped into the inbox) into local staging and present its members as triage candidates. Not a user command."
---

# unbundle

Called by `intake` for every inbox item with `bundle: true`: ZIP, TAR/TAR.GZ, 7z/RAR (only when the engine
detected a tool) and folders. A folder is an uncompressed bundle. Nothing in a container is trusted.

Engine resolution rules: see `references/engine.md`. Always pass `--json`.

## 1. Expand

```
<engine> unbundle <00_inbox/path> --staging <staging> --provisional --json
```

`<staging>` is the local temp directory chosen by `intake`, outside the metarepo. The engine validates
**before writing anything**, then expands into its own sub-directory `<staging>/<container-slug>-<hash6>/files/` (`data.staging`) and saves
`manifest.json` there, so many bundles can share one `<staging>` in a run without overwriting each other.
Use `--dry-run` to validate only.

A violation stops that bundle (exit 3). Report the code and the engine's message, leave the container in the
inbox, and carry on with other items:

| code | meaning |
|---|---|
| `bundle_path_traversal` | `..`, absolute path or drive letter (zip-slip) |
| `bundle_invalid_name` | Windows reserved or invalid name; ask for a renamed copy |
| `bundle_symlink`, `bundle_special_file` | links, devices, sockets |
| `bundle_encrypted` | password-protected: ask the operator for an unencrypted copy, or skip |
| `bundle_too_large`, `bundle_ratio` | past `intake.bundle_max_uncompressed_mb` / `bundle_max_ratio` |
| `bundle_depth` | nesting deeper than `intake.bundle_max_depth` |
| `bundle_name_collision` | two entries differ only by case |
| `bundle_unsupported` | 7z/RAR without a tool, or not a container; say which optional tool to install, never install it |
| `bundle_corrupt` | unreadable archive |

Email containers (`.eml`, `.msg`) are an open decision in the spec and are **not** unpacked: they stay
ordinary files for triage. Do not invent a convention for their attachments.

## 2. Read the manifest

* `members[]`: `member_path` (relative path inside the container), `staged` (file to pass to `place`),
  `id`, `sha256`, `media_type`, `duplicate_of`, `cloud_native`, `bundle`/`expanded`, `hint`,
  `provisional` (quality and policy verdict).
* Nested archives are expanded up to `intake.bundle_max_depth`; their members appear under a folder named
  after the nested archive. A nested archive beyond the limit stays a member with `reason`; triage it as a
  single file.
* `junk[]`: dropped, never triaged. `excluded[]`: forbidden paths (`.git/`, dependency folders, secret files)
  that were not written to staging; mention them in the summary without their contents.
* `code_folders[]` and members with `hint: code`: propose `reject` with `move-to-shared`.
* `container`: `slug`, and for archives `id` (the `--parent` value for members when the container is
  archived). `group` is the label that folder-bundle members share.
* `category_hint` and `top_folders`: the bundle's own name and structure suggest the category; confirm
  with the operator. Prefer an entry from `intake.categories` when one fits.

## 3. Present

Present the bundle as a tree (folders, then members with decision proposals) inside the intake proposal
table: `references/proposal-format.md` of the `intake` skill. Each member is triaged individually and keeps
its `member_path`. Members with `duplicate_of` are duplicates (report them, do not place). Members deeper than
`intake.max_source_depth` below the category are flattened into the file name by `place` (`a/b/c/d/e/f.pdf`
becomes `.../d/e--f.pdf`); mention it when it happens.

Placement of members and the container is described in `intake` step 7.
