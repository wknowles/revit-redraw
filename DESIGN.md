# Design — Redraw workflow (current state)

Reference for future features. Runtime facts and editing rules live in `AGENTS.md`;
this file describes *how the workflow works and why*. Change history: `CHANGELOG.md`.
Deferred work and known gaps: `ROADMAP.md`.

## 1. Purpose and run order

1. `1. Create Redraw.dyn` — freeze the current Main: save/sync it, extract a baseline
   JSON of every numbered Room (stand), and produce a **detached, non-workshared**
   Redraw RVT in a chosen folder. Designers edit the Redraw freely.
2. `2. Compare and Update Redraw.dyn` — run against Main with the Redraw folder:
   three-way compare (baseline vs Main vs Redraw), merge Redraw-only parameter and
   geometry changes back into Main, verify, then close the Redraw document it opened
   for reading. Nothing is linked into Main, so one Ctrl+Z undoes the merge, and
   each geometry cluster is applied in a sub-transaction, so a cluster that fails
   is rolled back while the rest of the run stays applied.

Room Number is the stand key and is assumed **globally unique** (business decision).
Duplicates are errors in graph 1. Graph 2 quarantines the geometry of a duplicate
stand's cluster and treats duplicate and blank-Number Rooms as unmanaged. The
duplicated stand's parameters stay as Main has them; parameter writes for other
stands in the cluster are independent of the geometry skip.

After graph 1 the Redraw is saved and closed and Main is active. The intended user
flow is: close Main → open the Redraw → edit/save/close → reopen Main → run graph 2.

## 2. Graph 1 — Create Redraw

Single input: `Directory Path`. `OUT = (summary, qa)`; code block `0;1` feeds the
Summary and QA watches. QA is watch-only by decision — no QA sidecar file.

### Preflight (before any save)
- Active document must be a project (not family).
- Directory must exist; target `<title>.rvt` and `<title>.baseline.json` must not
  already exist; folder must contain no other primary `.rvt` (backups `*.NNNN.rvt`
  ignored) and no other `*.baseline.json`.

### Save/sync Main (`sync_main`)
| Source | Action | `main_save_status` |
|---|---|---|
| Local, non-workshared | `doc.Save()`; error if never saved (`PathName` empty) | `SAVED` |
| Cloud, non-workshared | `doc.SaveCloudModel()` | `CLOUD_SAVED` |
| Workshared local (not central) | `SynchronizeWithCentral`, `RelinquishOptions(False)` (keep ownership), `SaveLocalAfter=True` | `SYNCED` |
| Central opened directly | `Save()` / `SaveCloudModel()` if modified | `CENTRAL_SAVED` / `CENTRAL_NO_CHANGE` |

Afterwards `doc.IsModified` must be false, else abort. Nothing is relinquished —
the operator keeps their worksets/ownership.

### Baseline extraction (`extract`)
- Boundary location **Center** (wall centerline), coordinates/measurements in Revit
  internal feet, rounded to 9 decimals. Lines and arcs stored natively (`LINE`,
  `ARC`); anything else tessellated to `POLYLINE` with max chord 1/12 ft (1 inch).
  `area_internal_sqft` still comes from `Room.Area` (Revit's room area computation);
  loops, perimeter and centroid are centerline.
- Every numbered Room is retained, including unplaced and placed-but-unbounded ones
  (`placement_status` from the room's location point, `has_geometry` flag). Blank numbers are
  skipped with a warning; duplicate numbers and extraction exceptions are ERRORs and
  abort the run.
- Per stand: `element_id`, `unique_id`, audited `parameters`, placement/geometry
  flags, `area_internal_sqft`, `perimeter_internal_ft`, `centroid_internal_ft`
  (signed-area centroid over loops, holes negative; falls back to room location
  point), `loops` of curves, plus metadata: `level_id`, `level_name`,
  `phase_created`, `phase_demolished`, `design_option`, `workset`.
- Audited parameters (9): `Corners`, `Floorplan Hall`, `Floorplan Pavilion`,
  `Floorplan Sector`, `Obstruction Area`, `Product Code`, `Custom Area`,
  `Stand Length`, `Stand Width`. Values read as text (`AsString`, fallback
  `AsValueString`), with `PARAMETER_NOT_FOUND` / `NO_VALUE` / `READ_ERROR` statuses.

### Detached copy / active-document outcome (`save_detached`)
| Source | Flow | `redraw_mode` | End state |
|---|---|---|---|
| Local non-workshared | `Application.CopyModel` to target; fallback `SaveAs` if copy fails | `COPIED` / `SAVED_COPY` | Main active, Redraw closed |
| Cloud non-workshared | `SaveAs` local target, reactivate Main, close target | `CLOUD_SAVED` | Main active, Redraw closed (best effort) |
| Local workshared | open central detached (`DetachAndDiscardWorksets`), `SaveAs` target, close | `DETACHED_LOCAL` | Main active, Redraw closed |
| Cloud workshared / local fallback | `SaveAs` temp local central (`SaveAsCentral=True`) → `Application.CopyModel` → open detached → `SaveAs` target → reactivate Main → close/delete temp | `DETACHED_TEMP_CENTRAL` | Main active, Redraw closed, temp deleted |

Why the cloud detour: `DetachFromCentralOption` is invalid for cloud model paths and
`Application.CopyModel` does not accept cloud paths, so the cloud model must first be
saved down to a local central.

After the target exists, `leave_main_active` uses
`UIApplication.OpenAndActivateDocument` to bring Main back, then closes the previous
Redraw/temp document and deletes temp files. If activation or cleanup fails, the
summary reports `SUCCESS WITH WARNINGS`, `main_active`/`redraw_closed`/
`temp_central_cleaned` are false, and QA names what to close manually.

The baseline is written to `<sidecar>.pending` **before** the copy and renamed into
place only after the target exists; both files are then size-verified. On failure the
pending file is removed and QA warns that a partial Redraw may need deleting.

### Baseline JSON contract (`schema_version "1.0"`)
Top level: `schema_version`, `snapshot_type: "REDRAW_BASELINE"`, `baseline_id` (UUID),
`created_utc`, `redraw`, `source`, `canonical_units`, `project_display_units`,
`extraction_settings`, `counts`, `stands` (keyed by Room Number).
- `redraw`: `filename`, `sidecar_filename`, `output_type` (`LOCAL`/`LOCAL_DETACHED`),
  `is_workshared: false`, `detached_from_central` (true iff source was workshared).
- `source`: `document_title`, `source_display_path`, `central_display_path`,
  `model_type` (`LOCAL`/`WORKSHARED`/`CLOUD`/`CLOUD_WORKSHARED`), `project_name`,
  `project_number`, `revit_version`, `is_cloud_model`, `is_workshared`.
- `extraction_settings`: `boundary_location: "Center"` (legacy `"Finish"` accepted
  by graph 2), decimal counts, tessellation chord, `audited_room_parameters` (9),
  `merge_parameters` (6).
- Written with `json.dump(..., indent=2, sort_keys=True, ensure_ascii=False)`.

## 3. Graph 2 — Compare and Update

Inputs: `IN[0]` Redraw directory, `IN[1]` tolerance metres (default 0.0005, and
**0.0005 in the saved graph**), `IN[2]` boundary sample spacing metres (default
0.25), `IN[3]` `Apply Updates` (default False in code and **false in the saved
graph**), `IN[4]` `Redraw Wins On Conflicts` (default False in code, **true in
the saved graph**). Metres are converted to internal feet via `UnitUtils`.
`OUT = (summary, pout, gout, issues, qa)`; all five are wired to watches.

On the tolerance: it does not limit how closely a rebuilt wall follows the
drawing, because a created or replaced wall is built from the Redraw's own
`Curve`. It decides whether an existing Main wall is "already the same wall"
(kept where Main has it) or is replaced, and whether a Main wall counts as
unchanged since the Redraw was cut. Untouched walls are bit-identical - the
Redraw is a copy of Main - so the tolerance only has to separate identical from
edited. At the old 10 mm a wall 9 mm out stayed in Main's position and moved the
area; 0.5 mm makes a touched wall be replaced. Straight walls compare by
endpoints, so this costs nothing; curved walls match through 17 sampled points
per curve, so their resolution is coarser but an unchanged arc still matches
exactly.

`check_inputs` rejects a blank directory, a tolerance that is not a number or is
negative, and a spacing that is not a positive number, each with a message that
names the input. A coarse spacing only warns, and only when it exceeds both
`8 x tolerance` and the shipped 0.25 m, so the default run is quiet.

### Pipeline
1. **Files/active document**: exactly one primary `.rvt` + one `*.baseline.json` in
   the folder; fail if the active document is the Redraw or the Redraw RVT is open.
2. **Validate** (`validate`): schema/snapshot, required filenames matching the actual
   files, `boundary_location` `"Center"` or `"Finish"` (returned for use by
   `collect`), `merge_parameters` equal to the graph's list when present, non-empty
   `stands`, project number match against Main's Project Information Number.
   Mismatch -> fatal; missing on either side -> `IDENTITY WARNING` in `issues`
   (wired) and `qa`.
3. **Read the Redraw** (`open_redraw`): `Application.OpenDocumentFile` opens the
   Redraw as a separate document; Main is brought back to the foreground, and the
   document is closed again in `finally`. There is **no Revit link**: loading one
   clears Revit's undo history, and a link type created before the merge can never
   be removed by the same undo that removes the merge, so a clean one-step undo is
   impossible with a link. No transform is needed either: graph 1 writes the Redraw
   as a copy of Main (`Application.CopyModel`, or a detached `SaveAs` of central),
   so the Redraw's internal coordinates *are* Main's.
4. **Collect**: three-way states - baseline (`bases`), Main, Redraw - at the
   baseline's boundary location. Each state has parameters, boundary loops, wall
   descriptors (type/level/height/offset/flipped/curve), centroid, room point and
   level id and name. The Room Tag types and views already in the model are read
   now (`room_tag_templates`), before anything is merged.
   A blank-Numbered Room is skipped and a duplicate Number is quarantined.
   `collect` keeps the first Room for a duplicated Number; `plan_clusters` skips
   any cluster that contains it, and `collect_exclusions` reads the unmanaged
   Rooms' boundaries so every wall they share is protected from deletion.
5. **Classify** every stand (`stand_status`): `UNCHANGED`, `ALREADY`, `CONFLICT`,
   `AFFECTED`, `ADDED`, `REMOVED`, `MAIN_ONLY`, `MAIN_REMOVED`, `BOTH_REMOVED`.
   `ALREADY` means Main already is what the Redraw wants - that is not a conflict.
   `ALREADY` also requires the wall attributes to match, so a wall that changed
   type/height/offset/flip/level without moving is `AFFECTED`. `BOTH_REMOVED`
   means neither Main nor the Redraw has a stand the baseline did; `MAIN_REMOVED`
   is reported in `issues` because Main lost a stand the Redraw still draws.
6. **Cluster** the stands: two stands are neighbours when they share a wall, in
   Main or in the Redraw (`state_wall_usage` -> `adjacency_from_wall_usage` ->
   `find_components`). The affected set is already closed over shared walls, so a
   cluster needs no separate algorithm.
7. **Plan** each affected cluster (`plan_component`, in `src/merge_core.py`):
   keep / replace / delete / create the walls, and list the room work - move the
   location point, create an added stand, delete a removed stand. Every stand of
   the cluster contributes its walls, not only the affected ones, so a wall shared
   with an unchanged neighbour is matched instead of duplicated. Walls that Main
   added after the Redraw was cut are retained; walls Main removed are conflicts.
   A cluster with a conflict is skipped unless `Redraw Wins On Conflicts` is set.
   When the Redraw wins and a conflicted stand shares no wall element id with its
   Redraw copy, the stand was re-created there (same Number, new walls), so all
   of its Main-only walls are deleted with the old geometry instead of being
   retained as later Main work. The planner then applies the gates below before
   anything is touched.
8. **Preview** (dry run, one transaction `Redraw Merge Preview`, which also
   clears the previous preview) or **Apply** (two transactions: `Redraw Merge -
   Clear Preview`, then `MVP2.4B6 - Merge Redraw Updates`): delete the walls to
   go, create the walls to come, then create, delete and move the rooms, tag the
   added stands, and write the parameters. Each geometry cluster is applied by
   `apply_cluster` in a `SubTransaction` of the merge transaction: every edit of
   that cluster commits together, or a failure rolls the whole cluster back and
   marks it `FAILED`, while the other clusters still commit. Parameter writes run
   after the geometry clusters in the same merge transaction and are independent of
   the cluster plan. A `REDRAW_ONLY` parameter is written even when its cluster is
   skipped or its geometry fails. The exception is a duplicated Room Number, whose
   parameters are not updated. Every `Commit()` is
   checked against `TransactionStatus.Committed` (preview, preview clear, each
   cluster, the merge), and a commit that does not return `Committed` aborts the
   run. After the merge commit, re-collect Main and verify each cluster against
   the Redraw.
   Undo is one Ctrl+Z on the merge transaction, so the applied clusters go away
   together; the preview-clear transaction commits first, so undo does not bring
   the bands back. A cluster that rolled back was never applied, so undo does not
   have to remove it.
9. **Close the Redraw** (`close_redraw` in `finally`): the document is closed
   whatever happened, and Main is activated again.
10. **Audit trail**: see the section below.

### Safety gates in the planner
`plan_clusters` skips a cluster, records the reason on the plan entry and appends
each stand's state to the QA, when any of these hold:

| Gate | Why |
|---|---|
| Duplicate Room Number in the cluster | the Number key is ambiguous, so the whole cluster geometry is quarantined; the duplicated stand's parameters are not updated, but other stands in the cluster can still receive parameter updates |
| Conflict and `Redraw Wins On Conflicts` is false | Main and the Redraw changed the same walls differently |
| Room level mismatch (Main or Redraw vs baseline) | the stand is on a different level, so wall rebuilds would land in the wrong place |
| Main or Redraw boundary is not made of walls | Room Separation Lines and non-wall boundaries are not supported |
| Main wall to delete/replace is not RX, or a Redraw wall to create is not RX | only the two RX wall types may be rebuilt |
| A created/replaced wall's type is missing in Main | the apply deletes before it creates, so a type that cannot resolve must fail here, not halfway |
| A created/replaced wall's level is missing in Main | same reason; a wall's own level wins, the cluster level is only the fallback |
| A Main wall to delete/replace hosts a `FamilyInstance` or `Opening` | Revit cascades doors/windows/openings with the wall and the merge cannot rebuild them, so the cluster is left alone |
| The wall still separates two surviving stands, or a stand and an unmanaged Room | deleting it would put both Rooms in one region. A stand whose Room moved away in the Redraw has vacated its side, so a wall it shared is allowed to go |

A `Location.Move` that Revit refuses (returns false) raises, so that cluster rolls
back instead of silently staying put.

### Sold stands
A sold stand must not be resized by the designer, so the merge refuses rather than
applying a partial result. `Status` is a Room parameter that the CRM plugin owns:
it lives in **Main only** (the Redraw has no such parameter) and the plugin
deletes it when the model is saved and re-imports it about two minutes later.

- `state()` reads `Status` from Main and normalises it (case, spaces, underscores,
  hyphens). Only `sold` blocks; `OnHold`, `Available` and a blank do not.
- `status_present(m)` is a document-wide check: the parameter is on every Room or
  none. If it is absent, a preview reports `SOLD STATUS UNAVAILABLE` in `issues`
  and the Summary, and an apply raises `Refused` with the wait-and-rerun message.
- `sold_check` examines every stand in a cluster the planner would apply - not only
  the cluster's `affected` stands, because the plan reconciles every wall of the
  component, so a sold *neighbour* of a moved stand, or a sold stand Main edited
  after the Redraw was cut, is rebuilt too. A cluster skipped for another reason
  changes nothing, so a sold stand in it cannot block the run. Per sold stand:
  - the Redraw deletes it -> blocker, because the area would go to zero;
  - the Redraw's area differs from Main's by more than
    `area_allowance = max(0.001 m2, perimeter x tolerance)` -> blocker;
  - the area is within the allowance but the perimeter moved by more than it ->
    `SOLD STAND DIMENSIONS` warning in `issues`, which makes the run
    `SUCCESS WITH WARNINGS` but does not block.
- A blocker raises `Refused` before the merge transaction opens, so nothing is
  written and no cluster is half-applied. The Summary status is `REFUSED`, the
  message names every stand and says the Redraw or the CRM decision has to
  change, and the audit log records one `REFUSED` row. There is no traceback: a
  business rule refused the run, it did not crash.
- A dry run writes nothing, so instead of refusing it marks each blocked cluster
  `SKIPPED` with the reason, which also draws it magenta in the preview.
- Defence in depth: inside the merge transaction, after the clusters and the
  parameter writes, `doc.Regenerate()` runs and `merge_sold_drift` compares each
  checked sold stand's live `Room.Area` with the Redraw's. Any drift rolls the
  whole merge transaction back and raises `Refused`.

### Statuses, the summary and the audit trail
`judge_run` sets `Summary['Status']`: `COMPLETED WITH ERRORS` if any change is
`FAILED`/`VERIFY_FAILED`, otherwise `SUCCESS WITH WARNINGS` if any change was
skipped, any issue is a conflict, or any issue is a sold-stand warning, otherwise
`SUCCESS`. `Summary['Message']` carries the first skip/conflict/sold reason (or a
failure count), so the always-visible watch says why. `Summary['Audit']` is `OK`,
`FAILED` or `SKIPPED` (no folder to write to). A run refused by a business rule
ends with the top-level status `REFUSED`, which is separate from `FAILED`
(crash) and `COMPLETED WITH ERRORS` (change-level failures). Skips stay visible
in `gout` with their reason, so a dry run does not report a blocked cluster as
mergeable. `Summary` also carries `Sold status` (`PRESENT`/`MISSING`),
`Sold stand blocks` and `Sold stand warnings`.

Every run writes two CSVs next to the Redraw (both gitignored). The log is
appended and the summary is rewritten:

| File | Written | Columns |
|---|---|---|
| `<title>.merge-log.csv` | append, one row per cluster plus one `change=PARAMETER` row per parameter decision | `run_utc, run_id, mode, cluster, stands, change, status, walls_replaced, walls_created, walls_deleted, params_written, tags_moved, details` |
| `<title>.merge-summary.csv` | rewritten, one row for the run | `run_utc, run_id, mode, clusters_merged, clusters_skipped, clusters_moved, clusters_resized, clusters_added, clusters_removed, walls_replaced, walls_created, walls_deleted, params_written, tags_moved, conflicts` |

`clusters_moved` counts clusters where a Room's location point moved by more than
the tolerance; `clusters_resized` counts clusters whose boundary changed around a
fixed point. A cluster with added or removed stands is `ADDED`, `REMOVED` or
`MIXED` instead.

`mode` is `APPLIED` or `PREVIEW`. A fatal run writes one `change=RUN FAILED` row
with the exception text when it can locate the folder; if it cannot, `Audit` is
`SKIPPED`. A failed write is reported in QA and sets `Audit` to `FAILED`.

### The preview
A dry run draws the difference on the plan instead of only describing it:

| Colour | What |
|---|---|
| Blue | the Redraw's new wall positions, as filled `DirectShape` bands |
| Amber | the Main walls the merge will rebuild or delete |
| Red | the Main walls Main changed itself, which are left alone |

The two Main colours are drawn by recolouring the existing walls with
`View.SetElementOverrides`, so they need no temporary geometry. Only the Redraw's
new positions need a band. Each band carries the marker `REDRAW MERGE PREVIEW
v=<view> w=<walls>` in its element name (`DirectShape.SetName`) and Comments, and
the next run removes it by marker and then sweeps every view's overrides for the
preview colours, so an unmarked band from an older run cannot survive.

## 4. Decisions and rationale (business + technical)

### The merge unit is a wall, not a room

The rebuild in `src/` changes the unit of comparison from a Room to a **Wall**.

In the old room-level merge a stand was compared and rebuilt as a monolith: if the
stand changed, its entire boundary was deleted and recreated. That is why only an
**isolated** stand could merge — a shared wall cannot be half-rebuilt — and why a
stand that merely grew lost the doors and joins on the walls that did not move.

Comparing walls instead:

- a Main wall that already sits where a Redraw wall sits is **kept**;
- a Main wall with no Redraw counterpart is **deleted**, but only if no stand
  outside the current change still needs it;
- a Redraw wall with no Main counterpart is **created**;
- a wall shared by two stands is **one wall**, matched once, so a cluster of
  connected stands merges as one change.

**Clusters need no separate algorithm.** If a shared wall moves in the Redraw,
both neighbours' boundaries change, so the set of stands whose Redraw differs
from the baseline is already closed over shared walls — it *is* the cluster.
The one exception is deletion, handled by the "only delete if every stand that
binds it is in the change set" rule.

`src/merge_core.py` holds this logic as pure Python, so it is unit tested
offline. Straight walls are matched by their ends, which is exact and O(1).

### Other decisions

- **Always save/sync Main first** — the Redraw must contain the operator's latest
  work; the old optional boolean was misleading and is gone.
- **Detached, non-workshared Redraw** — designers edit without worksharing overhead;
  `DetachAndDiscardWorksets` keeps the copy clean.
- **No Revit link in graph 2** — the Redraw is read from a document opened on the
  fly and closed again. Loading a link clears Revit's undo history, and the link
  type can never be removed by the same undo that removes the merge, so a link can
  never give the one-step undo the operator wants. The Redraw is a copy of Main, so
  its internal coordinates are Main's and no transform is needed.
- **Tags follow the model's own tags** — a stand the Redraw added gets a Room Tag in
  every view that already carries Room Tags, with the type that view uses, on the
  Room's location point; a moved stand's tags are re-centred in every view that has
  them, not just the active view.
- **Centerline boundaries** — graph 1 extracts at `SpatialElementBoundaryLocation.Center`
  so Redraw geometry follows wall centerlines; graph 2 honors the baseline location
  for Main, Redraw and verification. Model-level room area settings are not changed.
- **Main active, Redraw closed after graph 1** — the user closes Main, opens the
  Redraw, edits/saves/closes it, reopens Main and runs graph 2. Graph 1 attempts this
  for every source type and reports cleanup warnings instead of silently leaving a
  temp central active.
- **Room Number as global key** — agreed unique across levels/phases; metadata
  (level/phase/option/workset) is recorded for auditing and the level gate, not as
  part of the key.
- **6 merged parameters; 3 audit-only** — business rule; keep both graphs' lists in
  sync only if the rule changes. Graph 2 fails on a mismatched `merge_parameters`.
- **QA watch-only, no QA sidecar** — keeps the folder contract to exactly one RVT +
  one baseline JSON.
- **Narrow geometry scope, explicit gates** — only REDRAW_ONLY changes, RX walls only,
  unshared walls only. Added stands are created, removed stands deleted, but a
  cluster is skipped rather than half-applied when a type/level is missing, a wall
  hosts a door/window/opening, a Number is duplicated, levels disagree or a
  conflict is unresolved. Conservative by design.
- **A geometry cluster is the atomic unit of apply** — `apply_cluster` runs each
  cluster in a `SubTransaction` of the single merge transaction. One cluster's
  failure cannot leave a deleted Room or a half-built boundary behind, and the
  clusters that did succeed still commit. Parameter writes are separate from the
  cluster transaction and can still succeed when the geometry is skipped or fails,
  except for a duplicated Room Number. The outer transaction is still one Ctrl+Z.
- **The summary says what happened** — `SUCCESS` / `SUCCESS WITH WARNINGS` /
  `COMPLETED WITH ERRORS` plus the first reason, and skips remain in the geometry
  watch with their reason instead of disappearing.
- **The audit trail is a file contract** — an append-only per-cluster/per-parameter
  log and a rewritten per-run summary next to the Redraw. Both are gitignored so a
  local run cannot publish stand data. A failed run still records `RUN FAILED`.
- **The tolerance is an identity test, not an accuracy setting** — a rebuilt wall is
  the Redraw's own curve, so it is exact whatever the tolerance. What the tolerance
  decides is whether an existing Main wall is "the same wall" (kept) or replaced.
  Untouched walls are bit-identical because the Redraw is a copy, so the default is
  0.5 mm: an edited wall is rebuilt, and the area cannot be left 10 mm out by a wall
  that was merely "close enough". Straight walls compare by endpoints, so this is
  free; curved walls are sampled, which is why 0.5 mm is the floor rather than zero.
- **A sold stand refuses the run, it does not skip a cluster** — the commercial rule
  is that no part of a redraw may merge if a sold stand's area would change, so the
  check is a run-level gate before the transaction, with a second check of the live
  `Room.Area` inside the transaction before the commit. Only `sold` blocks; `OnHold`
  and `Available` do not. The area allowance is derived from the perimeter and the
  tolerance rather than guessed, because a rebuild that keeps a wall within the
  tolerance can legitimately move the area by about that much.

## 5. Revit API facts relied on (verified against docs)

- `DetachFromCentralOption` cannot be used with cloud model paths;
  `Application.CopyModel` does not support cloud paths (it does copy local files
  without changing the active document).
- `SpatialElementBoundaryLocation.Finish` is the finish face; `.Center` is the wall
  centerline; `.CoreCenter`/`.CoreBoundary` are the compound-wall core variants.
- `UIApplication.OpenAndActivateDocument(ModelPath, OpenOptions, bool)` opens or
  activates a document and changes the active document; it must run with no open
  transaction.
- `Document.IsModelInCloud`, `Document.SaveCloudModel()`,
  `Document.SynchronizeWithCentral(TransactWithCentralOptions, SynchronizeWithCentralOptions)`;
  `SynchronizeWithCentralOptions.SetRelinquishOptions/SaveLocalAfter/Comment`.
- `WorksharingUtils.GetCentralModelPath(doc)`; `OpenOptions.DetachFromCentralOption =
  DetachAndDiscardWorksets`; `SaveAsOptions` + `WorksharingSaveAsOptions.SaveAsCentral`.
- `RevitLinkType.Create(Document, ModelPath, RevitLinkOptions)` returns a
  `LinkLoadResult` (`.ElementId`), loads the link, and **throws if a link type for
  that path already exists** — hence find/reuse first.
- `RevitLinkType.IsLoaded(Document, ElementId)`; `RevitLinkType.Load()` regenerates,
  clears undo history, and must run with no open transaction.
- **`Application.OpenDocumentFile(ModelPath, OpenOptions)`** opens a second document
  without touching the active one; `Document.Close(False)` closes it without saving.
  Neither clears the active document's undo history — that is why graph 2 reads the
  Redraw this way instead of linking it.
- `Element.GetExternalFileReference().GetAbsolutePath()` returns an absolute
  `ModelPath` regardless of stored relative/absolute path type;
  `ModelPathUtils.ConvertModelPathToUserVisiblePath` for comparison.
- `ElementId.Value` (Revit 2024+) with `IntegerValue` fallback; `SpecTypeId`/
  `UnitTypeId` + `UnitUtils` for units.
- **Revit 2027 has no `Room.IsPlaced`.** `SpatialElement` exposes only `Area`,
  `Level`, `LevelId`, `Location` and `Number`. Graph 1 therefore derives
  `placement_status` from `room.Location` being a `LocationPoint`, and
  `has_geometry` from whether boundary segments are actually returned.
- `SpatialElement.Number` has a setter, so a Room's number can be written
  directly as well as through its parameter (both confirmed in Revit 2027).
- `SpatialElement.Location` is get-only; a room's location point is moved with
  `Location.Move`, not by assigning to the property.
- `View.SetElementOverrides(ElementId, OverrideGraphicSettings)` applies a
  graphics override to a **single element** in a view. `OverrideGraphicSettings`
  has a parameterless constructor and a copy constructor, and can set cut,
  projection and surface colours and patterns.
- `DirectShape.CreateElement(Document, ElementId categoryId)` +
  `SetShape(GeometryObject[])`; `SolidOptions.MaterialId` and
  `Material.Color` are settable.
- `RevisionCloud.Create(Document, View, ElementId revisionId, IList<Curve>)`
  takes the **Revision** id, so a revision cloud's colour comes from its
  revision and cannot be varied per cloud.
- `Document.Create.NewRoom(Level, UV)` — the position is a `UV`, not an `XYZ`.
  Revit **auto-numbers** a new room from the sequence, so an added stand must
  have its Number set explicitly.
- `Document.Create.NewRoomTag(LinkElementId, UV, ElementId)` creates a Room Tag in
  a given view; `LinkElementId(ElementId)` wraps a host-model Room's id. A view
  that is not a plan or section view throws `ArgumentException`, and Revit returns
  `null` when the tag cannot be created — both are reported per view, not fatal.
  `Element.ChangeTypeId(ElementId)` matches the tag type the view already uses,
  and `RoomTag.TagHeadPosition` moves the head.
- `DirectShape.SetName(string)` sets a DirectShape's element name, which is how the
  preview bands are marked for later cleanup (`Element.Name` reads it back).
- `View.GetElementOverrides()` lists every element override in a view, so leftover
  preview colours can be found without relying on a marker.
- `SubTransaction(Document)` + `Start()` / `Commit()` / `RollBack()`; `Commit()`
  returns a `TransactionStatus` and is only complete when it returns
  `TransactionStatus.Committed`. A sub-transaction must be finished before the
  outer transaction commits.
- `Wall.GetDependentElements(ElementFilter)` returns the elements Revit cascades
  with the wall; `FamilyInstance` and `Opening` among them are doors, windows and
  openings the merge must not destroy.
- **`WorksharingUtils.GetCentralModelPath(Document)` does not exist in Revit
  2027.** Use `Document.GetWorksharingCentralModelPath()`, which returns the
  central path or null, and throws `InvalidOperationException` when the
  document is not workshared.
- **`Room.PhaseCreated` / `Room.PhaseDemolished` do not exist in Revit 2027.**
  Use `Element.CreatedPhaseId` / `Element.DemolishedPhaseId`.
- **`ElementId.IntegerValue` was removed** in Revit 2024+; use `ElementId.Value`.
- **`BuiltInCategory.OST_GenericAnnotations` does not exist** in Revit 2027 —
  the category is `OST_GenericAnnotation` (singular).
- **`Extrusion.Create` does not exist** in Revit 2027; build an extruded solid
  with `GeometryCreationUtilities.CreateExtrusionGeometry(IList[CurveLoop],
  XYZ, double)`.
- **Room boundary curves are NOT trimmed to the room.**
  `BoundarySegment.GetCurve()` returns the **full wall location curve**, and
  for a wall shared by two rooms both rooms' segment lists carry the identical
  curve. Confirmed in Revit 2027 on a shared wall: segment length equals wall
  length and the endpoints match. So baseline curves can be compared with
  Main's wall curves exactly, and a shared wall de-duplicates by curve.
- `XYZ` has **no `Length` property** — use `GetLength()`. Its arithmetic
  operators are not dependable from PythonNet; do the arithmetic on floats and
  build new points with the constructor.
- `GeometryCreationUtilities.CreateExtrusionGeometry(IList[CurveLoop], XYZ,
  double)` builds an extruded solid. There is **no**
  `GeometryCreationUtilities.CreateExtrusionSolid` in Revit 2027;
  `Extrusion.Create(CurveLoop, Line, double)` is the older equivalent.
