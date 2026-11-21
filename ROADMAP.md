# Roadmap — known gaps, deferred features, test checklist

Companion to `DESIGN.md` (current behaviour) and `CHANGELOG.md` (what changed).
Everything here is intentionally out of scope today; treat it as the input list for
future feature work.

## 1. Must verify in Revit 2027 before trusting the current build

No Revit/Dynamo runtime was available during the build; only static checks and
mocked pure-function tests passed. Run this checklist first:

### Graph 1
- [ ] Local non-workshared Main: `CopyModel` creates the Redraw without activating it;
      **Main active, Redraw closed**, baseline counts/geometry/parameters look right.
- [ ] Local workshared Main: syncs without relinquishing, detached Redraw has no
      worksets, **Main active, Redraw closed**.
- [ ] Cloud non-workshared Main: `SaveCloudModel` then local `SaveAs`; **Main
      reactivated and Redraw closed** (best effort; confirm no host-document issue).
- [ ] Cloud workshared Main: syncs, temp local central created, Redraw detached,
      **Main reactivated, temp central closed/deleted**. If Revit refuses, QA must
      identify the temp path.
- [ ] Centerline boundary: a Room beside a thick wall has baseline loops on the wall
      centerline, not the finish face; perimeter/centroid follow those loops.
- [ ] Refusals: existing `.rvt`/`.baseline.json` pair; another primary RVT or baseline
      in the folder; never-saved local file; duplicate/blank Room Numbers; unplaced
      and unbounded rooms retained with correct statuses.
- [ ] Failure atomicity: force a `SaveAs` failure and confirm no orphan `.pending`
      sidecar and a clear QA warning about a partial Redraw.
- [ ] Active-document cleanup failure: simulate inability to reactivate Main/close
      Redraw; summary reports `SUCCESS WITH WARNINGS` and QA names the manual step.

### Graph 2
- [ ] Guards: active document is the Redraw → fatal; Redraw RVT still open → fatal.
- [ ] Dry run (`Apply Updates` false) from Main with Redraw closed: plans only, link
      loaded then removed. Unchanged model → no geometry changes.
- [ ] Apply: REDRAW_ONLY parameter writes verified; REDRAW_ONLY geometry rebuilds
      walls, moves room, recenters tags in the active view; **Undo is one step**.
- [ ] Level mismatch (Main or Redraw room on a different level) → geometry skipped,
      parameters still merged.
- [ ] Project number mismatch → fatal; missing number → `IDENTITY WARNING` visible
      in the issues watch.
- [ ] New `Center` baseline and legacy `Finish` baseline both work; Main/Redraw/
      Verify use the baseline's boundary location.
- [ ] Old baseline (pre-MVP2.4C, no level metadata / `merge_parameters`) still passes
      validation and behaves sanely.
- [ ] Pre-existing Redraw link type (loaded and unloaded) is reused, not duplicated;
      link type removed after verification; emergency cleanup on forced mid-run error.
- [ ] Conflicts (both sides changed), shared walls, non-RX wall types, Room
      Separation Line boundaries → skipped/reported, never modified.

## 2. Known limitations (deliberate)

- **Geometry scope**: existing rooms only; REDRAW_ONLY changes only; both boundaries
  must be entirely `Closed Wall - RX` / `Open Wall - RX` walls, unshared in Main.
  Added/removed/replaced rooms, placement changes, level mismatches and conflicts
  are skipped, not fixed.
- **Room Separation Lines unsupported** anywhere in the geometry path.
- **Tags**: only tags in the *active view* are captured/recentered; other views'
  tags may drift after a room move.
- **Wall rebuild** copies type/height/offset/flipped from Redraw; height fallback
  10 ft; joins, inserts (doors/openings) and wall-type variations beyond the two RX
  types are not handled.
- **Cloud/temp cleanup is best effort** — graph 1 tries to reactivate Main and
  close/delete the temp central, but Revit may refuse to close a document hosting
  the Dynamo run. In that case the summary warns and the operator must clean up
  manually.
- **Graph 2 `qa` (`OUT[4]`) is not wired**; fatal reasons are only visible via the
  Summary status or by inspecting the node. Watch labels for `gout`/`issues` are
  misnamed ("Parameter Conflicts"/"Errors").
- **Directory inputs ship blank** — operators must select the folder on every run.
  This is intentional: local paths are not published.
- **No automated tests**; `.dyn` edits are hand/programmatic JSON surgery
  (see `AGENTS.md` verification recipe). Pre-MVP2.4C backups exist only in the
  session temp folder and are ephemeral.
- Graph 2's apply transaction is still named `MVP2.4B6 - Merge Redraw Updates`
  (kept for continuity); transaction names retain MVP prefixes even though the
  graph `Name` fields are now `Create Redraw` / `Compare and Update Redraw`.

## 3. Candidate future features

Ordered roughly by value/effort; none are committed.

1. **Surface graph 2 QA**: wire `OUT[4]` to a watch (add a `List.GetItemAtIndex` +
   code-block index) and fix the `gout`/`issues` watch labels. Small, high value —
   fatal reasons are currently invisible.
2. **In-Revit regression pass** (section 1) and then a small sample Main/Redraw pair
   checked into the repo as a manual test fixture.
3. **Add a pre-commit check** for JSON validity and embedded `py_compile` (the
   `AGENTS.md` recipe automated); the repo and initial commit already exist.
4. **Harden cloud temp-central cleanup**: graph 1 now attempts to reactivate Main,
   close/delete the temp central and close the Redraw; verify this in Revit. If
   closing the host document is refused, investigate a supported cloud→local detach
   path or a safe deferred close (e.g. Idling event) instead of the manual fallback.
5. **Added/removed room handling**: report-only today; next step could be guided
   creation of new rooms/walls from Redraw, or explicit deletion plans — both need
   new conflict rules and probably a review step.
6. **Room Separation Line support** in boundaries (eligibility, rebuild, verify).
7. **Multi-view tag recentering** (collect tags across views, not just active view).
8. **Conflict resolution UX**: today conflicts are listed and skipped; a future
   graph could let the operator choose Main-wins/Redraw-wins per stand/parameter.
9. **Level-change support**: currently skipped; could allow deliberate level moves
   with wall level reassignment.
10. **QA sidecar / audit trail** (was explicitly deferred): persist QA + merge
    results next to the baseline if the business wants a record; would change the
    one-RVT/one-JSON folder contract.
11. **Transaction/version hygiene**: decide whether to drop MVP prefixes from the
    apply/link transaction names; keep `schema_version` bumps coordinated across
    both graphs.
12. **Parameterise wall types / merged parameters** instead of hardcoding, if the
    business rules start varying per project (keep the cross-graph contract check).

## 4. Contract rules for any future change

- Baseline schema changes must update **both** graphs and `schema_version`;
  graph 2 validates schema/snapshot/filenames, `Center` or legacy `Finish`,
  `merge_parameters` and project number, and will reject mismatches.
- The 9 audited vs 6 merged parameter lists are a business rule; change them in both
  graphs together (`AUDIT_PARAMETERS` / `MERGE_PARAMETERS` in graph 1, `PARAMS` in
  graph 2) and update `AGENTS.md`/`DESIGN.md`.
- Keep the folder contract: exactly one primary `.rvt` + one `*.baseline.json`.
- Keep geometry conservative: when in doubt, skip and report rather than auto-fix.
- Preserve `.dyn` CRLF JSON and escaped `\r\n` code when editing programmatically.
