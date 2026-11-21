# Changelog

## 2026-11-21 — Privacy cleanup for public repo

- Blanked the Directory Path defaults in both graphs: `Inputs[0].Value`, plus the
  Directory Path node's `HintPath` and `InputValue`. This removes a Windows
  user-profile path (username, employer name, project-folder name) from the
  published files.
- Audited both `.dyn` files for PII: no human names, emails, phone numbers, URLs,
  absolute POSIX paths, project numbers or sample baselines found. `Author` and
  `Thumbnail` are empty; graph/node UUIDs remain and are identifiers, not PII.
- Docs updated: Directory inputs now ship blank and must be selected per run.

## 2026-11-20 — MVP2.4C build (save/sync + detached Redraw)

### `1. Create Redraw.dyn`
- Embedded Python replaced (node `85a5caf1d73448c39a067e3776b44474`).
- **Removed** the `Save Before Creating Redraw` boolean entirely: `BooleanInputNode`
  `4ab31598b8a64979ab08f76530452dd7`, its connector, the graph `Inputs` entry, its
  `View.NodeViews` entry, and the Python node's `IN[1]` port `741ace8c1775471ca802d79965c7e84b`.
  The graph now has one input: `Directory Path`.
- **Always saves/synchronizes Main before extracting the baseline and copying:**
  - non-workshared local → `Document.Save()` (fails if the file was never saved);
  - non-workshared cloud → `Document.SaveCloudModel()`;
  - workshared local → `SynchronizeWithCentral` with `RelinquishOptions(False)`,
    `SaveLocalAfter=True`, comment "Redraw baseline creation";
  - central opened directly → `Save()` / `SaveCloudModel()`;
  - aborts if `doc.IsModified` is still true afterwards.
- **Detached Redraw creation** (was: plain `SaveAs`, sometimes workshared output):
  - non-workshared → direct `SaveAs`; Redraw becomes the active document;
  - local workshared → open central with `DetachAndDiscardWorksets`, `SaveAs` target,
    close; Main stays active; falls back to the temp-central flow on failure;
  - cloud workshared → `SaveAs` a temporary local central (`SaveAsCentral=True`),
    `Application.CopyModel` to a second temp file, open that detached, `SaveAs` target,
    close/delete the second temp. The temporary central is deliberately left open;
    QA warns the operator to close/delete it and reopen Main.
- **Baseline additions:** per-stand `level_id`, `level_name`, `phase_created`,
  `phase_demolished`, `design_option`, `workset`; `redraw.output_type`
  (`LOCAL` / `LOCAL_DETACHED`), `redraw.is_workshared: false`,
  `redraw.detached_from_central`; `extraction_settings.merge_parameters` (the 6 merged
  parameters); `counts.unbounded_rooms`.
- Placement status now comes from `Room.IsPlaced`; unplaced and placed-but-unbounded
  rooms are retained for audit instead of being mislabelled; `Room.Area` reads and
  boundary extraction for unplaced rooms are failure-tolerant.
- Preflight (before any save): valid project document, directory exists, target
  `.rvt`/`.baseline.json` pair absent, no other primary RVT or baseline in the folder.
- Sidecar still written atomically (`.pending` → rename) and both output files are
  size-verified after `SaveAs`. QA remains watch-only (`OUT[1]`); no QA sidecar.

### `2. Compare and Update Redraw.dyn`
- Embedded Python updated (node `61f7d7a83b4b4b8995410f7d195d6238`); graph structure,
  inputs and `OUT` wiring unchanged.
- **Baseline validation** now checks: `schema_version == "1.0"`,
  `snapshot_type == "REDRAW_BASELINE"`, required `redraw.filename` /
  `redraw.sidecar_filename` matching the actual files,
  `extraction_settings.boundary_location == "Finish"`, `merge_parameters` matching the
  graph's 6-parameter list when present, non-empty `stands`, and source project number
  vs Main's Project Information Number (mismatch → fatal; missing on either side →
  `IDENTITY WARNING` appended to the wired `issues` output and to `qa`).
- **Level check:** Main/Redraw/baseline level ids and names are carried through;
  `REDRAW_ONLY` geometry updates are skipped when Main's level differs from the
  baseline, Redraw's level differs from the baseline, or Main/Redraw levels differ.
  Parameter merging is unaffected.
- **Link robustness:** existing Redraw link types are found via
  `GetExternalFileReference().GetAbsolutePath()`, loaded with `RevitLinkType.Load()`
  when unloaded (outside any transaction), and existing instances are reused by type
  id; a new type + instance is created in one transaction only when absent.
  `add_link` returns `(instance, link_type_id)` so cleanup always knows the type id.
- Docstring corrected: geometry scope is unshared `Closed Wall - RX` / `Open Wall - RX`
  walls, **not** Room Separation Lines (the code always deleted/recreated walls).
- Unchanged: 6-parameter merge, `REDRAW_ONLY`-only geometry, wall eligibility rules,
  pre-move tag capture and recentering, single apply transaction
  `MVP2.4B6 - Merge Redraw Updates`, post-commit verification, late link removal,
  dry run when `Apply Updates` is false (saved graph defaults it to true).

### `AGENTS.md`
- Rewritten for the new save/sync rules, active-document outcomes, validation contract,
  level-skip behaviour and link reuse.

### Verification performed (no Revit available)
- Both `.dyn` files parse as JSON; CRLF line endings and escaped `\r\n` code preserved.
- Embedded Python extracted and `py_compile`-checked; embedded code matches the
  reviewed drafts byte-for-byte (after line-ending normalisation).
- Structural checks: boolean node/port/connector/input/view fully removed from graph 1;
  graph 2 node/connector counts unchanged.
- Graph 2 pure functions (`validate`, `level_mismatch`, `prel`, `desired`) exercised
  with mocked Revit modules: pass/fail cases for schema, boundary location, project
  number, level mismatches and parameter relations all behave as intended.
- Pre-edit copies of both `.dyn` files were backed up to the session temp folder
  (ephemeral; there is no git repo).

### Still unverified
- Everything that needs the Revit runtime: save/sync, detached opens, the cloud
  temp-central flow, link load/reuse, wall delete/create, room moves and tag
  recentering. See `ROADMAP.md` for the in-Revit test checklist.
