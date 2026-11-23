# Revit Floorplan Redraw Process — User Guide

This guide explains how to create, develop, compare, and apply a floorplan Redraw in Revit.

Use this guide with:

- Autodesk Revit 2027
- Dynamo 4.0.2
- RX Revit floorplans
- stands that are Revit Rooms
- the Room Number as the Stand Number

**Last reviewed:** 23 November 2026.

If the Dynamo graph and this guide do not agree, follow the graph. Report the difference to the BIM support team.

---

## 1. Purpose

The Redraw process lets a floorplanner develop a revised layout without changing the live Main floorplan.

The Redraw is a separate copy of Main. When the Redraw is approved, Dynamo compares it with Main and with the Baseline. Dynamo then transfers only the changes that are safe to apply.

You do not replace Main with the Redraw. Dynamo applies selected changes to Main and reports everything that it cannot apply.

### Terms

| Term | Meaning |
|---|---|
| **Main** | The live Revit floorplan. |
| **Redraw** | A separate Revit model that contains the proposed layout. |
| **Baseline** | A snapshot of Main at the moment the Redraw was created. |
| **Stand** | A Revit Room with a Room Number. The Room Number is the Stand Number. |
| **Cluster** | A group of stands that share walls. Dynamo compares and applies the group together. |

---

## 2. Process overview

The process uses three floorplan states:

| State | Purpose |
|---|---|
| **Baseline** | Shows how Main looked when the Redraw started. |
| **Main** | Shows the current live floorplan. |
| **Redraw** | Shows the proposed changes. |

The Baseline is necessary because Main can change while the Redraw is in development. Dynamo compares Main with the Baseline and the Redraw with the Baseline. This shows which side changed.

### Workflow

1. Open the correct **Main** model.
2. Run **Create Redraw**.
3. Dynamo saves Main, writes the **Baseline**, and creates the **Redraw**.
4. Close Main.
5. Open the Redraw and make the approved layout changes.
6. Save and close the Redraw.
7. Reopen Main.
8. Run **Compare and Update Redraw** in **Preview** mode.
9. Review the proposed changes, conflicts, and skips.
10. Correct the Redraw or Main where necessary. Run Preview again.
11. Run **Compare and Update Redraw** in **Apply** mode.
12. Make sure that the updated Main floorplan is correct.
13. Save or synchronise Main only after the result is approved.

The Redraw stays separate from Main until you apply the changes. Main remains the live floorplan during the whole review.

---

## 3. Before you start

### 3.1 Main

- Open the correct Main model.
- Make Main the active document.
- Save Main before you run Preview. This gives you a recovery point.
- Open the floorplan view that you want to review during Preview.
- Make sure the view is the active view. Dynamo draws the Preview in the active view only.

Dynamo does not save Main during **Compare and Update Redraw**. Save or synchronise Main yourself.

### 3.2 Redraw

- Close the Redraw if it is open in Revit.
- Use the approved Redraw folder.
- Make sure the folder contains exactly one primary `.rvt` file.
- Make sure the folder contains exactly one `.baseline.json` file.
- Do not put Main or any other primary model in the Redraw folder.

Dynamo ignores Revit backup files with names such as `.0001.rvt`.

### 3.3 Floorplan data

Make sure that the following are correct before you compare:

- Each Room Number is unique across the whole model.
- Each Room Number is the correct Stand Number.
- Affected stands use Revit walls on all sides.
- Affected walls use the supported wall types.
- Main and the Redraw use the expected levels.
- Main and the Redraw have the same Project Information Number. If both have a number and the numbers differ, Dynamo stops.

---

## 4. Step 1 — Create the Redraw

Run **Create Redraw** from Main. Do not run it from a family document.

### 4.1 Input

**Create Redraw** has one input:

| Input | What to do |
|---|---|
| **Directory Path** | Select an empty folder for the Redraw and the Baseline. |

The folder input is blank when the graph opens. This is intentional. Select the folder on every run.

### 4.2 What Dynamo does

1. Dynamo saves Main. If Main is workshared, Dynamo synchronises it with the central model.
2. Dynamo keeps your workset ownership.
3. Dynamo reads every numbered Room and writes the Baseline.
4. Dynamo creates a detached, non-workshared Redraw.
5. Dynamo saves the Redraw and closes it.
6. Dynamo makes Main active again.

The output files use the Main model title:

- Redraw: `<Main title>.rvt`
- Baseline: `<Main title>.baseline.json`

The Baseline stores the stand boundaries on the wall centerlines. It also stores stand metadata and nine audited parameters:

- Corners
- Floorplan Hall
- Floorplan Pavilion
- Floorplan Sector
- Obstruction Area
- Product Code
- Custom Area
- Stand Length
- Stand Width

Dynamo can merge only six of these parameters later. See section 7.3.

### 4.3 Refusals

**Create Redraw** stops if:

- Main has no saved path and is not a cloud model.
- the target folder does not exist.
- the target `.rvt` or `.baseline.json` already exists.
- the folder contains another primary `.rvt` or another `*.baseline.json`.
- a Room Number is duplicated.
- no numbered Rooms exist.
- Dynamo cannot read a Room boundary.

A blank Room Number does not stop the run. Dynamo skips that Room and adds a warning. An unplaced Room or a Room with no boundary is kept in the Baseline for audit.

To create a new Redraw for the same title, delete or rename the old `.rvt` and `.baseline.json` pair first.

### 4.4 Make sure that the result is correct

Read the **Summary** and **QA** outputs.

Expected result:

- **status** is `SUCCESS`.
- **main_active** is `True`.
- **redraw_closed** is `True`.

If the status is `SUCCESS WITH WARNINGS`, Dynamo created the Redraw but did not complete the active-document cleanup. Read the QA output. Activate Main, then close the Redraw or temporary central model by hand.

Then continue with section 5.

---

## 5. Step 2 — Develop the Redraw

1. Close Main.
2. Open the Redraw from the approved folder.
3. Make the approved floorplan changes.
4. Save the Redraw.
5. Close the Redraw.
6. Reopen Main.

Follow these rules while you edit:

- Change the Redraw, not Main.
- Do not change the Baseline `.baseline.json` file.
- Keep Room Numbers unique.
- Do not change Room Numbers unless the approved layout changes the Stand Number.
- Use `Closed Wall - RX` or `Open Wall - RX` for walls that Dynamo must rebuild.
- Keep each affected stand on its expected level.
- Keep the approved Redraw and its Baseline together in the same folder.

The Baseline is the starting point for the three-way comparison. If you change it, Dynamo can no longer tell which side made a change.

---

## 6. How the comparison works

Dynamo does not simply copy the Redraw into Main.

Dynamo compares:

- Baseline with current Main.
- Baseline with current Redraw.

The result shows what changed on each side.

### 6.1 Normal comparison rules

| Situation | Result |
|---|---|
| Neither Main nor the Redraw changed | No action |
| Only the Redraw changed | Apply the Redraw change to Main |
| Only Main changed | Keep the Main change |
| Main and the Redraw made the same change | No action |
| Main and the Redraw changed differently | Report a conflict |
| The Redraw added a stand | Create the stand in Main |
| The Redraw removed a stand | Remove the stand from Main, if the safety checks pass |
| Only Main added a stand | Keep it |

### 6.2 Why the Baseline is necessary

Example:

1. A Redraw is created on Monday.
2. On Tuesday, a floorplanner moves Stand 101 in the Redraw.
3. On Wednesday, another person moves Stand 101 in Main.
4. The two models now differ, but the difference alone does not show who changed what.
5. The Baseline shows that both sides changed Stand 101.
6. Dynamo reports a conflict. It does not overwrite one change with the other.

---

## 7. What Dynamo compares

### 7.1 Stand geometry

Dynamo compares stand boundaries and the walls that form them. It detects:

- moved stands.
- resized or modified stands.
- new stands.
- removed stands.
- new walls.
- removed walls.
- wall position changes.
- wall type, height, base offset, flipped state, and level changes.

Dynamo detects a wall change even when the wall does not move.

### 7.2 Shared walls and clusters

Two neighbouring stands can share a wall. If that wall moves, both stands change.

Dynamo groups connected stands into a cluster and compares the group together. This prevents Dynamo from processing the same shared wall several times.

Dynamo applies each cluster as one atomic change. If one cluster fails, Dynamo rolls back that cluster only. The other clusters can still commit.

### 7.3 Stand parameters

Dynamo compares six stand parameters:

- Floorplan Hall
- Floorplan Pavilion
- Floorplan Sector
- Obstruction Area
- Product Code
- Custom Area

If only the Redraw changed a value, Dynamo can write the value to Main.

If Main and the Redraw changed a value differently, Dynamo reports a parameter conflict. Dynamo does not overwrite the Main value.

### 7.4 Rooms and Room Tags

When Dynamo applies a geometry change, it can:

- move an existing Room to the Redraw position.
- create a Room for a new stand.
- delete the Room of a removed stand.
- move a moved stand's Room Tags in every view that has them.
- create Room Tags for a new stand in every view that already carries Room Tags.

Dynamo reports a view that does not accept a new Room Tag. The run continues.

---

## 8. Limits and safety rules

The geometry scope is deliberately controlled.

Dynamo can rebuild a wall only when the change is safe. Dynamo stops or skips a cluster instead of making a partial change.

### 8.1 Supported walls

Dynamo can create or rebuild only these wall types:

- `Closed Wall - RX`
- `Open Wall - RX`

Dynamo skips a cluster if:

- a stand boundary is not made of walls, including a boundary with a Room Separation Line.
- a wall to delete or replace is not an RX wall type.
- a Redraw wall to create is not an RX wall type.
- a wall type that Dynamo must create does not exist in Main.
- a wall level that Dynamo must use does not exist in Main.
- the Main and Redraw Room levels do not match the Baseline.
- a Main wall to delete or replace hosts a door, window, or opening.
- a wall that Dynamo must remove still separates two stands, or a stand and an unmanaged Room.
- a Room Number is duplicated.
- a geometry conflict is unresolved.

A stand whose Room moves away in the Redraw no longer occupies its old side. In that case, Dynamo can remove the wall it left behind if no other stand still needs it.

### 8.2 Unmanaged Rooms

Dynamo does not merge:

- a Room with a blank Room Number.
- a Room whose Room Number is duplicated.

Dynamo protects every wall that these Rooms share. It will not delete such a wall.

A duplicated Room Number quarantines the whole cluster. The parameters of that cluster stay as Main has them.

### 8.3 What Dynamo does not merge

Dynamo does not merge changes to these audited parameters:

- Corners
- Stand Length
- Stand Width

Make these changes in Main by hand if the approved result needs them.

Dynamo creates a new wall without the doors, windows, or openings that the Redraw wall hosts. If a new Redraw wall must host one of these items, add it in Main by hand.

Dynamo also does not merge other floorplan features outside the stand workflow. Manage those features separately in Main.

### 8.4 Conflicts

A conflict occurs when Main and the Redraw both changed the same item in different ways.

This version has one global conflict setting: **REDRAW WINS ON CONFLICTS**. It cannot choose a winner for each stand or each parameter.

Dynamo skips a geometry conflict unless **REDRAW WINS ON CONFLICTS** is `True`.

Dynamo always reports a parameter conflict. **REDRAW WINS ON CONFLICTS** never overwrites a conflicting parameter value.

---

## 9. Step 3 — Preview the Redraw

Always run Preview before Apply.

Preview compares the three floorplan states. It does not apply geometry or parameter changes to Main.

### 9.1 Inputs

| Input | What it does | Saved default | Recommended use |
|---|---|---|---|
| **Redraw Directory** | Selects the folder that contains the Redraw and the Baseline. | Blank | Select the approved folder on every run. |
| **Comparison Tolerance** | Sets the geometry difference that counts as a change, in metres. | `0.01` | Keep `0.01` unless support instructs otherwise. |
| **Boundary Sample Spacing** | Sets the sampling distance for complex boundaries, in metres. | `0.25` | Keep `0.25` unless support instructs otherwise. |
| **APPLY UPDATES** | Selects Preview or Apply mode. | `False` | Use `False` for Preview. Use `True` for Apply. |
| **REDRAW WINS ON CONFLICTS** | Lets Redraw geometry win a geometry conflict. | `True` | Make sure that this input is correct before every run. See section 9.2. |

Use the Boolean selectors for **APPLY UPDATES** and **REDRAW WINS ON CONFLICTS**. Do not type these values as text.

Dynamo refuses a blank directory, a tolerance that is not a number or is negative, and a spacing that is not a positive number. Dynamo warns when the spacing is much larger than the tolerance.

### 9.2 The Redraw-wins default

The saved graph sets **REDRAW WINS ON CONFLICTS** to `True`. The code fallback is `False` when the input is missing.

Use this procedure:

1. Set **REDRAW WINS ON CONFLICTS** to `False` for the first Preview.
2. Review every reported conflict.
3. Decide which version must win.
4. Set it to `True` only when the approved decision is that the Redraw geometry replaces Main.
5. Run Preview again with the final setting.
6. Apply with the same setting that you reviewed.

When **REDRAW WINS ON CONFLICTS** is `True`, Dynamo still reports each conflict. The run status can therefore be `SUCCESS WITH WARNINGS` even when Dynamo resolves the conflict with the Redraw geometry.

### 9.3 Preview procedure

1. Open Main.
2. Open the floorplan view that you want to review.
3. Make sure Main is the active document.
4. Save Main.
5. Close the Redraw.
6. Open **Compare and Update Redraw**.
7. Select the **Redraw Directory**.
8. Set **Comparison Tolerance** to `0.01 m`.
9. Set **Boundary Sample Spacing** to `0.25 m`.
10. Set **APPLY UPDATES** to `False`.
11. Set **REDRAW WINS ON CONFLICTS** to the value that you want to review.
12. Run Dynamo.
13. Read the **Summary**.
14. Make sure that **Mode** is `DRY RUN`.
15. Review the coloured changes in the active view.
16. Review **Parameter Updates**.
17. Review **Geometry Updates**.
18. Review **Conflicts / Skips**.
19. Review **QA / Errors**.

Dynamo opens the Redraw for reading and closes it at the end of the run. Dynamo does not link the Redraw into Main.

### 9.4 What Dynamo examines before it continues

Dynamo stops before the merge if:

- the active document is the Redraw.
- the Redraw is open in Revit.
- the folder does not contain exactly one primary `.rvt` and one `*.baseline.json`.
- the Baseline schema or snapshot type is wrong.
- the Baseline filenames do not match the files in the folder.
- the Baseline merge-parameter list does not match the graph.
- the Baseline has no stands.
- the Project Information Number differs between Main and the Baseline.

Dynamo reports a warning when either model has no Project Information Number. It continues in that case.

---

## 10. Understand the Preview colours

Dynamo draws the Preview in the **active view**. Open the plan view that you want to review before you run Preview.

| Colour | Meaning |
|---|---|
| **Blue** | New Redraw wall, or the new position of a replaced wall. |
| **Amber** | Existing Main wall that Dynamo will remove or replace. |
| **Red** | Main wall that Dynamo will keep. Main added this wall after the Redraw was created. |
| **Magenta** | Dynamo skipped this cluster. |

A moved wall can show two colours:

- **amber** at its current Main position.
- **blue** at its proposed Redraw position.

Magenta is not an instruction to apply the cluster. Read **Conflicts / Skips** and **QA / Errors** to find the reason.

The colours are a visual aid. The text outputs are the authoritative record of the plan.

Preview is a separate transaction. The next run removes the old Preview before it draws a new one. Apply also removes the old Preview before it starts the merge.

---

## 11. Review the outputs

**Compare and Update Redraw** has five outputs:

| Output | What it tells you |
|---|---|
| **Summary** | The run status, mode, counts, audit result, and the first reason for a warning. |
| **Parameter Updates** | Stand parameter changes. |
| **Geometry Updates** | Planned or completed geometry changes, one entry per cluster. |
| **Conflicts / Skips** | Changes that need review, or changes that Dynamo did not process. |
| **QA / Errors** | Warnings, cluster details, timing, and fatal errors. |

### 11.1 Summary fields

| Field | Values | Meaning |
|---|---|---|
| **Status** | `SUCCESS`, `SUCCESS WITH WARNINGS`, `COMPLETED WITH ERRORS`, `FAILED` | Overall result. |
| **Message** | Text | The first skip, conflict, or failure reason. |
| **Mode** | `DRY RUN`, `APPLIED` | Preview or Apply. |
| **Audit** | `OK`, `FAILED`, `SKIPPED` | Result of the CSV audit write. |
| **Clusters** | Number | Clusters planned or verified in this run. |
| **Parameters** | Number | Parameter changes planned or verified. |
| **Geometry** | Number | Geometry changes planned or verified. |
| **Rooms created** | Number | Rooms created for stands that the Redraw added. |
| **Skipped** | Number | Clusters that Dynamo did not process. |
| **Errors** | Number | Failed or verification-failed changes. |
| **Conflicts** | Number | Geometry and parameter conflicts. |
| **Redraw document closed** | `YES`, `NO`, `NOT OPENED` | Result of closing the Redraw document. |
| **Preview** | Text | What the Preview drew or cleared. |
| **Undo** | Text | The transaction to undo, or `NOT REQUIRED`. |

### 11.2 Status meanings

| Status | Meaning |
|---|---|
| `SUCCESS` | No change failed, no cluster was skipped, and no conflict was reported. |
| `SUCCESS WITH WARNINGS` | The run completed, but one or more clusters were skipped or one or more conflicts were reported. |
| `COMPLETED WITH ERRORS` | One or more changes failed, or one or more changes failed verification. |
| `FAILED` | The run stopped before it completed. Read **Error** and **QA / Errors**. |

A `SUCCESS` status does not prove that every Redraw change was applied. It proves that nothing was skipped, conflicted, or failed. Always review the detailed outputs.

### 11.3 Change statuses

| Status | Meaning |
|---|---|
| `PLANNED` | Preview will apply this change in Apply mode. |
| `APPLIED` | Apply wrote this change. Verification runs next. |
| `VERIFIED` | Main matches the Redraw after Apply. |
| `SKIPPED` | Dynamo did not apply this change. The reason is in **Conflicts / Skips** or **QA / Errors**. |
| `FAILED` | Dynamo did not apply this change. |
| `VERIFY_FAILED` | Main does not match the Redraw after Apply. |

**Geometry Updates** also shows the cluster change type:

- `MOVED`
- `RESIZED`
- `ADDED`
- `REMOVED`
- `MIXED`

---

## 12. Conflicts

A conflict occurs when Main and the Redraw both changed the same item from the Baseline in different ways.

Example:

1. The Baseline has Stand 101 in position A.
2. Main moves Stand 101 to position B.
3. The Redraw moves Stand 101 to position C.
4. Dynamo detects that both sides changed.
5. Dynamo reports a geometry conflict.

A parameter conflict occurs in the same way for one of the six merged parameters.

### What to do

1. Read the conflict in **Conflicts / Skips**.
2. Compare Main, the Redraw, and the approved layout.
3. Correct Main or the Redraw so that only one required change remains.
4. Run Preview again.
5. If the approved decision is that the Redraw geometry must replace Main, set **REDRAW WINS ON CONFLICTS** to `True` and review the conflict again.

Do not use **REDRAW WINS ON CONFLICTS** to hide an unresolved design decision. It changes geometry only. It never resolves a parameter conflict.

---

## 13. When not to Apply

Do not run Apply if:

- **Status** is `FAILED`.
- the run reports unexpected errors.
- the wrong Main or Redraw is selected.
- the Baseline does not match the Redraw.
- an unexpected area is magenta.
- Dynamo reports an unsupported wall type.
- a stand uses a boundary that is not made of walls.
- a wall hosts a door, window, or opening.
- Main and the Redraw use different levels.
- a geometry conflict is unresolved.
- a parameter conflict affects the required result.
- the Preview does not match the approved Redraw.
- **Redraw document closed** is `NO` and you have not closed the Redraw by hand.

Correct the problem. Run Preview again. Apply only after the Preview is correct.

---

## 14. Step 4 — Apply the Redraw

Run Apply only after you review and accept the Preview.

If Main changes after the Preview, run Preview again before Apply.

### 14.1 Procedure

1. Make sure that Main is open and active.
2. Make sure that the Redraw is closed.
3. Save or synchronise Main. This gives you a recovery point.
4. Keep the reviewed Preview result available.
5. Decide the **REDRAW WINS ON CONFLICTS** setting.
6. Set **APPLY UPDATES** to `True`.
7. Use the same **REDRAW WINS ON CONFLICTS** setting that you reviewed.
8. Run Dynamo once.
9. Wait for the run to finish.
10. Read **Summary**. Make sure that **Mode** is `APPLIED`.
11. Review **Parameter Updates**.
12. Review **Geometry Updates**.
13. Review **Conflicts / Skips**.
14. Review **QA / Errors**.
15. Inspect the affected areas in Revit.
16. Save or synchronise Main only after you approve the result.

Apply runs in two transactions:

1. `Redraw Merge - Clear Preview` removes the old Preview.
2. `MVP2.4B6 - Merge Redraw Updates` applies the changes.

Each cluster runs in a sub-transaction of the merge. A cluster that fails is rolled back. The other clusters can still commit.

### 14.2 Apply outcomes

| Outcome | What it means |
|---|---|
| `SUCCESS` | All planned changes applied and verified. |
| `SUCCESS WITH WARNINGS` | Some changes applied, but at least one cluster was skipped or a conflict was reported. |
| `COMPLETED WITH ERRORS` | At least one change failed or failed verification. Review the affected clusters. |
| `FAILED` | Dynamo stopped. If the failure occurred during the merge, the merge transaction rolled back. |

---

## 15. What Apply can change

### 15.1 Walls

Dynamo can:

- keep existing Main walls.
- delete Main walls that the Redraw removed.
- create Redraw walls that Main does not have.
- replace Main walls with the Redraw version when position or attributes differ.

Dynamo keeps a wall that Main added after the Redraw was created, unless **REDRAW WINS ON CONFLICTS** replaces a stand that the Redraw re-created.

### 15.2 Stands

Dynamo can:

- move existing Rooms.
- create Rooms for stands that the Redraw added.
- delete Rooms for stands that the Redraw removed.

### 15.3 Room Tags

Dynamo can:

- move a moved stand's Room Tags in every view that has them.
- create Room Tags for a new stand in every view that already carries Room Tags.

A new stand gets one Room Tag per view that has Room Tags, using the type that the view already uses. Dynamo places the tag on the Room location point. If the model has no Room Tags, Dynamo creates one tag in the active view with the default type.

### 15.4 Parameters

Dynamo can write these six parameters when only the Redraw changed them:

- Floorplan Hall
- Floorplan Pavilion
- Floorplan Sector
- Obstruction Area
- Product Code
- Custom Area

Dynamo skips a parameter write if the parameter is missing in Main, is read-only, or is not a text parameter.

---

## 16. Examine the completed update

After Apply, inspect the changed area in Revit.

Make sure that the following are correct:

- stand positions.
- stand dimensions and boundaries.
- shared walls.
- new stands.
- deleted stands.
- wall types, heights, offsets, and levels.
- Room locations.
- Room Tags in every affected view.
- neighbouring stands.
- updated stand parameters.

Dynamo makes sure that each applied cluster matches the Redraw after the merge. A cluster whose geometry does not match the Redraw is reported as `VERIFY_FAILED`. A parameter whose Main value does not match the Redraw is also reported as `VERIFY_FAILED`.

Do not publish the updated Main model until you review the result.

---

## 17. Undo and recovery

Apply edits Main in one merge transaction:

`MVP2.4B6 - Merge Redraw Updates`

One Ctrl+Z reverses the merge.

The Preview is a separate transaction. Apply removes the old Preview before the merge. If you undo the merge, Revit does not restore the Preview graphics.

A cluster that failed was rolled back during Apply. Undo does not need to remove it.

Undo is a recovery tool. It is not a replacement for Preview, review, and a saved Main model.

---

## 18. Audit files

Dynamo writes two CSV files next to the Redraw:

- `<Redraw title>.merge-log.csv`
- `<Redraw title>.merge-summary.csv`

The log appends one row for each cluster and one row for each parameter decision. The summary is rewritten on each run.

A failed run writes a `RUN FAILED` row when Dynamo can find the folder. If Dynamo cannot write the files, **Summary / Audit** shows `FAILED` or `SKIPPED`.

Treat the audit files as project records. They can contain stand data. Do not publish them.

---

## 19. Common refusals

| Message or symptom | What to do |
|---|---|
| `Select the Redraw directory` | Select the folder that contains the Redraw and its Baseline. |
| `Redraw directory must contain one primary RVT` | Remove extra `.rvt` files from the folder. |
| `Redraw directory must contain one baseline JSON` | Put exactly one matching `.baseline.json` file in the folder. |
| `Active document is the Redraw` | Open Main and run Compare and Update Redraw from Main. |
| `Close the Redraw before running` | Close the Redraw document in Revit. |
| `Redraw or baseline already exists` | Delete or rename the old Redraw and Baseline pair before you run Create Redraw. |
| `Target directory must contain no other primary RVT files` | Use an empty or dedicated folder. |
| `Project number mismatch` | Stop. You have the wrong Redraw, Baseline, or Main model. |
| `Duplicate Room Number` | Make each Room Number unique in Main and the Redraw. |
| `Main Room level differs from baseline` | Correct the Room level, or add the required level to Main. |
| `No level in Main for this cluster` | Add the level to Main, or correct the wall level in the Redraw. |
| `Main wall ... hosts doors/windows/openings` | Remove the hosted items in the Redraw, or handle the change by hand. |
| `Wall type not found in Main` | Add the wall type to Main, or use an RX wall type that Main already has. |
| `The Redraw removes a wall that still separates` | Keep the wall in the Redraw, or delete the neighbouring stand in the Redraw. |
| `The comparison tolerance must be` | Enter a number of metres that is zero or greater. |
| `The boundary sample spacing must be` | Enter a number of metres that is greater than zero. |
| `Main still has unsaved changes after save/sync` | Save Main by hand, then run Create Redraw again. |
| `Audit` is `FAILED` | Make sure that the Redraw folder has write permission and free disk space. |

---

## 20. Quick operator checklist

### Create and develop

- [ ] Main is open, active, and saved.
- [ ] The Redraw folder is empty or dedicated to this Redraw.
- [ ] **Create Redraw** reports `SUCCESS`.
- [ ] The Redraw and Baseline exist as a matched pair.
- [ ] Main is active and the Redraw is closed after Create Redraw.
- [ ] The Redraw contains the approved layout changes.
- [ ] Room Numbers are unique.
- [ ] Affected walls use `Closed Wall - RX` or `Open Wall - RX`.
- [ ] The Redraw is saved and closed.
- [ ] Main is open again.

### Preview

- [ ] Main is active and saved.
- [ ] The Redraw is closed.
- [ ] The correct Redraw folder is selected.
- [ ] Comparison Tolerance is `0.01 m`.
- [ ] Boundary Sample Spacing is `0.25 m`.
- [ ] APPLY UPDATES is `False`.
- [ ] REDRAW WINS ON CONFLICTS is set for this review.
- [ ] Read Summary Status and Mode.
- [ ] Review the Preview colours in the active view.
- [ ] Review Parameter Updates.
- [ ] Review Geometry Updates.
- [ ] Review Conflicts / Skips.
- [ ] Review QA / Errors.

### Apply

- [ ] Review and accept the Preview.
- [ ] Resolve or explicitly accept every conflict.
- [ ] Save or synchronise Main.
- [ ] Make sure that Main is active and the Redraw is closed.
- [ ] APPLY UPDATES is `True`.
- [ ] REDRAW WINS ON CONFLICTS matches the reviewed setting.
- [ ] Run Dynamo once and wait for it to finish.
- [ ] Read Summary Status. Make sure that it is `SUCCESS` or that you understand the warnings.
- [ ] Review Geometry Updates. Make sure that they are `VERIFIED` or explained.
- [ ] Review Parameter Updates. Make sure that they are `VERIFIED` or explained.
- [ ] Examine the changed stands in Revit.
- [ ] Examine Room Tags and parameters.
- [ ] Examine the audit files.
- [ ] Save or synchronise Main after approval.

---

## 21. Key rules

1. Make Redraw changes in the Redraw, not in Main.
2. Do not change the Baseline.
3. Run Preview before Apply.
4. Make sure that **REDRAW WINS ON CONFLICTS** is correct before every run. The saved default is `True`.
5. Do not ignore conflicts, skipped clusters, or magenta areas.
6. Examine Main before you save or publish the applied result.
