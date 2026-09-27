# Verdict — browser-vision M1.1 + M1.2 cert audit

- **Verdict:** `SHIP` for **M1.1 + M1.2** scope.
- **Not a full M1/G1 sign-off.** G1 evidence and `test-output-g1.txt` are not certified by this verdict.
- **Auditor:** Sarah (QA Cert Lead)
- **Date:** 2026-09-27
- **Spec:** `/projects/browser-vision-extension/docs/spec-browser-vision-m1.md`
- **Build claim:** M1.1 + M1.2 complete, 78/78 tests green
- **Evidence root:** `/projects/browser-vision-extension`

## Scope

This audit certifies:

- **M1.1** — host contract and extension scaffold, including §2.5 extension card and §2.6 CLI invocation pattern.
- **M1.2** — coordinate-grid protocol.

This audit observed, but does not formally certify:

- M1.3/M1.4 tool implementations and tests.
- G1 evidence items E1–E5.
- M1.6 `extension/test-output-g1.txt`.

## Verdict criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | M1.1 extension layout matches §2.1 | PASS | `extension/` contains `manifest.json`, `extension-card.json`, `index.js`, `cli.js`, `tools/`, `grid/`, `capture/`, `test/`, `package.json` |
| 2 | `manifest.json` matches §2.2 | PASS | `/projects/browser-vision-extension/extension/manifest.json` has exact name, version, description, entry, tools, and lifecycle contract |
| 3 | Lifecycle hooks conform to §2.3 | PASS | `index.js` exports `register`, `init`, `destroy`; `register()` does not start browser; `init()` creates `HeadlessCapture`; `destroy()` closes capture and is idempotent |
| 4 | Tool registration contract conforms to §2.4 | PASS | `register()` calls `host.registerTool()` for exactly `view`, `click`, `type`, `scroll`; each tool exposes `name`, `description`, `params`, `handler` |
| 5 | Extension card conforms to §2.5 | PASS | `/projects/browser-vision-extension/extension/extension-card.json` parses, matches manifest `name`/`version`, lists the four tools in manifest order, and has non-empty one-liners |
| 6 | CLI invocation pattern conforms to §2.6 | PASS | `cli.js` exports `parseBvCommand` and `dispatchBvCommand`; unknown tool, missing params, invalid JSON, and handler-error paths conform to the dispatcher error contract |
| 7 | M1.1 steps complete per §2.7 | PASS | Scaffold files exist; lifecycle, extension card, and CLI dispatcher are covered by `test/lifecycle.test.js` |
| 8 | M1.2 grid protocol conforms to §3 | PASS | `grid/protocol.js` implements `VIEWPORT`, `setPrecision`, `validateCoordinate`, `cellCenter`, `gridMetadata` |
| 9 | M1.2 edge cases covered per §6.2 | PASS | `test/grid-protocol.test.js` covers N=1, N=960, N=0, negative, >960, non-integer, string precision, and boundary coordinates |
| 10 | Test plan files present per §6.1 | PASS | All 7 required test files exist under `extension/test/` |
| 11 | Test run green | PASS | Auditor reran jest: 7 suites passed, 78 tests passed |
| 12 | M1.1/M1.2 touch-list files present per §8 | PASS | Required M1.1/M1.2 files exist; M1.3/M1.4 files also exist but are outside this verdict scope |
| 13 | Regression coverage | PASS with note | No prior baseline exists; full available suite is green, so no regression was observed in this audit |

## Evidence reviewed

### File inspection

- `/projects/browser-vision-extension/docs/spec-browser-vision-m1.md`
- `/projects/browser-vision-extension/extension/manifest.json`
- `/projects/browser-vision-extension/extension/extension-card.json`
- `/projects/browser-vision-extension/extension/index.js`
- `/projects/browser-vision-extension/extension/cli.js`
- `/projects/browser-vision-extension/extension/package.json`
- `/projects/browser-vision-extension/extension/grid/protocol.js`
- `/projects/browser-vision-extension/extension/capture/headless.js`
- `/projects/browser-vision-extension/extension/tools/view.js`
- `/projects/browser-vision-extension/extension/tools/click.js`
- `/projects/browser-vision-extension/extension/tools/type.js`
- `/projects/browser-vision-extension/extension/tools/scroll.js`
- `/projects/browser-vision-extension/extension/test/grid-protocol.test.js`
- `/projects/browser-vision-extension/extension/test/lifecycle.test.js`
- `/projects/browser-vision-extension/extension/test/view.test.js`
- `/projects/browser-vision-extension/extension/test/click.test.js`
- `/projects/browser-vision-extension/extension/test/type.test.js`
- `/projects/browser-vision-extension/extension/test/scroll.test.js`
- `/projects/browser-vision-extension/extension/test/preclick.test.js`
- `/projects/browser-vision-extension/extension/test/fixtures/test-page.html`

### Test run

Command used:

```text
node /projects/browser-vision-extension/extension/node_modules/.bin/jest \
  --config /projects/browser-vision-extension/extension/jest.config.js \
  --rootDir /projects/browser-vision-extension/extension \
  --testPathPattern test/ \
  --verbose
```

Result:

```text
Test Suites: 7 passed, 7 total
Tests:       78 passed, 78 total
```

This confirms Charles’s 78/78 claim for the working tree.

## Conformance notes

### M1.1 scaffold

- `manifest.json` matches the §2.2 contract exactly.
- `extension-card.json` matches the §2.5 contract and is separate from `manifest.json`.
- `index.js` exports `register`, `init`, `destroy`, and `getHandlers`.
- `register()` registers exactly the four manifest tools and does not start a browser session.
- `init()` creates the `HeadlessCapture` instance.
- `destroy()` closes the capture instance and is safe to call twice.
- `cli.js` implements the scaffold-level `bv` dispatcher without implementing tool logic.
- Dispatcher errors conform to:
  - `UNKNOWN_TOOL`
  - `MISSING_PARAMS_JSON`
  - `INVALID_PARAMS_JSON`
  - handler error returned unchanged

### M1.2 grid protocol

- Viewport is fixed at `960 × 960`.
- `setPrecision(N)` rejects non-integer, zero, negative, and `N > 960`.
- `cols` and `rows` are derived as `floor(960 / N)`.
- `validateCoordinate(N, col, row)` rejects out-of-bounds coordinates without clamping.
- `cellCenter(N, col, row)` uses the §3.4 formula.
- `gridMetadata(N)` matches the §3.3 schema.

## Gaps and limitations

### 1. No commit SHA or PR evidence

The repository has no commits yet:

```text
fatal: your current branch 'master' does not have any commits yet
```

`git status` shows:

```text
?? docs/
?? extension/
```

This does not fail the M1.1/M1.2 functional bar, because the spec does not require a commit for M1.1/M1.2. However, it means this verdict is pinned to the working tree, not to a commit SHA or PR.

If the operator requires revision-pinned sign-off, Charles should commit the build and provide the commit SHA or PR URL before final operator acceptance.

### 2. `test-output-g1.txt` is missing

Required by §7 and §8 for G1/M1.6:

```text
extension/test-output-g1.txt
```

This file is not present:

```text
Path not found: /projects/browser-vision-extension/extension/test-output-g1.txt
```

This does not block the M1.1 + M1.2 verdict, but it does block full M1/G1 certification.

### 3. G1 evidence E1–E5 not independently verified

This audit did not independently verify:

- E1: BM host loads the extension
- E2: lifecycle starts and stops cleanly in the host
- E3: host tool registry dump
- E4: `view` returns a real headless-browser screenshot
- E5: screenshot result includes coordinate-grid metadata in a host run

The test suite covers the relevant contracts, but full G1 requires the named evidence artifacts.

## Handoff

### For M1.1 + M1.2

No build gap blocks this scope. The build may proceed.

### For full M1 / G1

Charles must provide:

1. `extension/test-output-g1.txt` from a full jest run.
2. Evidence for G1 items E1–E5.
3. A commit SHA or PR URL if revision-pinned evidence is required.

After those exist, request re-cert for full M1/G1.
