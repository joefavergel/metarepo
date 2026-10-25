---
name: join-metarepo
description: "Onboard a team member onto an existing metarepository: find the shared folder, check runtime and toolkit, record who you are, clone the project's code repositories outside the synced folder, confirm offline-availability settings, wire the agent to both folders, and verify. Safe to re-run to repair a setup. Use when someone joins a project metarepo (/join-metarepo)."
---

# join-metarepo

Set up this machine for an existing metarepository. You do the conversation and the judgment; the engine does every
check and write. Re-running keeps what is correct and fixes only what is missing or broken (R10). Never edit files
in the synced folder, never install software, never change sync-provider settings (P13).

Engine resolution and conventions: `references/engine.md`. Harness wiring: `references/harness-wiring.md`. Offline
availability per provider: `references/sync-providers.md`. Speak the operator's language.

## Workflow

1. **Locate (R1).** Run `<engine> status --json`. With no root found, ask for the shared folder path and retry with
   `--root`. If there is none, explain how to get access (accept the shared-folder invitation, install and sign in to
   the sync client, wait for the folder to appear) and **stop**.
2. **Runtime (R2).** From `status`: Python 3.10+, PyYAML, and the optional extractors (report, do not install). When
   something is missing show the install instructions the output contains and stop for required items.
3. **Toolkit (R3).** `status` reports compatibility with `project.yaml → toolkit.requires`. If outside the range, give
   the install/update steps for the operator's harness (plugin update, or re-install the portable bundle with
   `install-skills`) and stop. Exit 4 from write commands means the same.
4. **Identity (R4).** Run `<engine> config user --json` to see what is stored. Read `team` with
   `config get team`. Ask which member the operator is (list ids and display names) or whether they are new.
   Store it with `<engine> config user --set actor=<id>`. A new id is only a *claimed identity*: say so (the engine
   warns `actor_not_in_team`) and ask the project owner to add the member to `project.yaml → team` (that needs the
   `config` lease and the owner's confirmation; this skill does not edit `project.yaml`).
5. **Repositories (R6).** `<engine> repos list --json`; when none are declared, skip to step 7. For each repository:
   - Propose the local path `~/code/<project-slug>/<name>` (or the already recorded one); let the operator change it.
   - **Refuse any path inside the synced folder** and explain: git metadata and build folders must not sync (AC2). The
     engine refuses it too (`clone_inside_tree`); choose another path instead.
   - Preview with `repos clone <name> --dest <path> --dry-run --json`: `data.action` is `adopt` (an existing correct
     clone is only recorded) or `clone`.
   - **Ask before cloning**, then run it without `--dry-run`. It uses the operator's own git credentials. Report a
     failure (`clone_failed`, `git_missing`) with its message and the way to fix it; do not retry silently.
6. **Harness (optional).** Record the harness name with `config user --set harness=<text>` if the operator wants.
7. **Sync settings (R7).** Detect the provider from `status`/`config get sync.provider`. Show the recommended
   per-folder availability and the steps from `references/sync-providers.md` and ask the operator to confirm they have
   applied them. You cannot change provider settings.
8. **Wiring (R8).** Explain, for the current harness, how to give the agent access to the metarepository folder and the
   code folders (`repos path <name>` prints each), per `references/harness-wiring.md`. Point non-Claude harnesses to
   `AGENTS.md` in the metarepository root.
9. **Verify (R9).** Run `<engine> doctor --local --json` when it is available, and always `repos status --json` and
   `config user --json`. Show a short pass/fail checklist: root files readable, toolkit compatible, every repository in
   state `ok`, none `inside-tree`, per-user config present outside the tree. If `doctor` answers `not_implemented`, say
   the check ran without it.
10. **Close.** Summarise what changed and what was already correct. A second run on a correct machine must report
    "nothing to change".

## Rules

- The per-user config lives at `~/.config/metarepo/<project-id>.yaml` (Windows `%APPDATA%\metarepo\`), never in the
  synced folder. Only `config user` and `repos clone` write it.
- Ask before every clone and before every change to the operator's configuration.
- Make no assumption about domain, company or language beyond what the project and the operator state.
