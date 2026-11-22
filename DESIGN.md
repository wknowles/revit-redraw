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
   geometry changes back into Main, verify, then remove the Redraw link.

Room Number is the stand key and is assumed **globally unique** (business decision).
Duplicates are errors in graph 1 and skipped-with-QA in graph 2.

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

Inputs: `IN[0]` Redraw directory, `IN[1]` tolerance metres (default 0.01),
`IN[2]` boundary sample spacing metres (default 0.25), `IN[3]` `Apply Updates`
(default False in code; saved graph defaults **true**). Metres are converted to
internal feet via `UnitUtils`. `OUT = (summary, pout, gout, issues, qa)`; only
indices 0–3 are wired (see `AGENTS.md` output-wiring gotcha).

### Pipeline
1. **Files/active document**: exactly one primary `.rvt` + one `*.baseline.json` in
   the folder; fail if the active document is the Redraw or the Redraw RVT is open.
2. **Validate** (`validate`): schema/snapshot, required filenames matching the actual
   files, `boundary_location` `"Center"` or `"Finish"` (returned for use by
   `collect`), `merge_parameters` equal to the graph's list when present, non-empty
   `stands`, project number match against Main's Project Information Number.
   Mismatch -> fatal; missing on either side -> `IDENTITY WARNING` in `issues`
   (wired) and `qa`.
3. **Link** (`add_link`): find existing link type by absolute external-file path;
   `Load()` it if unloaded (outside transactions - `Load` clears undo history);
   reuse an existing instance of that type, else create type+instance in one
   transaction (`MVP2.4B1 - Load Redraw`). Returns `(instance, link_type_id)`;
   cleanup always has the type id. Redraw rooms are read through
   `GetTotalTransform()` into Main coordinates.
4. **Collect**: three-way states - baseline (`bases`), Main, Redraw - at the
   baseline's boundary location. Each state has parameters, boundary loops, wall
   descriptors (type/level/height/offset/flipped/curve), centroid, room point and
   level id and name.
5. **Classify** every stand (`stand_status`): `UNCHANGED`, `ALREADY`, `CONFLICT`,
   `AFFECTED`, `ADDED`, `REMOVED`, `MAIN_ONLY`, `MAIN_REMOVED`. `ALREADY` means
   Main already is what the Redraw wants - that is not a conflict.
6. **Cluster** the stands: two stands are neighbours when they share a wall, in
   Main or in the Redraw (`state_wall_usage` -> `adjacency_from_wall_usage` ->
   `find_components`). The affected set is already closed over shared walls, so a
   cluster needs no separate algorithm.
7. **Plan** each affected cluster (`plan_component`, in `src/merge_core.py`):
   keep / replace / delete / create the walls, and list the room work - move the
   location point, create an added stand, delete a removed stand. Walls that Main
   added after the Redraw was cut are retained; walls Main removed are conflicts.
   A cluster with a conflict is skipped unless `Redraw Wins On Conflicts` is set.
   The RX wall type rule and the "boundary must be made of walls" rule are
   checked here.
8. **Preview** (dry run) or **Apply** (one transaction `MVP2.4B6 - Merge Redraw
   Updates`, so Undo is one step): delete the walls to go, create the walls to
   come, then create, delete and move the rooms, then the parameters. After the
   commit, re-collect Main and verify each cluster against the Redraw.
9. **Cleanup**: remove the Redraw link type (`MVP2.4B1 - Remove Redraw Link`) only
   after verification, because link-derived curves die with the link. Emergency
   cleanup in `finally` on any earlier failure.
10. **Audit trail**: every run appends to `<title>.merge-log.csv` and rewrites
    `<title>.merge-summary.csv` next to the Redraw.

### The preview
A dry run draws the difference on the plan instead of only describing it:

| Colour | What |
|---|---|
| Blue | the Redraw's new wall positions, as filled `DirectShape` bands |
| Amber | the Main walls the merge will rebuild or delete |
| Red | the Main walls Main changed itself, which are left alone |

The two Main colours are drawn by recolouring the existing walls with
`View.SetElementOverrides`, so they need no temporary geometry. Only the Redraw's
new positions need a band. The preview is removed by the next run of the graph.

## 4. Decisions and rationale (business + technical)

### The merge unit is a wall, not a room

The rebuild in `src/` changes the unit of comparison from a Room to a **Wall**.

Today a stand is compared and rebuilt as a monolith: if the stand changed, its
entire boundary is deleted and recreated. That is why only an **isolated**
stand can merge — a shared wall cannot be half-rebuilt — and why a stand that
merely grew loses the doors and joins on the walls that did not move.

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
- **Narrow geometry scope** — only REDRAW_ONLY changes on existing rooms bounded by
  the two hardcoded RX wall types; everything else is skipped/reported, never
  auto-fixed. Conservative by design.
- **Late link removal** — verification needs live link geometry.

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
