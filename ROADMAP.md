# Roadmap — known gaps, deferred features, test checklist

Companion to `DESIGN.md` (current behaviour) and `CHANGELOG.md` (what changed).
Everything here is intentionally out of scope today; treat it as the input list for
future feature work.

## 1. Must verify in Revit 2027 before trusting the MVP2.4C build

No Revit/Dynamo runtime was available during the build; only static checks and
mocked pure-function tests passed. Run this checklist first:

### Graph 1
- [ ] Non-workshared Main: saves, Redraw + baseline created, **Redraw becomes active**,
      baseline counts/geometry/parameters look right.
- [ ] Local workshared Main: syncs without relinquishing, **Main stays active**,
      Redraw opens detached with no worksets, baseline written.
- [ ] Cloud workshared Main: syncs, temp local central created, Redraw detached
      correctly, temp2 deleted, QA warning names the temp central; operator can
      close/delete it and reopen Main.
- [ ] Cloud non-workshared Main: `SaveCloudModel` path works; direct `SaveAs` to a
      local Redraw succeeds.
- [ ] Refusals: existing `.rvt`/`.baseline.json` pair; another primary RVT or baseline
      in the folder; never-saved local file; duplicate/blank Room Numbers; unplaced
      and unbounded rooms retained with correct statuses.
- [ ] Failure atomicity: force a `SaveAs` failure and confirm no orphan `.pending`
      sidecar and a clear QA warning about a partial Redraw.

### Graph 2
- [ ] Dry run (`Apply Updates` false): plans only, link loaded then removed.
- [ ] Apply: REDRAW_ONLY parameter writes verified; REDRAW_ONLY geometry rebuilds
      walls, moves room, recenters tags in the active view; **Undo is one step**.
- [ ] Level mismatch (Main or Redraw room on a different level) → geometry skipped,
      parameters still merged.
- [ ] Project number mismatch → fatal; missing number → `IDENTITY WARNING` visible
      in the issues watch.
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
- **Cloud workshared flow leaves a temporary local central open** — manual cleanup
  by the operator; graph 2 must not be run until Main is reopened.
- **Graph 2 `qa` (`OUT[4]`) is not wired**; fatal reasons are only visible via the
  Summary status or by inspecting the node. Watch labels for `gout`/`issues` are
  misnamed ("Parameter Conflicts"/"Errors").
- **Directory inputs ship blank** — operators must select the folder on every run.
  This is intentional: local paths are not published.
- **No automated tests**; `.dyn` edits are hand/programmatic JSON surgery
  (see `AGENTS.md` verification recipe). Pre-MVP2.4C backups exist only in the
  session temp folder and are ephemeral.
- Graph 2's apply transaction is still named `MVP2.4B6 - Merge Redraw Updates`
  (kept for continuity); graph names in the `.dyn` metadata are stale
  ("MVP 2.3A", "2.1 Redraw Comparison (Auto)").

## 3. Candidate future features

Ordered roughly by value/effort; none are committed.

1. **Surface graph 2 QA**: wire `OUT[4]` to a watch (add a `List.GetItemAtIndex` +
   code-block index) and fix the `gout`/`issues` watch labels. Small, high value —
   fatal reasons are currently invisible.
2. **In-Revit regression pass** (section 1) and then a small sample Main/Redraw pair
   checked into the repo as a manual test fixture.
3. **Add a pre-commit check** for JSON validity and embedded `py_compile` (the
   `AGENTS.md` recipe automated); the repo and initial commit already exist.
4. **Automate cloud temp-central cleanup**: close/delete the temporary central via
   API after the detached `SaveAs`, or investigate a supported cloud→local detach
   path that avoids the temp central entirely. Needs careful Revit testing; the
   current manual fallback is the safe baseline.
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
11. **Version/metadata hygiene**: bump graph `Name` fields and transaction names
    together; keep `schema_version` bumps coordinated across both graphs.
12. **Parameterise wall types / merged parameters** instead of hardcoding, if the
    business rules start varying per project (keep the cross-graph contract check).

## 4. Contract rules for any future change

- Baseline schema changes must update **both** graphs and `schema_version`;
  graph 2 validates schema/snapshot/filenames/`Finish`/`merge_parameters`/project
  number and will reject mismatches.
- The 9 audited vs 6 merged parameter lists are a business rule; change them in both
  graphs together (`AUDIT_PARAMETERS` / `MERGE_PARAMETERS` in graph 1, `PARAMS` in
  graph 2) and update `AGENTS.md`/`DESIGN.md`.
- Keep the folder contract: exactly one primary `.rvt` + one `*.baseline.json`.
- Keep geometry conservative: when in doubt, skip and report rather than auto-fix.
- Preserve `.dyn` CRLF JSON and escaped `\r\n` code when editing programmatically.
