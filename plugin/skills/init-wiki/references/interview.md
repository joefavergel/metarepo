# Interview for /init-wiki

Ask in the operator's language. Use `AskUserQuestion` when available, plain questions otherwise. Keep wording
neutral: no example may name a real domain, company, person or place; use placeholders such as `<project>` and
`example-topic`. Write prose answers in `language.content`.

## Questions

| # | Question | Answer key | Default / notes |
|---|---|---|---|
| 1 | "What should this wiki let people know or do? (objective)" | `objective` | Restate `project.yaml` `objective` and ask to confirm or adjust. |
| 2 | "Is it for the team or for one person?" | `profile` | `team` (default) or `personal`. |
| 3 | "How should pages be organized: by area, by page type, or flat?" | `axis` | `area`, `type` or `flat`. Propose one from the survey. |
| 4 | "Which areas?" (only when axis = area) | `areas` | List of names, or `{name, folder, description}`. |
| 5 | "Which kinds of page do you want?" | `page_types` | Free list, at least one. `Source` and `Skill` are structural: never ask for them. Suggestions only: `Concept`, `Entity`, `Process`, `Decision`, `Requirement`, `Question`, `Comparison`, `Timeline`, `Metric`, `Policy`. Entries are names or `{name, folder, description}`. |
| 6 | "When does a subject earn its own page?" | `threshold` | Free text. Default: the neutral threshold written in the template. |
| 7 | "What should ingestion emphasize or avoid?" | `ingest_emphasis` | Free text. Default: vocabulary-defining sources first. |
| 8 | "Which roles will sign pages off, and is an audit expected?" | `roles`, `audit_expected` | `roles` default `["author", "reviewer"]`; `audit_expected` defaults to `project.yaml` `governance.audit_expected`. |

Optional: `governance` (text replacing the generated governance paragraph), `open_questions` (list),
`examples` (`{type name: example title}`; usually left for after the first feed).

## Answers file

JSON written outside the synced folder, passed with `wiki scaffold --answers <file>`:

```json
{
  "objective": "<one or two sentences>",
  "profile": "team",
  "axis": "area",
  "areas": ["example-area-a", "example-area-b"],
  "page_types": ["Concept", {"name": "Process", "folder": "processes"}],
  "threshold": "<text>",
  "ingest_emphasis": "<text>",
  "roles": ["author", "reviewer"],
  "audit_expected": false
}
```

Flags `--axis`, `--profile`, `--area` and `--page-type NAME[=FOLDER]` override or replace the file.

## Validation

The engine rejects an invalid combination with `wiki_config_invalid` (exit 2): unknown axis or profile, axis `area`
without areas, no page type, duplicate names or folders, reserved folder names (`sources`, `skills`, `pages`,
`_meta`), a name with no letters or digits and no explicit `folder`. Fix the named key and re-run.

## Neutrality checklist (AC3)

- [ ] No question or example names a domain, company, industry, person, jurisdiction or natural language.
- [ ] Starter page types are generic and offered as suggestions only.
- [ ] The language of the conversation follows the operator, not this file.
