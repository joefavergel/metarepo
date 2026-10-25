# Adding or replacing an engine command

The engine uses a command registry so that several people can add commands in parallel without touching
shared code.

* `cli.py` is a thin entry point. It never changes.
* `metarepo_engine/dispatch.py` owns everything cross-cutting: argument tree, `--json`/`--root`,
  UTF-8, the JSON envelope, exit codes, the compatibility gate, `--dry-run`, the event log.
* Each command is one module in `metarepo_engine/commands/` that exposes a single `COMMAND`.
* `metarepo_engine/commands/__init__.py` lists the modules in `MODULES` (sorted, one line each).

All commands from the 003 command table are already registered. Commands owned by later specs exist as
stubs (`implemented=False`) that answer `not_implemented` with exit status 2. **To implement one, edit its
module and replace the stub `COMMAND`; the registry does not change.** To add a command that is not in the
table, add one module and one line in `MODULES`.

## Module shape

```python
import argparse
from ..command import Command, Context
from ..jsonout import Result

def _args(p: argparse.ArgumentParser) -> None:      # optional
    p.add_argument("path")

def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()                  # only when needs_project=True
    ...
    return Result(data={"..."}, targets=["30_wiki/x.md"])   # targets go to the event log

COMMAND = Command(
    name="wiki gate",              # "group sub" makes a nested subcommand; one space at most
    help="...",
    run=run,
    add_arguments=_args,
    mutating=True,                 # adds --dry-run and --actor
    writes_project=True,           # compatibility gate refuses outside toolkit.requires (exit 4); event logged
    needs_project=True,            # root discovered first (exit 3 when absent)
    owner="009",
)
```

## Rules

* Commands return a `Result`; they never print JSON, never call `sys.exit`. Raise `EngineError(code,
  message, exit_code)` to stop with a specific status (see `jsonout.py` for the exit-code constants).
* Honour `ctx.dry_run`: describe the planned operations in `data` and change nothing.
* Paths in `data` are POSIX-style and relative to the root. Use `pathlib`, UTF-8, and
  `fsops.atomic_write_*` for every write; never overwrite unless the command's contract says so.
* Set `Result.log_event = False` when a writing command changed nothing.
* Reads never write; writes happen only through the engine (principles P4, P9, P12).
* No network access except `repos clone` and explicitly enabled cloud extractors (003/R14); a test greps for it.
* Add tests under `tests/engine/`. The registry-wide test (`tests/engine/test_registry_contract.py`)
  checks `--help`, `--json` and `--dry-run` for every command, so your module is covered automatically.
* The envelope and exit codes are a public API. Changing them is a MAJOR version change.
