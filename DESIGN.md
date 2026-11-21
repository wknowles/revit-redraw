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
  (`placement_status` from `Room.IsPlaced`, `has_geometry` flag). Blank numbers are
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
   Mismatch → fatal; missing on either side → `IDENTITY WARNING` in `issues` (wired)
   and `qa`.
3. **Link** (`add_link`): find existing link type by absolute external-file path;
   `Load()` it if unloaded (outside transactions — `Load` clears undo history);
   reuse an existing instance of that type, else create type+instance in one
   transaction (`MVP2.4B1 - Load Redraw`). Returns `(instance, link_type_id)`;
   cleanup always has the type id. Redraw rooms are read through
   `GetTotalTransform()` into Main coordinates.
4. **Collect**: three-way states — baseline (`bases`, curves rebuilt from JSON),
   Main, Redraw — at the baseline's boundary location (`Center` or legacy `Finish`).
   Each state has parameters, boundary loops, wall descriptors
   (type/level/height/offset/flipped/curve), centroid, room point, level id/name.
5. **Plan** per stand present in all three:
   - Geometry change class from baseline→Main and baseline→Redraw:
     `UNCHANGED` / `MOVED` / `MODIFIED` / `ADDED` / `REMOVED` / `PLACEMENT_CHANGED`
     (boundary distance = max sampled point-to-curve projection both ways).
   - Apply geometry only when Main is `UNCHANGED` vs baseline and Redraw is
     `MOVED`/`MODIFIED` (**REDRAW_ONLY**). Both-changed → `GEOMETRY CONFLICT`;
     added/removed/placement-changed → skipped (out of MVP scope).
   - **Level gate** (`level_mismatch`): skip geometry if Main level id/name differs
     from baseline, Redraw level name differs from baseline, or Main/Redraw level
     names differ. Parameters still merge.
   - **Wall eligibility** (`geometry_eligibility`): Main boundary must be entirely
     walls, each used by only this stand, of type `Closed Wall - RX` /
     `Open Wall - RX`; Redraw boundary must be entirely walls of those same types.
     Room Separation Lines are not supported.
   - Parameters: three-way relation `NO_CHANGE` / `MAIN_ONLY` / `REDRAW_ONLY` /
     `SAME_CHANGE` / `CONFLICT`; only `REDRAW_ONLY` is written, only to text
     parameters, `NO_VALUE` maps to blank string. The 6 merged parameters:
     `Floorplan Hall`, `Floorplan Pavilion`, `Floorplan Sector`, `Obstruction Area`,
     `Product Code`, `Custom Area`. `Corners`, `Stand Length`, `Stand Width` are
     audit-only; Room Name is never merged.
6. **Apply** (one transaction `MVP2.4B6 - Merge Redraw Updates`, so Undo is one step):
   per geometry op — delete Main boundary walls → regenerate → capture room tags in
   the **active view** → create Redraw walls (type/height/offset/flipped copied;
   height fallback 10 ft; non-structural) on the Main room's level → regenerate →
   move room location point to Redraw room point/centroid → recenter captured tags
   (`TagHeadPosition`, keeping tag Z). Then parameter sets. Per-op failures are
   recorded, not fatal.
7. **Verify** after commit: re-collect Main; geometry verified by boundary distance
   ≤ tolerance, parameters by exact string equality → `VERIFIED` / `VERIFY_FAILED`.
8. **Cleanup**: remove the Redraw link type (`MVP2.4B1 - Remove Redraw Link`) only
   after verification, because link-derived curves die with the link. Emergency
   cleanup in `finally` on any earlier failure.

Dry run (`Apply Updates` false) outputs the same plans with status `PLANNED` and
still loads/removes the link.

## 4. Decisions and rationale (business + technical)

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
