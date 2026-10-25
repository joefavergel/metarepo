# Migrations

A migration moves a project between two compatibility lines and is a file named `<from>_to_<to>.py`:

* from toolkit 1.0 onward a line is the MAJOR number: `1_to_2.py`;
* in the 0.x series a line is `0.<minor>`: `0.1_to_0.2.py`.

Each file defines:

```python
def migrate(root, dry_run):
    """Return the root-relative POSIX paths this migration changes (or would change)."""
```

With `dry_run=True` it must change nothing and return exactly the paths it would touch. `cli.py migrate`
uses that list to back up every file into the member's local cache before the real run, bumps
`toolkit.requires`, and records the migration in `_system/state.json`. A migration that touches a file
its dry run did not announce is reported as an error.

No migration ships in 0.1.0.
