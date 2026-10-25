# Installing the metarepo toolkit (portable bundle)

For harnesses other than Claude, where the plugin is not available. The toolkit is installed **once per
machine**. It is never copied into a project, and `install-skills` never writes into a metarepository.

## Requirements

* Python 3.10 or newer: `python3 --version` (Windows: `py -3 --version`).
* PyYAML: `python3 -m pip install pyyaml` (Windows: `py -3 -m pip install pyyaml`).

The toolkit never installs software on its own; it only tells you what is missing.

## Steps

1. Unpack `metarepo-toolkit-<version>.zip` somewhere permanent, for example `~/metarepo-toolkit`.
2. Check the engine runs: `python3 <install dir>/engine/cli.py status --json`.
3. Install the skills into your harness's skills directory:

   ```
   python3 <install dir>/engine/cli.py install-skills --target <your harness skills folder>
   ```

   Use `--dry-run` first to see what would be installed. Re-running is safe: unchanged skills are left alone;
   skills that exist with different content are only replaced with `--force`. Each installed skill records the
   absolute path of this engine in its `references/engine.md`.
4. Point your harness at the metarepository folder. `AGENTS.md` at its root explains the project, and works
   without the toolkit for reading and consulting.

## Engine resolution

Skills find the engine in this order: the `METAREPO_ENGINE` environment variable (path to `cli.py` or an
executable), the engine installed next to the skill, then a `metarepo` executable on `PATH`.

## Harness skills folders

This guide does not assume where your harness looks for skills: pass its skills directory as `--target`.
Consult your harness's documentation for that location.

## Compatibility

Each project declares the toolkit versions it accepts in `project.yaml` (`toolkit.requires`). Commands that
write refuse to run outside that range (exit status 4) and say whether to update the toolkit or migrate the
project (`cli.py migrate --dry-run`). Read-only commands run with a warning.
