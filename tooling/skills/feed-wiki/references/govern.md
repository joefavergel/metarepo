# Governance: roles, sign-offs, claimed identity

Sign-offs are recorded for readers to see; they never block a feed or a read (010/ADR-002). The only hard block in
the wiki path is the project's policy gate.

| Role | Key written | Meaning |
|---|---|---|
| `author` | `generated` | who is responsible for the content (the operator whose id you were given; the agent is `assisted_by`) |
| `reviewer` | `verified` | a human checked the page against its sources |
| `auditor` | `audited` | an independent check, when the project expects one (`audit_expected` in `WIKI.md`) |

* Identity is **claimed**: `human:<id>` is the id the operator states (`--actor`, `METAREPO_ACTOR` or
  `config user`). It is an attestation log, not a legal signature.
* You write `generated` while feeding. You never write `verified` or `audited`, and you never sign for someone
  who did not say so. When the operator says "I reviewed this page" or "mark X as reviewed by Y", run
  `<engine> sign <page> --role reviewer --actor <id>` (add `--stable` only if they also want `status: stable`).
* The same actor in two roles on a page is recorded and the engine warns (`segregation_of_duties`). Relay the
  warning; do not refuse and do not hide it.
* Each sign-off adds a line to `log.md` and an event to `_system/events.log`.
* Editing a page that carries `verified` leaves the sign-off in place (it is not forged or removed), so say in the
  summary which verified pages changed and suggest a new review.
