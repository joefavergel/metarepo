---
name: create-metarepo
description: "Interview the project owner and scaffold a new knowledge metarepository in an empty folder: contract folders, project.yaml, AGENTS.md and README.md, state. Use when someone wants to start a new project metarepo (/create-metarepo [project name])."
---

# create-metarepo

Create a new metarepository in a folder the owner chooses. You do the interview and the judgment; the engine
(`init`) does the scaffolding. Never create files yourself and never copy toolkit files into the project.

Engine resolution and conventions: `references/engine.md`. Question set, defaults and validation:
`references/interview.md`. Speak to the operator in the language of their first message and follow switches.

## Workflow

1. **Preconditions (R1, R2).**
   - Run `<engine> status --json` (add `--root <target>` only if the target is already a root). Python 3.10+ and
     PyYAML must be available; otherwise show the install instructions from the output and stop (never install).
   - Ask for the target folder if it was not given. It must exist, be writable, and be empty (provider metadata
     files such as `.DS_Store` are fine).
   - If `status --root <target>` or a parent shows a metarepository, stop: point to `/join-metarepo` (to work in
     it) or to editing `project.yaml` (to reconfigure). Do not scaffold.
   - Sync: `init` detects the provider from the path. If it reports none, ask the owner to confirm the provider
     (`google-drive`, `onedrive`, `dropbox`) or `none`. Warn if the folder is inside a git working tree.
2. **Can you ask questions?** If you have no way to ask the operator and no answers file was supplied, create
   nothing. Explain that this skill needs an interview, or that `cli.py init <dir> --answers <file>` /
   `--interactive` can be used instead. (R9)
3. **Interview (R3).** Ask only the basic questions in `references/interview.md`: name (unless given as argument),
   description, specific instructions for agents (optional), code repositories (yes/no, then URL, proposed name,
   default branch). Use `AskUserQuestion` when available, plain questions otherwise. Everything else takes a default.
4. **Advanced settings (R3a).** Offer once to configure advanced settings now. Declining is the default.
5. **Answers file.** Write `answers.json` to a temporary location **outside** the synced folder (never in the
   target).
6. **Proposal (R4).** Run `<engine> init <target> --answers <answers.json> --dry-run --json`. Show the operator the
   tree that would be created and the key `project.yaml` values (`data.created`, `data.project`, `data.sync`) and
   ask for confirmation. Surface every warning.
7. **Scaffold (R5).** On confirmation run the same command without `--dry-run`. Report `data.created` and, on a
   re-run, `data.kept` (R6). Never retry by deleting files.
8. **Validate.** Run `<engine> config validate --root <target>`; it must report no errors.
9. **Hand-off (R7).** Close with three next steps, in the operator's language:
   - members join with `/join-metarepo`;
   - start the wiki with `/init-wiki` (recommended after a first `/intake`);
   - drop material in `00_inbox/`.
   Say that the settings can be edited later in `project.yaml` and that `/init-wiki` revisits objective and scope.
   If the runtime can run sibling skills you MAY offer to run `/join-metarepo` for the owner now.

## Rules

- The engine is the only writer. Do not hand-edit `project.yaml`, `AGENTS.md` or `_system/`.
- Do not assume a domain, company, industry or language; use only what the operator answers.
- Do not invent answers for questions the operator skipped; use the stated defaults.
