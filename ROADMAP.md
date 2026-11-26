# Roadmap — known gaps, deferred features, test checklist

Companion to `DESIGN.md` (current behaviour) and `CHANGELOG.md` (what changed).
Section 1 is the regression checklist. Sections 2 and 3 are intentionally out of
scope today; treat them as the input list for future feature work.

## 1. In-Revit 2027 validation and regression checklist

The rebuilt graph has been validated in Revit 2027. Keep this list as the
regression checklist. Run it again after a Revit or Dynamo upgrade, or after a
change to either graph. Tick the boxes during each pass.

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
- [ ] Guards: active document is the Redraw → fatal; Redraw RVT still open → fatal;
      blank Directory, negative tolerance, zero/negative spacing → named refusal;
      run with `Apply Updates` untouched → dry run only (the saved default is false).
- [ ] Dry run (`Apply Updates` false) from Main with Redraw closed: plans only, the
      Redraw document is opened for reading and closed again. Unchanged model → no
      geometry changes. A skipped cluster still appears in Geometry Updates with
      `SKIPPED` and its reason.
- [ ] Apply: REDRAW_ONLY parameter writes verified; REDRAW_ONLY geometry rebuilds
      walls, moves room, recenters its tags in every view that has them; a stand the
      Redraw added gets its Room **and** Room Tags in every view that has tags;
      **one Ctrl+Z puts Main back exactly where it was** (no link, no bands) — and
      the preview-clear transaction is not undone by it.
- [ ] Attribute-only wall edit: change a wall's type, height, offset, flip or level
      without moving it in the Redraw → the cluster is planned and the wall is
      replaced (it must not report `ALREADY`).
- [ ] Hosted elements: put a door or window in a Main wall that the cluster would
      replace/delete → the cluster is skipped, the issue names the hosted element,
      and the door survives.
- [ ] Sold stands: set `Status` = `Sold` on a stand, resize it in the Redraw → Apply
      is `REFUSED`, nothing is written, the message names the stand, and one
      `REFUSED` row lands in the audit log; Preview marks the cluster `SKIPPED`
      and reports `SOLD STAND AREA`. Delete a sold stand in the Redraw → also
      refused. Move a sold stand without changing its area → the run applies and
      says `SUCCESS`. Change its shape at the same area (e.g. 3x3 → 4.5x2) → the
      run applies and says `SUCCESS WITH WARNINGS` with `SOLD STAND DIMENSIONS`.
- [ ] Sold stands with no `Status`: save Main and run Apply within two minutes →
      `REFUSED` with the wait-and-rerun message; Preview reports
      `SOLD STATUS UNAVAILABLE` and still draws. After the CRM import, the same
      run is clean and `Sold status` reads `PRESENT`.
- [ ] Tolerance: a wall 2 mm out of position in the Redraw is replaced, not kept
      (the saved default is 0.0005 m). Confirm the `retained` count stays at zero
      on an untouched stand, i.e. the tight tolerance does not turn Revit's own
      rounding into phantom Main work.
- [ ] Duplicate/blank Numbers: two Rooms with the same Number → the geometry
      cluster is quarantined; the duplicated stand's parameters stay as Main has
      them, while other stands in the cluster can still get parameter updates. A
      blank-Numbered Room sharing a wall with a stand → the shared wall is not
      deleted.
- [ ] Cluster failure: force one cluster to fail (e.g. a wall type that exists only
      at apply time) → only that cluster is rolled back and reported `FAILED`; the
      other clusters are `VERIFIED` and the run says `COMPLETED WITH ERRORS`.
- [ ] Status/audit: a clean apply → `SUCCESS`; one skip or conflict →
      `SUCCESS WITH WARNINGS` and `Message` carries the reason; `Audit` is `OK`.
      `merge-log.csv` gains one row per cluster plus one per parameter, and
      `merge-summary.csv` is rewritten, all next to the Redraw.
- [ ] Preview removal: after an apply, the previous run's bands and wall colours are
      gone — including bands left by the *older* link-based runs, which carry no name
      marker and are found by the override sweep.
- [ ] Level mismatch (Main or Redraw room on a different level) → cluster skipped
      with its reason. A wall moved to another level in the Redraw is rebuilt on
      that level when the level exists in Main.
- [ ] Project number mismatch → fatal; missing number → `IDENTITY WARNING` visible
      in the issues watch; Main losing a stand the Redraw still has → `MAIN REMOVED`
      issue.
- [ ] New `Center` baseline and legacy `Finish` baseline both work; Main/Redraw/
      Verify use the baseline's boundary location.
- [ ] Old baseline (pre-MVP2.4C, no level metadata / `merge_parameters`) still passes
      validation and behaves sanely.
- [ ] The Redraw document is closed at the end of every run, including a failed one;
      Main is the active document again.
- [ ] Conflicts (both sides changed) → skipped/reported unless `Redraw Wins On
      Conflicts` is set; non-RX wall types and Room Separation Line boundaries →
      skipped/reported, never modified.
- [ ] `Redraw Wins On Conflicts` with a stand re-created at a new location (same
      Number in Main and the Redraw, no shared wall element ids): Main's old walls
      are deleted, the Redraw's walls created, and the Room moved.

## 2. Known limitations (deliberate)

- **Geometry scope**: REDRAW_ONLY changes only; Main walls that are deleted or
  replaced must be entirely `Closed Wall - RX` / `Open Wall - RX` and unshared in
  Main. Added stands are created and removed stands deleted, but a cluster is
  skipped (not fixed) on a level mismatch, a conflict (unless `Redraw Wins On
  Conflicts` is set), a missing wall type or level in Main, or a hosted element.
- **Room Separation Lines unsupported** anywhere in the geometry path.
- **A wall hosting doors/windows/openings is never rebuilt** — deleting the wall
  would cascade the hosted elements and the merge cannot recreate them, so the
  cluster is skipped and the hosted elements are named in the issue.
- **Unmanaged Rooms are protected, not merged** — a blank-Numbered Room is skipped,
  a duplicate Room Number quarantines its cluster geometry, and any wall those
  Rooms share is protected from deletion. The duplicated stand's parameters stay
  as Main has them; other stands in the cluster can still get parameter updates.
  A cluster that would remove a wall separating a stand from an unmanaged Room is
  skipped.
- **Level changes of a Room are still skipped**; a wall moved to another level is
  rebuilt on that level only when the level exists in Main.
- **Tags**: a new stand is tagged in every view that already carries Room Tags; a
  moved stand's tags are re-centred in every view that has them. A view that refuses
  the new tag is reported, not fatal.
- **Wall rebuild** copies type/height/offset/flipped and the wall's own level from
  the Redraw; height fallback 10 ft; joins and wall-type variations beyond the two
  RX types are not handled.
- **Curved walls match by sampling, not analytically** — `wall_points` gives a
  `Line` two endpoints and any other curve 17 sampled points, and `curve_distance`
  resamples at the spacing, so a curved wall's matching resolution is coarser than
  the 0.5 mm tolerance (chord error is a few millimetres). An unchanged arc still
  matches exactly, because both sides sample the same geometry, so a curved stand
  is not misread as changed. What is not exact is how small a real difference a
  curved wall can reveal. Straight walls compare by endpoints and are exact. Do
  not lower the spacing to compensate: `curve_distance` is O((length/spacing)^2)
  and the verification runs it per cluster.
- **Sold-stand detection reads a parameter the CRM plugin owns** — `Status` is in
  Main only and is deleted on save, so for about two minutes after a save an apply
  is refused (`REFUSED`, wait-and-rerun) and a preview reports
  `SOLD STATUS UNAVAILABLE`. Only `sold` blocks; `OnHold` is deliberately ignored.
  Whether a stand became sold *during* the redraw cannot be detected, because the
  snapshot cannot record `Status` (graph 1 saves Main, which deletes it); that
  history lives in the CRM.
- **Cloud/temp cleanup is best effort** — graph 1 tries to reactivate Main and
  close/delete the temp central, but Revit may refuse to close a document hosting
  the Dynamo run. In that case the summary warns and the operator must clean up
  manually.
- **Graph 2 opens the Redraw document during the run** — it appears in Revit's
  document switcher and is closed again at the end; if the close is refused, the
  Summary says `Redraw document closed: NO` and QA names it.
- **Directory inputs ship blank** — operators must select the folder on every run.
  This is intentional: local paths are not published.
- **Validated in Revit 2027** — the rebuilt graph passed the in-Revit checklist.
  The offline tests in `src/test_*.py` and `tools/check_repo.py` still cover the
  planner, merge core and static contracts. Use section 1 as the regression gate
  after any Revit or Dynamo upgrade. `.dyn` edits remain hand/programmatic JSON
  surgery (see `AGENTS.md`).
- Graph 2's apply transaction is still named `MVP2.4B6 - Merge Redraw Updates`
  (kept for continuity); transaction names retain MVP prefixes even though the
  graph `Name` fields are now `Create Redraw` / `Compare and Update Redraw`.

## 3. Candidate future features

Ordered roughly by value/effort; none are committed.

1. **Sample fixture** — add a small sample Main/Redraw pair checked into the repo
   as a manual test fixture. The in-Revit regression pass (section 1) is complete.
2. **Harden cloud temp-central cleanup**: graph 1 now attempts to reactivate Main,
   close/delete the temp central and close the Redraw; verify this in Revit. If
   closing the host document is refused, investigate a supported cloud→local detach
   path or a safe deferred close (e.g. Idling event) instead of the manual fallback.
3. **Room Separation Line support** in boundaries (eligibility, rebuild, verify).
4. **Conflict resolution UX**: today conflicts are listed and skipped unless the
   global `Redraw Wins On Conflicts` flag is set; a future graph could let the
   operator choose Main-wins/Redraw-wins per stand or per parameter.
5. **Level-change support for Rooms**: currently skipped; could allow deliberate
   level moves with wall level reassignment.
6. **Hosted-element handling**: instead of skipping a cluster whose wall hosts a
   door/window/opening, move or recreate the hosted element with the wall.
7. **Audit trail follow-ups**: persist the QA log and the baseline identity beside
   the CSVs. A cluster is now reported as `MOVED` when a Room's location point
   moved and `RESIZED` when only its boundary changed, so the summary's
   `clusters_resized` column is meaningful. Extending the audit further would
   change the one-RVT/one-JSON folder contract, so it needs care.
8. **Transaction/version hygiene**: decide whether to drop MVP prefixes from the
   apply transaction name; keep `schema_version` bumps coordinated across both
   graphs.
9. **Parameterise wall types / merged parameters** instead of hardcoding, if the
   business rules start varying per project (keep the cross-graph contract check).

## 4. Contract rules for any future change

- Baseline schema changes must update **both** graphs and `schema_version`;
  graph 2 validates schema/snapshot/filenames, `Center` or legacy `Finish`,
  `merge_parameters` and project number, and will reject mismatches.
- The 9 audited vs 6 merged parameter lists are a business rule; change them in both
  graphs together (`AUDIT_PARAMETERS` / `MERGE_PARAMETERS` in graph 1, `PARAMS` in
  graph 2) and update `AGENTS.md`/`DESIGN.md`.
- Keep the folder contract: exactly one primary `.rvt` + one `*.baseline.json`
  (the two merge CSVs are additional files and are gitignored).
- The audit CSVs are an append/rewrite contract: keep the header names in
  `AUDIT_LOG_COLUMNS` / `AUDIT_SUMMARY_COLUMNS` stable, or bump a version, and keep
  the log append-only so past runs are not rewritten.
- Keep geometry conservative: when in doubt, skip and report rather than auto-fix.
- Preserve `.dyn` CRLF JSON and escaped `\r\n` code when editing programmatically.
