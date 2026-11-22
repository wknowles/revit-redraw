# Changelog

## 2026-11-22 — Phase 0 probe for the Redraw merge rebuild

- Confirmed API facts from the Revit 2027 documentation and one live Revit run,
  now recorded in `DESIGN.md`:
  - **`Room.IsPlaced` does not exist in Revit 2027.** `SpatialElement` exposes
    only `Area`, `Level`, `LevelId`, `Location` and `Number`. Graph 1 already
    falls back to the location-point check, so it was working, but the docs
    claimed otherwise; `DESIGN.md`/`CHANGELOG.md` now say where
    `placement_status` really comes from.
  - `SpatialElement.Number` **has a setter**, so a Room's number can be written
    directly as well as through its parameter. Both were confirmed writable in
    Revit 2027, which unblocks creating an added stand.
  - `SpatialElement.Location` is get-only; a room's location point is moved
    with `Location.Move`.
  - `Document.Create.NewRoom` takes a `UV`, not an `XYZ`.
  - `View.SetElementOverrides(ElementId, OverrideGraphicSettings)` applies a
    graphics override to a single element in a view.
  - `RevisionCloud.Create` takes the Revision id, so a revision cloud's colour
    comes from its revision and cannot be varied per cloud.
- Added `phase0/`, a diagnostic tool that is **not** part of the 1 → 2 workflow.
  It answers the runtime questions the Revit API documentation cannot answer
  before graph 2 is rebuilt around wall-level matching and a visual preview:
  - **A** does a per-element view override (`View.SetElementOverrides`)
    recolour an existing wall in a floor plan?
  - **B** do DirectShape surfaces render in a floor plan, and does
    `SetElementOverrides` accept a DirectShape?
  - **C** is a Room's `Number` writable (needed to create an added stand)?
  - **D** are room boundary curves trimmed to the room, or the full wall curve?
- `phase0/phase0_probe.py` is the source of truth; `phase0/Phase 0 Prototype.dyn`
  is generated from it by `python3 phase0/build_dyn.py` and verified with
  `python3 phase0/build_dyn.py --check`. The generated graph is CRLF JSON with
  the Python stored as escaped `\r\n`, like the two workflow graphs.
- The probe is safe by default: all diagnostics run inside a transaction that is
  rolled back. The optional visible test commits one transaction and is undone
  with a single Ctrl+Z; a later run also clears the previous probe elements.
- **Probe hardened after the first run.** The first version aborted everything
  when it found no wall-bounded room, and swallowed the reason. It now:
  - inventories every room's boundary at **both** `Center` and `Finish` and
    reports what the boundary is made of (Walls, Room Separation Lines, other),
    so a model bounded by separation lines is diagnosed instead of silently
    returning nothing;
  - keeps going when there is no wall, so questions A, C and E still run;
  - no longer leaves `shape` unbound when DirectShape creation fails;
  - no longer relies on `Room.IsPlaced`, which does not exist in Revit 2027.
- Added `phase0/test_probe.py`, an offline harness that stubs the Revit API and
  drives the probe through wall-bounded, non-wall-bounded, unplaced and empty
  models. Its mock matches Revit 2027 (no `IsPlaced`, writable `Number`), so it
  catches API mistakes the first version of the mock hid. It checks the
  probe's control flow only, not whether an API call is correct against Revit.
- Docs updated (`AGENTS.md`, `DESIGN.md`, `phase0/README.md`). Questions **A**,
  **B** and **D** are still open - they need a model with real walls.

## 2026-11-21 — Centerline boundaries, clean names, Main-active workflow

- **Graph 1 boundary extraction** now uses `SpatialElementBoundaryLocation.Center`
  (wall centerline); new baselines write `extraction_settings.boundary_location:
  "Center"`. `area_internal_sqft` still comes from `Room.Area`; loops, perimeter and
  centroid are centerline. No model-level room area setting is changed.
- **Graph 2** reads `boundary_location` from the baseline and uses it for Main,
  Redraw and post-apply verification. `"Center"` and legacy `"Finish"` are accepted,
  so old baselines still work.
- **Active-document workflow** is now consistent in intent: graph 1 saves/closes the
  Redraw and leaves Main active for every source type. Local non-workshared uses
  `Application.CopyModel` so Main is not activated away; cloud/workshared paths
  reactivate Main and close/delete temp documents after the Redraw is written. If
  cleanup fails, the summary is `SUCCESS WITH WARNINGS` and QA names the manual step.
- **Intended user flow:** close Main, open the Redraw to edit, save/close it, reopen
  Main, then run graph 2.
- **Graph 2 guards** now fail if the active document is the Redraw or if the Redraw
  RVT is still open.
- **Graph names** are now `Create Redraw` and `Compare and Update Redraw`; MVP prefixes
  removed from the embedded docstring titles. Transaction names retain their existing
  MVP labels.
- Docs updated (`AGENTS.md`, `DESIGN.md`, `ROADMAP.md`). Static JSON/`py_compile`
  checks pass; active-document and cloud cleanup behaviour still need Revit testing.

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
- Placement status now comes from the room's location point (`Room.IsPlaced` does
  not exist in Revit 2027 - `SpatialElement` exposes only `Area`, `Level`,
  `LevelId`, `Location` and `Number`); unplaced and placed-but-unbounded
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
