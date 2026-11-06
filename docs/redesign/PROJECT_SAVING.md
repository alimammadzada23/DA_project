# User-controlled project saving and deletion

Follow-up on `codex/insightpilot-redesign`, based on
`3f47317b7aa955a1468b4c71f23fd3b1e0850aff`.

## What the user controls

| State / action | Behavior |
| --- | --- |
| Start / New Project | A fresh temporary workspace, with no selected or inherited dataset |
| Upload, analyze, prepare, investigate, or write reports | Changes stay in the current temporary working copy; no Saved Projects entry is created |
| Save Project | Opens the naming dialog and publishes the project's data, retained versions, definitions, findings, analyses, investigation notes, report drafts, and current analytical context |
| Open | Makes an isolated working copy of that saved project's own data and work |
| Save Changes | Explicitly commits the working copy as the next saved snapshot |
| Rename | Opens the project-name dialog; Save Changes commits the name and current changes |
| Discard Project | Removes the managed temporary working copy; an existing saved snapshot is unchanged |
| Leave changed/unsaved work | Offers **Save Project / Discard Project / Cancel** before switching projects |
| Delete | Requires confirmation, removes that saved project from the catalog, and retains its private snapshot in recoverable Trash |

A visible action bar on every project page shows **Save Project / Discard Project**
for temporary projects, or **Save Changes / Rename / Delete** for saved projects.
It indicates **Not saved · Temporary session**, **Unsaved Changes**, or **All changes
saved**. The homepage lists only explicitly saved snapshots and preserved legacy
projects. Each card has a name, dataset names, last saved timestamp, Open and Delete.
Legacy projects whose actual save date is unknown are explicitly labeled unknown;
no save date is fabricated.

Navigating between the ten analysis destinations remains within the same project
and does not prompt to leave. Starting another project or opening a different one
is guarded when there is work to lose. Cancel retains report text and other draft
fields; simply changing pages does not mark a saved project dirty.

Existing buttons such as Save chart, Save view, Save investigation, and Save report
draft now write into the temporary project copy. They do **not** independently
publish a Saved Projects entry. Save Project / Save Changes is the explicit commit
point. Complete current investigation/report editors are included using the
existing report validation; unfinished fields remain editable as project drafts.
No narrative or analytical findings are invented.

## Storage and preservation

The existing `Workspace` DuckDB schema, ingestion, profiling, metric compiler,
statistics, SQL safeguards, and source/provenance design remain unchanged.
New orchestration separates temporary work from saved snapshots:

- `data/universal/.sessions/<random-token>` holds a temporary working copy. It is
  outside the one-level saved-project catalog. Runtime database writes and sidebar
  default initialization can affect this working copy, never a saved snapshot.
- Only explicit Save publishes a full snapshot into a normal catalog directory.
  It retains relative source files and immutable dataset versions. Project display
  IDs remain stable; a staging directory never becomes the public project identity.
- Saved names must be distinct. A save cannot silently replace another project.
  A root lock and saved-snapshot digest reject conflicting edits from another
  session rather than overwriting them.
- A snapshot is staged completely before publication. Failed copy and failed
  publication rollback are tested. Replaced snapshots are retained in `.history`.
- Confirmed Delete moves only the selected catalog directory to `.trash`. It does
  not follow symlinks, erase an external original upload, modify another project's
  data, or introduce a permanent shared-dataset deletion action.
- Discard physically removes only the managed temporary session copy. A saved
  project's original snapshot, unrelated projects, and original input files remain.
- Existing legacy projects are listed without a migration rewrite, automatic
  rename, or deletion. Opening one first makes a working copy. Existing IDs, source
  hashes, tables, versions, definitions, and saved work are preserved.

Investigation inputs now reach temporary session state without requiring an
outside Save Project action to submit an unrelated Streamlit form. Report and
investigation draft widgets are captured under explicit prefixes, not by dumping
all session state. Credentials and unrelated model/API settings are not copied
into the draft-state manifest. Calculation and report validation functions are
reused; no arbitrary saved SQL is executed during project saving.

## Refresh and closing the browser

The address contains a random `project_session` recovery token. Refresh resumes
that same temporary copy, including committed-to-session editor fields, current
analysis, and filters. This recovery storage is **not** a Saved Projects entry.
Opening a fresh address starts a fresh empty project. Treat recovery URLs as private.

The wheel registers a browser-native unsaved-work warning. Chromium verification
observed a `beforeunload` confirmation on refresh. Browser policy controls whether
that generic confirmation is shown and does not allow an application to replace
it with custom Save/Discard/Cancel buttons. Those three explicit choices are
provided for in-app project switching. Refresh recovery also protects work when a
browser suppresses its warning.

Temporary copies use disk because the existing DuckDB architecture is disk-backed.
Closing a tab does not promote a project to Saved Projects and does not purge its
recovery copy; explicit Discard removes it. No timed cleanup, secure erasure,
permanent Trash purge, or Trash-recovery UI is introduced. Snapshot/history storage
uses additional disk space. Long-running or crashed sessions may leave recovery
copies; these never appear as saved projects.

## Verification

**Full regression:** 953 passed, zero failures/skips, one third-party anyio/pytest
deprecation warning. **Final focused storage/lifecycle checks:** 19 passed. These
are overlapping runs, not 972 distinct tests. The focused checks include legacy
identity preservation, original-byte preservation, concurrent-edit rejection,
copy/publication failure handling, symlink rejection, Cancel retaining report text,
Save-before-leaving, no dataset inheritance, explicit saving of current report
editors, confirmed deletion, and navigation not causing false dirty indicators.

Actual Chromium lifecycle checks verified:

1. New project has no active dataset.
2. Upload and analysis work without creating a saved entry.
3. Refresh restores temporary data and emits the native warning in this run.
4. Discard removes the temporary project without adding it to Saved Projects.
5. Save Project creates a named saved entry with data, analyses, investigation,
   report draft, and editable fields.
6. Cancel preserves unsaved report edits; Discard does not change the saved copy.
7. Reopen restores that project's own data and work.
8. Save Changes explicitly updates the stored report editor.
9. Delete requires confirmation; cancelling leaves the project intact.
10. Confirmed Delete leaves an unrelated legacy project and the original uploaded
    fixture byte-for-byte unchanged.
11. All ten wheel destinations still work.

The broader browser regression also passed chart-value reconciliation, Pareto and
donut views, saved evidence, report HTML export, dataset/project switching, scrolling,
refresh, dock expansion and six desktop/mobile viewport sizes. The requested
homepage sentence is absent from application source. Screenshots below were
visually inspected; all data shown is explicitly synthetic verification data.

Commands (use new output directories):

```sh
python3 -m pytest -q
python3 -m pytest tests/test_projects.py tests/test_project_ui.py -q
STREAMLIT_SERVER_FILE_WATCHER_TYPE=none python3 scripts/browser_projects.py --output outputs/project-lifecycle-check
STREAMLIT_SERVER_FILE_WATCHER_TYPE=none python3 scripts/browser_redesign.py --output outputs/project-wheel-check
```

| View | Screenshot |
| --- | --- |
| Empty temporary project, Save / Discard visible | [Start](project-saving/01-empty-unsaved-project.png) |
| Analysis without a saved-project entry | [Analysis](project-saving/02-analysis-without-saving.png) |
| Explicit naming and save dialog | [Save Project](project-saving/04-explicit-save-dialog.png) |
| Saved card with dataset, date, Open and Delete | [Saved Projects](project-saving/05-saved-project-card.png) |
| Leave decision | [Save / Discard / Cancel](project-saving/06-unsaved-change-warning.png) |
| Confirmed project deletion explanation | [Delete](project-saving/07-delete-confirmation.png) |
| Unrelated project preserved after deletion | [After deletion](project-saving/08-deleted-unrelated-project-preserved.png) |
| Mobile controls and preserved wheel | [Mobile](project-saving/09-mobile-save-actions.png) |

[Lifecycle results](project-saving/browser-verification.json) ·
[Broader wheel regression](project-saving/wheel-regression.json) ·
[Test results](project-saving/test-verification.json)

No new cloud service/model, merge, deployment, or push is part of this change.
Native Safari/macOS, live Ollama inference, and large-data snapshot performance were
not tested in this run. Locking uses the existing Linux/macOS development targets.
