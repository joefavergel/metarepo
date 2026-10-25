# Offline availability per provider

The toolkit cannot change sync-provider settings. Show the recommendation for the project's provider
(`config get sync.provider`) and ask the member to confirm they applied it. Source of truth for the folder list:
`templates/sync-providers.yaml` → `availability` (004/R13).

| Folder | Recommended |
|---|---|
| `30_wiki/`, `20_extracts/`, `_system/`, root files | keep a local copy (offline) |
| `10_sources/`, `shared/` | online-only is fine; downloaded on demand |

Why: the agent reads the wiki, extractions and `_system/` constantly; a placeholder that is not downloaded looks like a
missing file (`doctor` reports it as `not-local`).

## Google Drive (Drive for desktop)

Shared drives are available offline: Google's help states offline access "includes files from shared drives" (checked
2026-10-25). In the file manager, right-click the folder and choose the offline option (Offline access > Available
offline). For My Drive you may instead mirror files (Settings > Preferences > Folders from Drive). If you stop
streaming, offline files are removed.
[TO VERIFY] Exact menu wording per OS and version: Google's pages consulted did not state it. Re-check on the machine.

## OneDrive

Windows: right-click the folder in File Explorer and choose **Always keep on this device**; **Free up space** reverses
it (Microsoft Support, checked 2026-10-25). macOS: the same option exists in Finder as "Always Keep on This Device".
[TO VERIFY] The macOS wording; Microsoft's Windows page links to a separate macOS article that was not read.

## Dropbox

Right-click the folder and choose **Make available offline** (online-only files feature).
[TO VERIFY] The Dropbox help page for this was not retrievable (404) when checked; confirm on the machine.

## No provider / other

Nothing to apply. Say so and move on.
