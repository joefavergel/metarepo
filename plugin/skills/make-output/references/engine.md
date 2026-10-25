# Locating the metarepo engine

Every workflow in this skill calls the metarepo engine (`cli.py`) instead of re-implementing what it does.
Find the engine with the first rule that matches, in this order:

1. The environment variable `METAREPO_ENGINE`: a path to `cli.py`, or to an executable.
2. The engine bundled with this installation: the path recorded below. If it says `unresolved`, look
   for `engine/cli.py` two folders above this skill's folder (plugin or portable-bundle layout).
3. A `metarepo` executable on `PATH`.

If none of them exists, stop and tell the operator how to install the toolkit (see `INSTALL.md` in the
portable bundle, or install the plugin). Do not guess a location and do not install anything yourself.

<!-- engine-path:start -->
path: ../../engine/cli.py
<!-- engine-path:end -->

## Running it

Use the first of `python3`, `python`, `py -3` (Windows) that reports Python 3.10 or newer:

```
<python> <engine path> <command> [arguments] --json
```

* `--json` prints a single JSON object `{ok, command, version, data, warnings, errors}` on stdout.
* Exit codes: `0` ok; `1` blocking findings; `2` usage error; `3` precondition failed (not a metarepo,
  gate closed, lease held); `4` toolkit outside the project's compatibility range, or integrity error;
  `5` missing dependency (install instructions are in the message).
* Pass `--root <path>` when the current directory is not inside the metarepository.
* Every command that changes something accepts `--dry-run`; use it to preview before asking the operator
  to confirm.
