# Giving the agent access to both folders

The agent needs the metarepository folder (shared, synced) and the code folders (local clones, outside it).
`<engine> repos path <name>` prints each clone path. Pick the section for the harness in use.

## Claude Cowork

Connect both folders to the project: the metarepository folder and the parent folder holding the clones
(for example `~/code/<project-slug>/`). Both must be connected, otherwise the agent sees only one side.

## Claude Code

Start Claude Code in a workspace that contains both, or add the second folder as an additional directory:

- open a terminal in the metarepository folder and add each code folder with the `--add-dir <path>` option, or
  `/add-dir <path>` inside a session;
- or start in the parent workspace that holds both folders.

The project instructions are in `AGENTS.md` at the metarepository root. Claude Code reads `AGENTS.md` natively only
when no `CLAUDE.md` exists, which the project guarantees (002/R2b); do not create a `CLAUDE.md` in the project.

## Other harnesses

Open a workspace that includes the metarepository folder and the code folders, or configure the harness with both
paths. Point the agent at `AGENTS.md` in the metarepository root first; it describes the folder contract and the
workflows. Install the portable skills with `install-skills --target <the harness skills folder>`.

[TO VERIFY] Option names above (`--add-dir`, `/add-dir`) follow the Claude Code documentation at the time of
writing; check `claude --help` if they differ on the installed version.
