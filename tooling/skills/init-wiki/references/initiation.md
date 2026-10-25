# Survey, axis mechanics and first-feed ordering

## Survey (spec 009/R2)

Goal: decide what the wiki should contain before asking the owner. Budget: stay within
`intake.triage_budget_tokens` per batch of samples; never load whole extractions.

1. Count and group: `<engine> registry --json` (use `--filter triage.decision=wiki` when present). Note media
   types, folders in `20_extracts/`, titles, and `quality`.
2. Sample broadly, not deeply: for 10 to 20 extractions spread across folders and kinds, read the frontmatter and
   the headings only.
3. Look for recurrence: use `<engine> search "<term>" --scope extracts` for terms that appear in many titles or
   headings. A term that recurs across several sources is a candidate page; a one-off is not.
4. Answer four questions and keep the answers for the proposal:
   - what kinds of material exist;
   - what recurs (candidate pages, candidate areas);
   - what the project wants to end up knowing (`objective` in `project.yaml`);
   - who reads the wiki (team, or one person).
5. No extractions yet: say the survey is empty, recommend `/intake` first, and let the owner decide to continue.

## Axis mechanics

| Axis | Folders under `30_wiki/` | `type` | `index.md` grouped by |
|---|---|---|---|
| `area` | one per area: `<area>/` | frontmatter | area |
| `type` | one per page type: `<type-folder>/` | frontmatter and folder | page type |
| `flat` | `pages/` | frontmatter | page type |

Always present: `sources/` (type `Source`), `skills/` (type `Skill`, holds `answer-from-wiki.md`). On the `type` axis
the area, if wanted, is recorded in `tags`. Search and lint do not depend on the layout.

Folder names: kebab-case in the content language. The engine never pluralises; if the project wants plural
folders, give them explicitly (`{"name": "Process", "folder": "processes"}`).

Choosing: prefer `area` when readers think in subject areas that the survey shows clearly; `type` when
the material is uniform and readers look things up by kind; `flat` for a small or personal wiki.

## First-feed ordering

Recommend, in this order: (1) sources that define vocabulary (glossaries, overviews, specifications); (2) sources
that describe how things work; (3) the rest by relevance to the objective. After two or three sources, stop and check
granularity with the owner: too many tiny pages means the threshold in `WIKI.md` is too low; pages that mix topics
mean it is too high. Edit `WIKI.md` before continuing.
