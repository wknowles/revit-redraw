# Changelog

## 2026-11-22 — Wall level merge, visual preview and audit trail

- Graph 2 failed on its first run after the rebuild, and the reason was
  invisible: **the QA watch was never wired**. `OUT = (summary, pout, gout,
  issues, qa)` only wired indices 0-3, so `qa` - which carries the fatal
  message - went nowhere. The Summary only said `Status: Failed`.
- Fixed, and the same class of problem cannot recur:
  - **Graph 2's QA is now wired** to a watch (`OUT[4]`), and the watch labels
    were corrected: `Parameter Conflicts` is really `Conflicts / Skips` and
    `Errors` is really `Geometry Updates`.
  - **The Summary now carries the error and the traceback**, so a failure is
    diagnosable from the Summary watch alone.
  - **`tools/check_calls.py`** compares every call against the signature of the
    function it calls. It found three wrong argument counts in the new code -
    `component_level`, `preview_height` and `draw_preview` - one of which was
    the actual failure. The check is now part of `tools/check_repo.py`.
  - **`src/test_graph2_plan.py`** drives the planning with stand, wall and curve
    fakes, so the glue between the Revit data and the merge core is tested
    offline. It found a real bug: `Redraw Wins On Conflicts` overrode a
    *wall level* conflict but not a *stand level* one, so a stand where both
    sides changed differently was still skipped.


- Graph 2 is rebuilt around **wall level** comparison instead of room level, so
  a cluster of connected stands merges as one change:
  - a Main wall that already sits where a Redraw wall sits is **kept**;
  - a Main wall with no Redraw counterpart is **deleted**;
  - a Redraw wall with no Main counterpart is **created**;
  - a wall whose type, height, offset or flip changed is **rebuilt**;
  - a wall added to Main after the Redraw was cut is **left alone**.
- Stands the Redraw **added** are created (walls first, then the Room, then its
  Number and the six merged parameters), and stands the Redraw **deleted** are
  deleted. Revit auto-numbers new rooms, so an added stand's Number is set
  explicitly.
- The stands that share a wall are clustered, and the affected set turns out to
  be closed over shared walls already - a shared wall that moves changes both
  neighbours, so no separate clustering algorithm is needed.
- `ALREADY` is now distinguished from `CONFLICT`: when Main already is what the
  Redraw wants there is nothing to do, and that is no longer reported as a
  conflict.
- The business rules still hold: only `Closed Wall - RX` and `Open Wall - RX`
  walls may be rebuilt, a boundary that is not made of walls is skipped, and
  level mismatches are skipped.
- **Visual preview.** A dry run now draws the difference on the plan:
  - **blue** - the Redraw's new wall positions, as filled `DirectShape` bands;
  - **amber** - the Main walls the merge will rebuild or delete;
  - **red** - the Main walls Main changed itself, which are left alone.

  The two Main colours recolour the existing walls with
  `View.SetElementOverrides`, so they need no temporary geometry; only the
  Redraw's new positions need a band. The preview is removed by the next run.
- **Audit trail.** Every run appends to `<title>.merge-log.csv` and rewrites
  `<title>.merge-summary.csv` next to the Redraw - flat CSV, one row per change,
  for business users.
- Graph 2 gained a fifth input, `Redraw Wins On Conflicts` (default false).
  With it off, a cluster where Main and the Redraw disagree is blocked and
  reported; with it on, the Redraw's version wins and the overwrite is recorded.
- `src/merge_core.py` grew the component planner, and the tests grew to 25,
  including the business case: four stands around a shared cross of walls, where
  moving one wall must rebuild that wall once and leave the other seven alone.

## 2026-11-22 — Phase 0 answered; merge core built; four live bugs fixed

- **All four Phase 0 questions are answered**, on a test model and on a live
  production model:
  - **A** `View.SetElementOverrides(ElementId, OverrideGraphicSettings)` with
    `SetCutForegroundPatternColor` **does** recolour an existing wall in a
    floor plan, and reads clearly.
  - **B** a `GeometryCreationUtilities.CreateExtrusionGeometry` band in
    `Generic Annotations`, coloured with the same override, **renders filled**
    in a floor plan — not as an outline.
  - **C** a Room's `Number` is writable three ways: the `SpatialElement.Number`
    property (it has a setter), `LookupParameter('Number')` and
    `BuiltInParameter.ROOM_NUMBER`.
  - **D** room boundary curves are **not** trimmed to the room:
    `BoundarySegment.GetCurve()` returns the full wall location curve, and a
    wall shared by two rooms carries the identical curve in both rooms' segment
    lists. So baseline curves can be compared with Main's wall curves exactly,
    and a shared wall de-duplicates by curve.
- Because the override recolours the *existing* wall, the preview needs no
  geometry for the "Main changed" and "conflict" halves — only the Redraw's new
  positions need a band.
- Fixed the problems the first live run of the rebuilt graph 2 found:
  - **The removed rooms are now deleted before the walls.** Deleting a shared
    wall while both rooms still exist puts two rooms in one region, and Revit
    answers that with a modal warning; clicking "Delete Room" in it edits the
    document outside the merge's transaction, which broke the run with
    "Modification of the document is forbidden".
  - **No regeneration after the commit.** Committing regenerates by itself,
    and regenerating outside a transaction is refused once anything else has
    touched the document.
  - **The plan now sees the Redraw's walls that no longer bound a room.**
    Deleting a stand in the Redraw leaves its walls behind as orphans. The
    plan only looked at walls that bound a room, so those orphans looked
    deleted and would have been taken out of Main - a wall the designer kept
    would have been destroyed. The Redraw's walls are now inventoried, and any
    that match a baseline wall of the cluster count as part of it.
  - **The preview bands every wall the merge touches**, not only the Redraw's
    new positions, so it is visible even where recolouring a wall does not
    take. It also reports how many bands it drew and how many walls it
    recoloured, and any failure, instead of counting what it planned to do.
- Fixed the second round of live run failures:
  - **The preview needed a transaction.** Creating the `DirectShape` bands and
    recolouring the walls both modify the document, so the preview was refused
    with "Attempt to modify the model outside of transaction".
  - **A deleted stand was treated as a moved one.** The room loop fell through
    to the "move" branch for a stand that had already been deleted, which
    failed the cluster.
  - **Removing a wall that separates two stands which both keep a room is now
    refused.** That would put both rooms in one region, and Revit's answer to
    that is a modal warning that edits the document outside the merge's
    transaction - which is what broke the run and left the model needing two
    undos.
  - **Undo is one step again.** The Redraw link is now removed inside the merge
    transaction, so undoing the merge undoes the lot. The verification compares
    against a snapshot of the Redraw's geometry taken as plain points, so it no
    longer needs the link to be alive.
- Full review after the third live run, against the Revit 2027 documentation:
  - **The Redraw link is now always reloaded.** `RevitLinkType.Load()` "loads or
    reloads", but only after the link is unloaded, and the old code reused an
    already loaded link. The merge could therefore run against a Redraw that
    was stale on disk - which would explain a plan that did not match the
    Redraw the operator had just edited.
  - **A stand with no boundary in the Redraw is now treated as removed.** A
    room that is still there but bounds nothing is not a stand any more, so
    its room is taken out of Main too.
  - **The QA now says where every stand of a skipped cluster actually is** -
    baseline, Main and Redraw, with its area and how many boundary walls it
    has - so the operator can see whether the Redraw really lost the stand.
  - **The preview shows skipped clusters** in magenta instead of showing
    nothing, and the guard's message now says what to do about it.
  - `Preview: NOT DRAWN` in apply mode now reads "not drawn in apply mode".

- Added `HANDOFF.md`: a working snapshot of the graph 2 rebuild - the design,
  the confirmed Revit 2027 API facts, the bugs found, where the work stands and
  what is next. A working note for the current effort, not a product document.

- Fixed the last live run finding: **a new stand's room was never created.**
  The cluster failed with the reason "0", which is `str(KeyError(0))` - the
  Redraw's parameters are a dict of status and value, and the new code read
  them like a pair. Only the six merged parameters that have a value are
  copied now.

- Fixed the fourth round of live run findings:
  - **The QA watch was never fed.** The `List.GetItemAtIndex` that feeds the QA
    watch had its index connected but not its list, so the watch showed
    nothing. The Python output is now connected to it.
  - **`Apply Updates` now defaults to false** and **`Redraw Wins On Conflicts`
    now defaults to true**, as asked for.
  - **`Wall.Create` wants an `ElementId` for the level**, and the component
    level was a plain int - "No method matches given arguments for
    Wall.Create". It is an `ElementId` now.
  - **A cluster of stands that are all new in the Redraw had no level**,
    because it has no stand in Main to take one from - "levelId is null". The
    level now comes from the Redraw's level name.
  - **A new stand is placed more carefully**: the Redraw's room point is tried
    first and its centroid second, and a failure now says "The new stand is
    not in an enclosed region" instead of Revit's bare message.
  - **One Ctrl+Z now takes the preview off the plan.** In a dry run the Redraw
    link is removed before the preview is drawn, so the preview is the last
    transaction. The QA also says that reloading the link clears the undo
    history from before the run.

- `src/test_graph2_plan.py` grew to 17 tests, covering the shared wall guard,
  the case where the wall between two stands is removed while one of them is
  deleted, a stand that has no boundary left in the Redraw, the report of
  where every stand of a skipped cluster is, and reading a stand's parameters.
  the case where the wall between two stands is removed while one of them is
  deleted, a stand that has no boundary left in the Redraw, and the report of
  where every stand of a skipped cluster is.
  and the case where the wall between two stands is removed while one of them
  is deleted.

- `src/test_graph2_plan.py` grew to 12 tests, including the reported case: a
  separating wall removed in the Redraw with one of the two stands deleted.
  Two more bugs came out of it - the Redraw's wall inventory could add a wall
  twice, and a stand level conflict was not overridden by "Redraw wins".

- Added `src/`, the rebuilt merge logic:
  - `src/graph2.py` is the source of truth for the Python inside
    `2. Compare and Update Redraw.dyn`. `src/build_graph2.py` injects it into
    the graph and leaves the node layout alone, so the graph stays diffable.
  - `src/merge_core.py` is the **pure Python** wall matching and clustering
    engine — no Revit imports, so it is unit tested offline by
    `src/test_merge_core.py` (16 tests). It covers curve distance, wall
    matching (unchanged, moved, added, removed, one-to-one, long moves),
    shared-wall detection and cluster grouping.
  - Straight walls are compared by their ends, which is exact and O(1):
    matching 2000 walls went from 6.9 s to 0.01 s.
  - Graph 2 gained a fifth input, `Redraw Wins On Conflicts` (default false).
- Added `tools/`, the repository's static checks. `python3 tools/check_repo.py`
  runs them all:
  - the `.dyn` files are valid CRLF JSON with compiling embedded Python;
  - the generated graphs match their sources;
  - the phase 0 probe survives its stubbed Revit models;
  - the merge core passes its unit tests;
  - **every Revit API call in the repository matches the Revit 2027
    documentation**. `tools/revit_api_2027.json` is a cached index of the
    documented API, refreshed with `python3 tools/build_api_index.py`.
- The API audit found **four live bugs**, all of which failed silently because
  the calls were wrapped in `except` blocks:
  - **`WorksharingUtils.GetCentralModelPath(Document)` does not exist in Revit
    2027.** Graph 1 used it three times. It now uses
    `Document.GetWorksharingCentralModelPath()`. Before: `is_central()` always
    returned False, the baseline recorded an empty `central_display_path`, and
    workshared models silently fell back to the temp-central flow.
  - **`Room.PhaseCreated` / `Room.PhaseDemolished` do not exist in Revit 2027.**
    Graph 1 wrote `phase_created` and `phase_demolished` as always null. It now
    uses `Element.CreatedPhaseId` / `Element.DemolishedPhaseId`.
  - **`Room.IsPlaced` does not exist in Revit 2027.** Graph 1 was already
    falling back to the location-point check; that fallback is now the code.
  - **`ElementId.IntegerValue` was removed** in Revit 2024+. The dead fallback
    in all three files is gone; `ElementId.Value` is used directly.
- The phase 0 probe also used **`BuiltInCategory.OST_GenericAnnotations`**, which
  does not exist in Revit 2027 — the category is `OST_GenericAnnotation`
  (singular) — and **`Extrusion.Create`**, which was replaced by
  `GeometryCreationUtilities.CreateExtrusionGeometry`.
- Confirmed API facts from the Revit 2027 documentation and live Revit runs,
  now recorded in `DESIGN.md`:
  - **`Room.IsPlaced` does not exist in Revit 2027.** `SpatialElement` exposes
    only `Area`, `Level`, `LevelId`, `Location` and `Number`.
  - `SpatialElement.Location` is get-only; the location point is moved with
    `Location.Move`.
  - `Document.Create.NewRoom` takes a `UV`, not an `XYZ`. Revit **auto-numbers**
    a new room from the sequence, so an added stand must set its Number.
  - `XYZ` has **no `Length` property** — use `GetLength()`; its operators are
    not dependable from PythonNet.
  - `RevisionCloud.Create` takes the Revision id, so a revision cloud's colour
    comes from its revision and cannot be varied per cloud.
- Added `phase0/`, a diagnostic tool that is **not** part of the 1 → 2 workflow.
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
