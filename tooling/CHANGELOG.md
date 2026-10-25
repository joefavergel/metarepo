# Changelog

Every version records its compatibility notes: whether the `toolkit.requires` range written into new
projects changes, and which migrations ship (001/R13). Versioning rules: MAJOR = breaking change to the
folder contract, frontmatter schemas or the CLI JSON envelope (needs a migration and a new `requires`
range); MINOR = new commands or checks, backward compatible; PATCH = fixes. While the major version is 0,
a MINOR bump is treated as a compatibility line change (see `engine/migrations/README.md`).

## 0.1.0

- First skeleton: engine CLI with a command registry, JSON envelope and exit codes, compatibility gate,
  `install-skills`, `migrate`, project contract (schemas, templates, naming, source ids), core services
  (atomic file operations, frontmatter, events log, hashing, registry, search).
- Compatibility: new projects are written with `requires: ">=0.1,<0.2"`.
- Migrations: none shipped.
- 004 concurrency: `lease acquire|renew|release|status` (advisory leases with settle-and-verify, owner mode),
  `leases.require_lease` guard for shared-file writers, conflict-copy detection (`conflicts`,
  `templates/sync-providers.yaml`), `not-local` readability check in hashing. `migrate` and `render-templates`
  now take the `config` lease and log `lease acquire|release|takeover` events. Additive; no `requires` change.
- 013 doctor: `doctor [--local] [--fix-safe --yes <id> --dest <id>=<folder>]` with the 20 checks of spec 013, a gitignore-syntax matcher (`gitignore.py`) for `policy.forbidden_paths`, per-action confirmed fixes, `state.json -> last_doctor`; the `/doctor` skill. Additive; no `requires` change.
- 007 intake: `inbox scan`, `unbundle`, `place` (modes: place, `--discard`, `--duplicate-of`, `--reject`,
  `--retriage`, `--proposal`, `--completed`), policy gate, bundle validation (ZIP/TAR, optional 7z/RAR tool,
  folders), provisional extraction in local staging, `intake` and `unbundle` skills. Additive; no `requires` change.
