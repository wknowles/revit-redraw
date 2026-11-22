# Phase 0 probe

Answers the four runtime questions that the Revit API documentation cannot answer,
before the Redraw merge is rebuilt.

The planned rebuild of `2. Compare and Update Redraw.dyn` moves the unit of merge
from a Room to a **Wall**, and adds a **visual preview** before the merge is
applied. Four things have to be true in a live Revit session before that work can
be written, because each one changes the design:

| | Question | Why it matters |
|---|---|---|
| **A** | Does a per-element view override (`View.SetElementOverrides`) recolour an existing wall in a floor plan, and does it read clearly? | The whole "highlight the Main walls that changed" half of the preview |
| **B** | Do DirectShape surfaces render in a floor plan (filled band, or outline only), and does `SetElementOverrides` accept a DirectShape? | The "here is where the Redraw puts it" half of the preview |
| **C** | Is a Room's `Number` writable — via `LookupParameter('Number')` or `BuiltInParameter.ROOM_NUMBER`? | A stand **added** in the Redraw has no Room in Main; without a writable Number it cannot be created |
| **D** | Are room boundary curves trimmed to the room, or are they the full wall curve? | Decides how the baseline's curves are matched against Main's wall curves |

Two more are answered along the way: what `Document.Create.NewRoom` does inside an
existing room, and whether the API calls used by the preview exist at all.

## Already answered

| | Question | Answer |
|---|---|---|
| **C** | Is a Room's `Number` writable? | **Yes**, all three ways: `room.Number = '...'` (the property has a setter), `LookupParameter('Number').Set(...)` and `BuiltInParameter.ROOM_NUMBER`. Confirmed in Revit 2027. |
| **D** | Are room boundary curves trimmed to the room? | **No.** `BoundarySegment.GetCurve()` returns the **full wall location curve** — for a wall shared by two rooms both rooms carry the identical curve. Confirmed in Revit 2027: segment length equals wall length and the endpoints match. |
| **A** | Are graphics overrides allowed in the view? | `AreGraphicsOverridesAllowed` returns **True**. Whether the wall visibly changes colour is still open. |
| **E** | `NewRoom` inside an existing room | Succeeds. The new room picks up a boundary and Revit **auto-numbers** it from the sequence (`A05` after `A01`-`A04`). An added stand must have its Number set explicitly. |

Still open: **A** (does the wall visibly recolour) and **B** (does the DirectShape
band render). Both need the visible test on a model with **real walls**.

## Running it

1. Open a project model in Revit 2027 with **at least one placed room bounded by
   walls**. A model with shared walls gives a conclusive answer to **D**.
   The probe is standalone: it needs **no Redraw and no baseline** — just the
   open model.
   *If the model was built by drawing room boundaries by hand, the boundary is
   made of Room Separation Lines rather than Walls. The inventory will say so;
   questions A, C and E still run, but D needs real walls.*
2. Open `Phase 0 Prototype.dyn` in Dynamo 4.0.2.
3. Leave **Run Visual Test** off and press Run. **Nothing in the model changes** —
   every diagnostic runs inside a transaction that is rolled back.
4. Read the **Probe Log** watch.
5. Optionally set **Run Visual Test** on and press Run again, to see the preview
   colours on the plan. This commits one transaction; press **Ctrl+Z once** to
   remove it. (A later run cleans up the previous one automatically.)

## Reading the log

The probe starts with a **boundary inventory**: it asks every room for its
boundary at both `Center` and `Finish`, and reports what the boundary is made
of. This is the first thing to look at, because it explains everything after it.

| Line prefix | Meaning |
|---|---|
| `A1` | graphics overrides allowed in the active view? |
| `A2` | `GetCategoryOverrides` usable? |
| `B1` | DirectShape creation, `SetShape` and `SetElementOverrides` all worked? |
| `C`  | is the Room Number writable? |
| `D`  | `segment len` vs `wall len` for a **shared** wall — `FULL WALL` means the boundary curve is the whole wall, `TRIMMED` means Revit cut it to the room |
| `E0`/`E1` | how many rooms, how many wall segments, how many walls bound more than one room |
| `E2` | which room and wall the probe picked |
| `E`  | `NewRoom` inside an existing room vs in empty space |

The `D` line is the one to look at first: compare `segment len` with `wall len`.
If they are equal the boundary curve is the full wall. If the segment is shorter,
Revit trims it per room and the baseline-to-wall matching has to tolerate that.

### If the inventory shows no Walls

`NO wall-bounded rooms. The boundary is not made of Walls` means the rooms are
bounded by something else — most often **Room Separation Lines**, which is what
you get when you draw a room boundary by hand instead of placing a room inside
walls. Two consequences:

- Questions **A**, **C** and **E** still run and are still valid; only **D** and
  the visual test are skipped.
- The merge itself only supports wall-bounded rooms, so the test model needs
  **real walls** for **D** to be answerable.

## What to report back

1. Does the wall turn **green** in the plan, and is it obvious? (**A**)
2. Is there a **blue band**, and is it filled or only an outline? (**B**)
3. `D  =>` — *FULL WALL curves* or *TRIMMED per room*? (**D**)
4. `C  ...` — `WRITABLE` or not? (**C**)
5. `E  NewRoom (inside an existing room)` — did it throw, or place a room? (**E**)

## Files

| File | Purpose |
|---|---|
| `phase0_probe.py` | The probe. This is the source of truth. |
| `build_dyn.py` | Generates `Phase 0 Prototype.dyn` from `phase0_probe.py`. |
| `Phase 0 Prototype.dyn` | The graph the operator opens in Dynamo. Generated — do not hand-edit. |
| `test_probe.py` | Offline harness. Stubs the Revit API and drives the probe through wall-bounded, non-wall-bounded, unplaced and empty models. |

## Files elsewhere in the repository

| File | Purpose |
|---|---|
| `tools/check_repo.py` | Runs every static check: CRLF JSON, embedded Python compiles, the generated graph matches its source, the probe survives its stubbed models, and the Revit API usage matches the documentation. |
| `tools/check_api.py` | The Revit 2027 API audit on its own. |
| `tools/build_api_index.py` | Refreshes `tools/revit_api_2027.json` from the documentation. Needs network access. |
| `tools/revit_api_2027.json` | Cached index of the documented API, so the audit runs offline. |

After editing `phase0_probe.py`:

```
python3 phase0/test_probe.py            # check the control flow offline
python3 phase0/build_dyn.py             # regenerate
python3 phase0/build_dyn.py --check     # confirm the .dyn matches
python3 tools/check_repo.py             # everything
```

`test_probe.py` can only prove that the probe's own control flow survives; it
cannot tell you whether an API call is correct against Revit. That is what
`tools/check_api.py` is for — it compares every API name the repository uses
with the Revit 2027 documentation.

The `.dyn` is CRLF JSON with the Python stored as escaped `\r\n`, matching the two
workflow graphs in the repository root. `build_dyn.py --check` is the pre-commit
gate for this file.
