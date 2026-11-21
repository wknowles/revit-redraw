# Privacy audit — Dynamo graphs (2026-11-21)

Scope: `1. Create Redraw.dyn`, `2. Compare and Update Redraw.dyn`, and the repo
Markdown docs. Goal: publish in an open git repo without exposing personal or
confidential information. The removed path is deliberately not reproduced here.

## Findings and remediation

1. **Windows absolute directory path (fixed).**
   Each graph contained the same path in three places:
   - `Inputs[0].Value`
   - the Directory Path node's `HintPath`
   - the Directory Path node's `InputValue`

   The path exposed a Windows username, an employer/company name, and a
   project-folder name. All three occurrences per graph are now `""`. The graphs
   still require the operator to select the folder at run time.

2. **No other PII found in the `.dyn` files.**
   Checked all serialized strings and metadata: no human names, email addresses,
   phone numbers, postal addresses, IP addresses, URLs, project numbers, or sample
   `.baseline.json` content.

3. **Metadata checked.**
   `Author` is empty, `Thumbnail` is empty (no graph screenshot), and
   `Dependencies`, `Bindings` and `NodeLibraryDependencies` are empty.
   `Description` is empty and `GraphDocumentationURL` is absent.

4. **Identifiers are not PII.**
   Graph UUIDs, node IDs, connector IDs and view IDs remain. They identify the
   Dynamo graph structure, not a person. Embedded Python contains workflow terms and
   business parameter names only.

5. **Docs.** No local paths, usernames, company names or project codes are included;
   the Directory-input note now says the inputs ship blank.

## Residual risks and pre-publish checklist

- Do **not** commit `.baseline.json` sidecars or `.rvt` models. Baselines can contain
  project name/number and room/parameter data; models contain far more.
- Do **not** commit Revit screenshots or exported schedules.
- If adding example baselines or fixtures later, sanitize project name/number, room
  numbers, stand parameter values, file paths and workset/user names.
- Re-scan before publishing for: drive-letter user-profile paths, cloud-sync folder
  names, the old username/company, project codes, UNC paths, POSIX home paths, email
  addresses and URLs.
- Dynamo can repopulate `Thumbnail` or `Author` when the graph is saved; re-check
  those fields before each public release.
- This audit is point-in-time. Re-run it after any graph edit that adds inputs,
  examples, screenshots or sample data.
