# Interview for /create-metarepo

Ask in the operator's language. Use `AskUserQuestion` when available, plain questions otherwise. Keep wording
neutral: no example may name a real domain, company, person or place; use placeholders such as `<project>`.

## Basic questions (always)

| # | Question | Answer key | Notes |
|---|---|---|---|
| a | "What is the project called?" (skip when passed as `/create-metarepo <name>`) | `name` | Propose a slug (lowercase, hyphenated); only send `slug` if the operator changes it. |
| b | "Describe the project in a sentence or two." | `description` | Required, non-empty. Becomes the README text and, by default, the objective. |
| c | "Any specific instructions for agents working in this project? (optional)" | `notes` | Free text, may be empty (omit the key). Must not contain the text `<!-- project-notes:`. |
| d | "Does the project have associated code repositories?" | `repos` | If yes, for each (1..n): URL (`url`); a proposed name from the URL, editable (`name`); default branch (`default_branch`, optional). Omit `repos` for "no". |

## Defaults (not asked)

| Setting | Default |
|---|---|
| Content language | the operator's language (send as `language`, a code such as the one in their first message) |
| Sync provider | detected by `init` from the path; ask only when `data.sync.detected` is null, then send `sync_provider` |
| `scope` | empty |
| `team` | empty |
| `wiki_write_mode` | `lease` |
| `intake.confirm` | `always` |
| `objective` | same as `description` |

## Advanced settings (offered once; default is to decline)

Offer: "Do you want to configure advanced settings now (objective, scope, content language, team, wiki write mode,
intake confirmation)?" If yes, ask only what the operator wants to change:

| Setting | Answer key |
|---|---|
| Objective distinct from description | `objective` |
| Scope in / out | `scope: {in: [...], out: [...]}` |
| Content language | `language` |
| Team members | `team: [{id, display_name, roles}]` |
| Wiki write mode | `wiki_write_mode` (`lease` or `owner`; `owner` needs `wiki_owners`) |
| Intake confirmation | `intake_confirm` (`always` or `when-uncertain`) |

After the interview say these can be edited later in `project.yaml`, and that `/init-wiki` revisits the
objective and scope.

## Validation

The engine validates the answers (`answers.schema.json`) and the resulting `project.yaml`; unknown keys are
rejected. On an `answers_invalid` error, fix the named key and re-run; do not create files by hand.

## Neutrality checklist (AC5)

- [ ] No question or example names a domain, company, industry, person, jurisdiction or natural language.
- [ ] Examples use placeholders (`<project>`, `example-topic`).
- [ ] The language of the conversation follows the operator, not this file.
