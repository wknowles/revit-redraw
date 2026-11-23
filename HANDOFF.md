# HANDOFF — Redraw merge rebuild

A snapshot of the working session that rebuilt graph 2, written so a new
conversation can pick it up. It is a working note, not a product document:
`AGENTS.md`, `DESIGN.md`, `ROADMAP.md` and `CHANGELOG.md` remain the records
of record.

## 1. Where this is going

The Redraw workflow has two Dynamo graphs (`1. Create Redraw.dyn`,
`2. Compare and Update Redraw.dyn`). Graph 2 could only merge **isolated**
stands that were **moved or resized**. The goal is a full merge:

- **clusters** of connected stands (most stands share walls) merge as one change
- stands **added** in the Redraw are created; stands **deleted** are removed
- a **visual preview** on the plan before anything is applied
- a **CSV audit trail** for non-technical business users
- conflicts resolved with an explicit **"Redraw wins"** approval

## 2. Repo state

| | |
|---|---|
| Branch | `phase0-redraw-merge-probe`, pushed to `origin` |
| `main` | untouched |
| PR | https://github.com/wknowles/revit-redraw/pull/new/phase0-redraw-merge-probe |
| Checks | `python3 tools/check_repo.py` → **ALL CHECKS PASSED** |

```
src/graph2.py            source of truth for graph 2's embedded Python
src/merge_core.py        pure-Python wall matching + clustering engine
src/test_merge_core.py   unit tests for the engine
src/test_graph2_plan.py  planning tests with stubbed Revit models
src/build_graph2.py      injects src/ into the .dyn (--check verifies)
tools/check_repo.py      runs every static check
tools/check_calls.py     AST call-arity check
tools/check_api.py       Revit API audit against the 2027 documentation
tools/revit_api_2027.json  cached API index (refresh: build_api_index.py)
```

**`src/graph2.py` is the source of truth — edit it, never the `.dyn`.**
`python3 src/build_graph2.py` injects it; `--check` fails if they are out of
step. The `.dyn` is CRLF JSON with the Python stored as escaped `\r\n`,
matching the repo's existing graphs.

## 3. The core design

### The unit of merge is a **wall**, not a room

| Wall situation | Action |
|---|---|
| Main wall already sits where a Redraw wall sits | **kept** (doors, joins, element ID survive) |
| Main wall, no Redraw counterpart | **deleted** |
| Redraw wall, no Main counterpart | **created** |
| Same position, different type/height/offset/flip | **rebuilt** |
| Added to Main after the Redraw was cut | **left alone** |

### Clusters need no algorithm

If a shared wall moves in the Redraw, **both** neighbours' boundaries change —
so the affected set is already closed over shared walls. It *is* the cluster.
One extra rule covers deletion: **a Main wall may only be deleted if every
Main stand bounding it is in the change set.**

### Three-way per stand

`UNCHANGED` / `ALREADY` (Main already matches the Redraw — *not* a conflict) /
`CONFLICT` / `AFFECTED` / `ADDED` / `REMOVED` / `MAIN_ONLY` / `MAIN_REMOVED`.

### Business rules that still hold

- Only `Closed Wall - RX` / `Open Wall - RX` walls may be rebuilt (scoped per
  *created* wall, not per stand)
- A boundary that is not made of walls is skipped (Room Separation Lines)
- Level mismatches are skipped
- 6 merged parameters only: `Floorplan Hall`, `Floorplan Pavilion`,
  `Floorplan Sector`, `Obstruction Area`, `Product Code`, `Custom Area`.
  `Corners` / `Stand Length` / `Stand Width` are audit-only (a plugin computes
  them on save)
- Removed stands → Room deleted, parameters captured in the CSV first
- Added stands → 6 parameters copied, Number set explicitly (Revit
  auto-numbers new rooms)
- Redraw wins on conflicts, but only when the operator approves it

### Preview

| Colour | Meaning |
|---|---|
| **Blue** | the Redraw's new wall positions (`DirectShape` bands) |
| **Amber** | Main walls the merge will rebuild or delete |
| **Red** | Main walls Main changed itself — left alone |
| **Magenta** | clusters that will be **skipped** (so a blocked plan is never invisible) |

Main walls are recoloured in place with `View.SetElementOverrides`; only the
Redraw's new positions need a band. Every touched wall gets a band as well, so
the preview is visible even where recolouring does not take.

### Audit trail

`<title>.merge-log.csv` (append-only, one row per cluster) and
`<title>.merge-summary.csv` (the last run's totals), written next to the
Redraw.

### Graph inputs (graph 2)

| Input | Default | |
|---|---|---|
| Directory Path | blank | the Redraw folder |
| Comparison Tolerance | 0.01 m | |
| Boundary sampling spacing | 0.25 m | |
| **Apply Updates** | **false** | preview only |
| **Redraw Wins On Conflicts** | **true** | |

## 4. Confirmed Revit 2027 API facts

Verified against `rvtdocs.com/2027/`. All of these **failed silently** in
this session because the calls were wrapped in `except` blocks.

| Fact | Consequence |
|---|---|
| **`Room.IsPlaced` does not exist** | `SpatialElement` exposes only `Area`, `Level`, `LevelId`, `Location`, `Number`. Use `room.Location` being a `LocationPoint` |
| **`WorksharingUtils.GetCentralModelPath` does not exist** | Use `Document.GetWorksharingCentralModelPath()` (returns null, or throws `InvalidOperationException` when the document is not workshared) |
| **`Room.PhaseCreated` / `PhaseDemolished` do not exist** | Use `Element.CreatedPhaseId` / `DemolishedPhaseId` |
| **`ElementId.IntegerValue` was removed** (2024+) | Use `ElementId.Value` |
| **`BuiltInCategory.OST_GenericAnnotations` does not exist** | It is `OST_GenericAnnotation` (singular) |
| **`Extrusion.Create` does not exist** | Use `GeometryCreationUtilities.CreateExtrusionGeometry(IList[CurveLoop], XYZ, double)` — there is no `CreateExtrusionSolid` |
| **`XYZ` has no `Length` property** | Use `GetLength()`; its operators are not dependable from PythonNet — do the arithmetic on floats |
| **`SpatialElement.Number` has a setter** | `room.Number = 'A05'` works |
| **`SpatialElement.Location` is get-only** | Move the point with `Location.Move(delta)` |
| **`Document.Create.NewRoom` takes a `UV`**, not an `XYZ` | |
| **`Wall.Create` wants an `ElementId` for the level** | not an int |
| **`View.SetElementOverrides(ElementId, OverrideGraphicSettings)`** | per-element graphics override — works |
| **`OverrideGraphicSettings()`** | parameterless constructor plus a copy constructor |
| **`RevisionCloud.Create(Document, View, ElementId revisionId, IList<Curve>)`** | the colour comes from the Revision → **one colour only**, and it pollutes the revision record. Revision clouds were rejected for this |
| **`RevitLinkType.Load()` "loads or reloads"** | it must be unloaded first, and it **clears the undo history** |
| **`Document.Regenerate()`** | committing regenerates anyway, and regenerating outside a transaction can be refused |

## 5. Phase 0 answers (all four settled, on a live production model)

- **A** `SetElementOverrides` with `SetCutForegroundPatternColor` **does**
  recolour an existing wall, and reads clearly.
- **B** a `CreateExtrusionGeometry` band in `Generic Annotations` **renders
  filled** in a floor plan.
- **C** a Room's Number is writable three ways: the `Number` property,
  `LookupParameter('Number')`, and `BuiltInParameter.ROOM_NUMBER`.
- **D** **Room boundary curves are NOT trimmed** —
  `BoundarySegment.GetCurve()` returns the full wall location curve, and a
  shared wall carries the identical curve in both rooms' segment lists. So
  baseline curves can be compared with Main's wall curves **exactly**, and a
  shared wall de-duplicates by curve. **No baseline schema change was needed.**

## 6. Bugs found and fixed (do not reintroduce)

| Bug | Symptom |
|---|---|
| `component_level` / `preview_height` / `draw_preview` called with the wrong number of arguments | `Status: Failed` — and **the QA watch was not wired**, so the reason was invisible |
| The QA watch's `List.GetItemAtIndex` never got its list | the watch showed nothing |
| `Document.Regenerate()` after the commit | "Modification of the document is forbidden" — Revit's modal "Multiple Rooms in one region" warning let the user edit the document outside our transaction |
| Rooms deleted **after** the walls | the same warning; rooms now go first |
| A wall shared by two surviving stands deleted | the same warning; now **refused** with an actionable message |
| A stand's room deleted in the Redraw, so its surviving walls were deleted from Main | the Redraw's **wall inventory** is now used, so orphan walls are kept |
| `Wall.Create` given an `int` level | "No method matches given arguments" |
| An all-new cluster had no level | "levelId is null" — the level now comes from the Redraw's level name |
| Redraw parameters (a **dict**) indexed like a pair | `reason: 0` (= `str(KeyError(0))`) → the new stand's Room was never created |
| Preview drawn outside a transaction | "Attempt to modify the model outside of transaction" |
| `Room.PhaseCreated`, `Room.IsPlaced`, `ElementId.IntegerValue`, `WorksharingUtils.GetCentralModelPath` | all silently degraded graph 1 |
| A stale Redraw link reused | the plan did not match the Redraw just edited → the link is now **always reloaded** |

## 7. Where it stands

**Verified working by the operator on a live model:**

- All wall adjustments (moved, resized, shared walls) merge correctly —
  cluster **C1 VERIFIED**: 3 walls deleted, 2 created, 7 kept, 2 rooms moved,
  2 tags moved.
- The preview bands and colours show, and one Ctrl+Z takes them off.
- The new stand **A05** is created: its 4 walls and its Room land in Main.

**Fixed after the first apply run (not yet verified in Revit):**

- A05 had no Room Tag; moved stands' tags were only re-centred in the active view.
- Undo was not one step: the Redraw **link** cleared the undo history when it was
  loaded, and undoing the merge restored the link type. Graph 2 no longer links
  the Redraw at all — it opens it as a document and closes it.
- The preview bands survived an apply: the cleanup depended on the Comments
  parameter existing on a DirectShape, and swallowed every failure. Bands are now
  marked with `DirectShape.SetName`, and every view's overrides are swept for the
  preview colours.

## 8. Next steps

1. **Re-run graph 2 on the live model**, both modes, and check the in-Revit
   checklist in `ROADMAP.md` §1 (Graph 2), in particular:
   - the Summary says `Redraw document closed: YES` and `Rooms created: 1`;
   - A05 has a Room Tag in every view that has Room Tags, with the same type;
   - the dry run's bands are gone after the apply;
   - **one Ctrl+Z after the apply puts Main back exactly where it was**.
2. The bands left over from the *older* link-based runs carry no name marker;
   the override sweep should still delete them. If any survive, QA names what
   could not be removed.
3. The CSV audit trail has never been run in anger — check the file appears next
   to the Redraw with sensible rows.
4. Still to build: text notes per stand in the preview (identity labels).

## 9. Gotchas for the next session

- **Never hand-edit a `.dyn`.** Edit `src/graph2.py`, then
  `python3 src/build_graph2.py`.
- **`.dyn` files are CRLF JSON.** `python3 src/build_graph2.py` preserves that;
  a plain `json.dump` does not. Read with `newline=''` or you silently lose the
  CRLF.
- **The mock harness is deliberately strict** — it matches Revit 2027 (no
  `IsPlaced`, no `XYZ.Length`, no XYZ operators, writable `Number`). A
  permissive mock hid three real bugs; do not relax it.
- `tools/check_calls.py` catches wrong argument counts; `tools/check_api.py`
  catches API names that do not exist in 2027. **Run `tools/check_repo.py`
  before every commit.**
- **Graph 2 must never link the Redraw again.** `RevitLinkType.Load()` clears
  Revit's undo history, and undoing the merge always restores the link type, so a
  link can never give the one-step undo the operator wants. Read the Redraw from
  a document opened with `Application.OpenDocumentFile` and closed in `finally`;
  no transform is needed because graph 1 writes the Redraw as a copy of Main.
- **Do not mark preview elements with the Comments parameter alone.** It is not
  guaranteed to exist on a DirectShape, and the old cleanup silently skipped
  everything when it was missing. Use `DirectShape.SetName`, and let the override
  sweep (`View.GetElementOverrides`) catch anything unmarked.
- The `ROADMAP.md` in-Revit checklist has still never been run against the
  rebuilt graph.

## 10. Decisions the operator made (do not re-litigate)

| Decision | Answer |
|---|---|
| Deleting a stand | deletes the Room in Main; the parameters are captured first |
| Parameters on an added stand | the 6 merged ones only |
| Stale `Stand Length` / `Stand Width` | fine, the plugin recalculates them on save |
| Doors on stand walls | none exist, so there is no data loss to worry about |
| Audit trail | CSV, for non-technical users |
| Wall types | keep the RX restriction |
| Conflicts | Redraw wins, but with an explicit approval flag |
| Undo | one step for the merge |
| Confirming a merge | visually, on the plan, plan by plan — no level filters needed (everything is on Level 1) |

## 11. Reference

- Revit 2027 API docs: **https://rvtdocs.com/2027/**
  - class pages: `https://rvtdocs.com/2027/<fqn>/`
  - search API: `https://rvtdocs.com/search/v2/api/?q=<term>&v=2027`
  - sidebar tree: `https://rvtdocs.com/static/json_trees/sidebar_2027.json`
- `revitapidocs.com/2027/` loads, but its class pages are JS-driven — use
  rvtdocs instead.
- The repo's own documents: `AGENTS.md` (operating rules), `DESIGN.md` (design
  and confirmed API facts), `ROADMAP.md` (limitations), `CHANGELOG.md`
  (history).
