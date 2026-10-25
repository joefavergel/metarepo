# metarepo

**A harness-agnostic toolkit for running a team's project knowledge as a "knowledge metarepository": one cloud-synced folder that holds every source, extraction, wiki page, output and shared tool of a project — maintained by LLM agents, curated by humans.**

> **Status: pre-alpha — specification phase.** This repository currently contains the design. The implementation is driven by the Spec-Driven Development documents in `.specs/` (local, git-ignored working drafts).

---

## Why

Multidisciplinary teams accumulate project knowledge in PDFs, spreadsheets, recordings, slide decks and chat threads spread across drives. LLM agents can turn that pile into a living knowledge base, but three problems get in the way:

1. **Cost.** Agents that re-read original binaries (PDF, XLSX, audio) on every question burn tokens and context.
2. **Drift.** Answers re-derived from raw files each time are inconsistent, uncited and lost after the session.
3. **Team reality.** People use different agent harnesses, are not all developers, and share files through a cloud drive — not git.

`metarepo` addresses these with a fixed folder contract, a persistent Markdown extraction layer, a compounding wiki (the [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)), deterministic scripts for everything that can be deterministic, and human approval for everything that changes shared knowledge.

It is **project-agnostic**: nothing in the toolkit assumes a domain, company, industry or language. Every project-specific choice lives in the project's own `project.yaml` and `WIKI.md`.

## What a knowledge metarepository is

A **single point of access** to everything a project knows: sources, their text extractions, the synthesized wiki, generated deliverables, shared tools and references to the project's code repositories. Code is *referenced, not contained* — official repositories stay in git and are cloned locally, outside the synced tree.

> **Naming note.** "Meta repo" usually means a git repository that groups other git repositories (e.g. `mateodelnorte/meta`, `twosigma/git-meta`). A *knowledge* metarepository is different: it is not a git repository and its primary content is knowledge, not code.

## The folder contract

```
<project>/                     ← synced folder (e.g. a Google Drive Shared Drive)
├── AGENTS.md                  ← instructions for any agent, Claude included (single source of truth)
├── README.md                  ← for humans: how to join and work in this metarepo
├── project.yaml               ← project configuration (scope, language, repos, policies)
│
│   — information lifecycle —
├── 00_inbox/                  ← drop zone: new, untriaged material
├── 10_sources/                ← immutable originals (PDF, XLSX, audio, video, images…)
├── 20_extracts/               ← one Markdown extraction per source — what agents read
├── 30_wiki/                   ← the project knowledge base (OKF bundle) + WIKI.md
│
│   — human work areas —
├── outputs/                   ← generated deliverables: one-pagers, slides, reports
├── shared/                    ← anything shared that is not a source; shared/tools/ for team tools — synced, never ingested
├── personal/<member>/         ← per-person scratch space
├── code/README.md             ← only if the project has code: index of repositories and how to clone them outside this folder
│
│   — machine areas —
└── _system/                   ← shared project state: configuration gates, write leases, event log
```

Folder names tell you what you may do there: **numbered** folders are stages of the information lifecycle, **plain** names are human work areas, and **`_`** folders are managed by the engine and not edited by hand.

A project contains **only project information, configuration and state** — no code or artifacts of the toolkit itself.

Code repositories live **outside** the synced folder (for example `~/code/<project>/<repo>`); `code/README.md` only tells the team which repositories exist and how to clone them. Each member's local paths are kept in a per-user config file, never in the shared tree.

### Access tiers (token economy)

| Tier | Folders | Rule for agents |
|---|---|---|
| Free | `AGENTS.md`, `30_wiki/`, registry | Always the entry point |
| On demand | `20_extracts/` | Search first, then read only the relevant sections |
| Explicit request only | `10_sources/`, `shared/` | Never opened unless the user asks |

## Commands

| Command | What it does |
|---|---|
| `/create-metarepo` | Interviews the owner and scaffolds a new metarepository: folders, `project.yaml`, `README.md`, `AGENTS.md`. |
| `/join-metarepo` | Onboards a team member: checks sync settings, clones code repos outside the synced tree, records local paths, verifies the installed toolkit is compatible. |
| `/intake` | Processes `00_inbox/`: de-duplicates, unpacks bundles (ZIP…), checks policies, proposes a triage decision per item, and — after approval — files originals into `10_sources/`, writes their extractions to `20_extracts/`, and **chains into `/feed-wiki`** for the items approved for the wiki. |
| `/init-wiki` | One-time guided setup of the wiki: objective, taxonomy, page types, governance; scaffolds the OKF bundle and runs the first feed. |
| `/feed-wiki` | Folds approved sources into the wiki from their extractions: source pages, cascade updates, contradictions, index and log. The only wiki writer. Runs automatically at the end of `/intake`; invoked directly for queued, promoted or stale sources, file-backs and proposals. |
| `/consult-wiki` | Answers questions from the wiki (read-only), citing pages and escalating to extractions only when needed. |
| `/lint-wiki` | Health-checks knowledge integrity: provenance, stale or missing extractions, orphans, broken links, policy violations. |
| `/make-output` | Produces deliverables (one-pager, slides, report…) from the wiki into `outputs/`, with citations. |
| `/doctor` | Checks environment and sync health: forbidden files, conflict copies, unreadable cloud-native files, path limits, toolkit compatibility. |

## A typical flow

Ana finds a paper relevant to the project.

1. She drops `2410.12345v2.pdf` into `00_inbox/`. Nothing else.
2. She (or anyone on the team) runs `/intake`. The agent hashes it, checks for duplicates, extracts it to Markdown, reads only the abstract and table of contents, compares it with the project scope and the wiki, and proposes: *"Ingest into the wiki — it covers an in-scope topic the wiki lacks."*
3. Ana approves. The PDF moves to `10_sources/papers/…` (immutable), its extraction lands in `20_extracts/papers/….pdf.md`.
4. In the same run, `/intake` hands over to `/feed-wiki`, which reads the **extraction**, writes a source page, creates or updates topic pages with citations, records contradictions, and updates `index.md` and `log.md`.
5. A week later, Carlos asks `/consult-wiki` a question. The agent answers from the wiki, cites the pages, and never opens the PDF — unless Carlos asks for it.

## Design principles

- **Project-agnostic.** No domain, company or language baked in; configuration over convention.
- **Harness-agnostic.** Behaviour is defined in portable `SKILL.md` files and `AGENTS.md`. The Claude plugin is a convenience layer; other harnesses install the portable toolkit bundle.
- **Projects hold only project content.** The toolkit is installed per harness, never copied into a project. Each project declares the compatible toolkit range in `project.yaml`; write operations refuse to run with an incompatible version, and schema changes go through explicit migrations.
- **Deterministic where possible.** Hashing, registry, placement, linting, locking and scaffolding are scripts. The model does judgment: triage proposals and synthesis.
- **Humans approve shared-knowledge changes.** Unattended runs take conservative defaults and say so.
- **Immutable sources; deletions explicit and logged.** Originals are never modified or removed. Other removals (duplicates, rejected or forbidden files) require confirmation, are recorded in the event log with their hash, and are recoverable from the sync provider's trash.
- **Provenance end to end.** Wiki claim → source page → extraction → original.
- **Sync-safe.** Assumes eventual consistency and concurrent editors: leases for writers, atomic writes, conflict detection, no git inside the synced tree.
- **Cross-platform.** Windows 11, macOS and Linux are first-class.

## Requirements

- A folder synced to every member. Google Drive for desktop with a **Shared Drive** is the primary target; other providers (OneDrive, Dropbox) or a plain local folder are supported with fewer guarantees.
- Python 3.10+ with PyYAML. Optional extractors (e.g. MarkItDown, Docling, PyMuPDF) improve extraction quality; the toolkit detects them and never installs anything on its own.
- Any LLM agent harness that can read files and run shell commands (Claude Cowork / Claude Code via the plugin; others via the portable toolkit bundle and `AGENTS.md`).

> **Google Drive has no native ignore mechanism.** The toolkit therefore does not try to filter synchronization: code lives outside the synced folder by design, and `/doctor` reports content that must never be in a project (e.g. `.git`, dependency folders, secrets) per `project.yaml → policy.forbidden_paths`.

## Installation

### Claude (Cowork / Claude Code)

Add this repository as a plugin marketplace and install the `metarepo` plugin. Run `/create-metarepo` inside an empty synced folder, or `/join-metarepo` inside an existing one.

### Other harnesses

Download `metarepo-toolkit-<version>.zip`, unpack it once per machine and run `python engine/cli.py install-skills --target <your harness skills folder>` (see `INSTALL.md`). Then point the harness at the metarepository folder; `AGENTS.md` explains the project. Without any toolkit installed, an agent can still read and consult the project by following `AGENTS.md`.

## Repository layout

```
metarepo/                       ← this repository
├── README.md
├── .claude-plugin/
│   └── marketplace.json        ← catalog for the Claude plugin
├── plugin/                     ← the Claude plugin (build copies of skills, engine, templates + hooks)
├── tooling/                    ← canonical, harness-agnostic toolkit (source of the plugin and the portable bundle)
│   ├── VERSION
│   ├── engine/                 ← Python engine + CLI
│   ├── skills/                 ← portable SKILL.md workflows
│   └── templates/              ← project skeleton, AGENTS.md, project.yaml, WIKI.md, output templates
├── tools/                      ← dev-only: build, package, CI checks
├── tests/                      ← dev-only: engine and structure tests
└── .specs/                     ← Spec-Driven Development drafts (git-ignored)
```

## Development

Work is specified before it is built. Read `.specs/README.md`, then implement specs in dependency order; each spec has `spec.md` (what and why), `plan.md` (how), `tasks.md` (ordered, verifiable steps) and, where needed, `decisions/` (ADRs).

Set up and check a clone:

```bash
python3 -m pip install -r requirements-dev.txt   # PyYAML + pytest
python3 tools/build.py                           # refresh plugin/ from tooling/
python3 tools/ci_check.py && python3 -m pytest
```

Edit only `tooling/`; `plugin/skills`, `plugin/engine` and `plugin/templates` are build output. To add an engine command see `tooling/engine/CONTRIBUTING-commands.md`.

## Prior art and acknowledgements

- Andrej Karpathy — *LLM Wiki* (raw sources → LLM-maintained wiki → schema; ingest / query / lint).
- Open Knowledge Format (OKF) — portable Markdown + YAML-frontmatter knowledge bundles, used as the wiki page format.
- Cookiecutter Data Science — immutable raw data and derived layers.
- Johnny.Decimal — numbered areas with gaps.
- The many open-source LLM Wiki implementations that explored registries, review queues and multi-agent hubs.

## License

`[TBD]`
