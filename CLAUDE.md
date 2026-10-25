# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Pre-alpha, specification phase. **No code exists yet.** The repository holds `README.md` (the public design) and `.specs/` (Spec-Driven Development drafts, git-ignored via `.gitignore` → `.specs/*`). Everything under "Planned layout" and "Commands" below comes from the specs; verify a path exists before relying on it. If `.specs/` is absent (it is not in other clones), treat `README.md` as the design reference and ask the owner for the specs rather than inferring requirements.

## Working from the specs

- Start with `.specs/README.md` (index, dependency order, milestones, definition of done), then `.specs/constitution.md` (principles P1–P14) and `.specs/glossary.md`.
- Implement specs in dependency order (001 → 014). Milestones: **M1** 001, 002, 003, 005, 013 · **M2** 004, 006, 007, 008 · **M3** 009–012 · **M4** 014.
- Each spec folder has `spec.md` (what/why, EARS requirements `R1…`, acceptance criteria `AC1…`), `plan.md` (how), `tasks.md` (ordered, each task names its verification), and optional `decisions/ADR-NNN-*.md`.
- Citation conventions: requirements as `007/R4`; ADRs always with the spec prefix (`002/ADR-003`, never bare `ADR-003`) because ADR numbers restart per spec; principles as `P7`.
- Specs cite principles, never restate or relax them. A new invariant means amending `constitution.md` (with a dated entry in its Amendments section), not a spec.
- `[TBD]` = open decision: frame it and list candidates, never invent the answer. `[TO VERIFY]` = external fact (e.g. Claude plugin/hook manifest format) to check before relying on it.
- `.specs/` is local-only. To make a decision part of the shared record, promote its ADR to a tracked path (e.g. repo-root `decisions/`).

## Planned layout (001)

```
tooling/            canonical, harness-agnostic toolkit: the only place to edit engine/skills/templates
  VERSION           semver; CHANGELOG.md records compatibility notes and migrations
  engine/cli.py     single CLI entry point (argparse dispatcher, no logic)
  engine/metarepo_engine/   Python package (module map in .specs/003-core-engine/plan.md)
  engine/migrations/        <from>_to_<to>.py for MAJOR versions
  skills/<name>/SKILL.md    portable Agent Skills workflows
  templates/        project skeleton, AGENTS.md, project.yaml, WIKI.md, output templates
plugin/             Claude plugin: skills/, engine/, templates/ are BUILD COPIES of tooling/; plus hooks/
.claude-plugin/marketplace.json
tools/              dev-only: build.py, package.py, ci_check.py, denylist.txt
tests/              engine and structure tests (tests/engine/ uses a fixture-metarepo builder)
```

Never edit `plugin/skills|engine|templates` directly; edit `tooling/` and rebuild. CI fails if the copies differ.

## Commands (planned)

```bash
python3 tools/build.py      # copy tooling/ into plugin/, stamp plugin.json version from tooling/VERSION
python3 tools/package.py    # deterministic dist/metarepo-plugin-<v>.zip and dist/metarepo-toolkit-<v>.zip
python3 tools/ci_check.py   # stdlib-only structure checks (manifests, SKILL.md frontmatter, command set,
                            # plugin == tooling hashes, version sync, denylist, no-artifact rule)
```

The test runner is not yet chosen in the specs; when it is, record how to run the suite and a single test here. Every spec's last task is "tests and `tools/ci_check.py` pass". CI matrix: ubuntu, macos, windows × Python 3.10 and latest.

## Architecture (big picture)

- **Two distribution channels from one source** (001): the Claude plugin and a portable zip bundle for other harnesses (installed via `cli.py install-skills --target <dir>`). The plugin adds no workflow logic; hooks are an enhancement only.
- **Projects contain zero toolkit artifacts** (001/ADR-004). A scaffolded metarepo holds only project content, `project.yaml`, rendered `AGENTS.md`/`README.md`/`code/README.md`, and `_system/`. Version consistency comes from `project.yaml → toolkit.requires`; write commands exit 4 outside that range; MAJOR changes need a migration.
- **Skills call the engine, never reimplement it** (P4, 003/ADR-001). The model does judgment (triage, synthesis, agent-vision extraction); hashing, IDs, placement, registry, linting, leases and scaffolding are engine commands. Skills locate the engine via `METAREPO_ENGINE` → bundled engine next to the skill → `metarepo` on `PATH`, else stop with install instructions (001/R5). No harness-specific variables (e.g. `${CLAUDE_PLUGIN_ROOT}`) in skill bodies.
- **CLI contract is a public API** (003): `cli.py <command> [--json] [--root <path>]`; `--json` prints one envelope `{ok, command, version, data, warnings, errors}`; diagnostics go to stderr; exit codes `0` ok, `1` blocking findings, `2` usage, `3` precondition (not a metarepo, gate closed, lease held), `4` compatibility/integrity, `5` missing dependency. Every mutating command supports `--dry-run`. Envelope/exit-code changes are MAJOR and covered by golden tests.
- **Metarepo folder contract** (002, README): `00_inbox → 10_sources (immutable) → 20_extracts (one Markdown per source; its frontmatter *is* the source record) → 30_wiki (OKF bundle + WIKI.md)`, plus `outputs/`, `shared/`, `personal/`, `_system/` (gates in `state.json`, leases in `locks/`, `events.log`). Source IDs are content-addressed: `src-<first 12 hex of sha256>`. Registry is a derived view, never hand-edited. Caches, indexes, staging and migration backups live in a per-member local cache outside the synced tree.
- **Sync safety** (004, P9): the metarepo lives on a cloud drive (Google Drive Shared Drive is primary). Shared hot files are written only under a lease; all writes are atomic (temp file in same dir → fsync → `os.replace`); conflicts are detected, never silently merged; no git inside the synced tree.
- **Workflow chain**: `/intake` (inbox → sources + extractions) chains into `/feed-wiki` (the only wiki writer) for items triaged `wiki`. Triage decisions: `wiki`, `reference`, `defer`, `reject`. The command set in 001/ADR-003 is fixed and CI-checked.

## Hard constraints for implementation

- **Project-agnostic (P1).** No domain, company, industry, person, jurisdiction or natural-language assumption in shipped files (`tooling/`, `plugin/`). Use neutral placeholders (`<project>`, `example-topic`). CI enforces this with `tools/denylist.txt`.
- **Prior OKF engine.** Some plans say "port from the prior OKF engine" (`okf.py`, `lint_wiki.py`, `search_wiki.py`, …). It may be consulted if available, but strip every domain-specific rule; nothing here may depend on it.
- **Python ≥ 3.10, PyYAML is the only hard dependency** (001/ADR-002). Runtime code and dev scripts are Python; `tools/ci_check.py` is stdlib-only. Detect optional extractors, never install anything (P13).
- **Cross-platform (P10).** `pathlib` everywhere, NFC-normalized paths, Windows path-length limits, forced UTF-8 for stdio and files, POSIX-style relative paths in JSON output.
- **No network** in the engine except `repos clone` and explicitly enabled cloud extractors.
- **Idempotent (P12).** Same inputs are a no-op; scaffolding never overwrites existing files.
- **Deletions** (P6): never touch `10_sources/`; other removals only via `remove <path> --confirm <sha256>`, logged to `_system/events.log`.
- **Language separation (P14).** Code, paths, frontmatter keys, identifiers and event labels are English; specs are English with EARS phrasing.
