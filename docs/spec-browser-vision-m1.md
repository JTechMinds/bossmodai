# Browser-Vision Extension — M1 Implementation TDD

Project: `browser-vision-extension`
Spec owner: Brian (Tech Spec Author)
Upstream: `plan-browser-vision-extension.md` (Harley, locked)
Status: active — junior-executable; revised 2026-09-27 for non-breaking M1.1 addendum (§2.5, §2.6)
Next pipeline: Charles (Build Engineer) → Sarah (QA Cert Lead)

## 1. Scope and Upstream Bar

- This TDD translates the locked M1 milestone plan into exact build steps.
- The locked bar is inherited from the plan; this spec does not re-litigate scope.
- If a step reveals the locked scope cannot be met, stop and return to Harley + Brad.
- M1 is a POC slice: headless-browser only, no Xvfb, no security model, no full marketplace.

In scope (locked):

- Extension scaffold with lifecycle hooks and tool registration.
- Four tools: `view`, `click`, `type`, `scroll`.
- Coordinate-grid protocol with adjustable precision parameter (`set_precision: N`).
- Pre-click re-screenshot before every `click`.
- Install surface entry: `Add Agent / Agent Marketplace / Extensions (flat dropdown, three items)`.

Out of scope (locked):

- M2 (Xvfb desktop swap), M3 (security model), production hardening.

Non-breaking addendum (2026-09-27): M1.1 also includes an extension card (§2.5) and a scaffold-level CLI invocation pattern (§2.6). These are additive scaffold-layer items. They do not change M1.2, M1.3, or M1.4 contracts and do not require restarting M1.2.

## 2. Host Contract and Extension Scaffold (M1.1)

The following scaffold sections are normative for M1.1. §2.5 and §2.6 are non-breaking addenda; they are scaffold-layer only and must not change tool-handler behavior.

### 2.1 Extension package layout

```
extension/
  manifest.json        # extension metadata, tool list, lifecycle entry points
  extension-card.json  # extension card metadata block (§2.5)
  index.js             # main entry: register() / init() / destroy()
  cli.js               # scaffold-level CLI dispatcher (§2.6)
  tools/
    view.js            # view tool handler
    click.js           # click tool handler
    type.js            # type tool handler
    scroll.js          # scroll tool handler
  grid/
    protocol.js        # coordinate-grid metadata + setPrecision logic
  capture/
    headless.js        # headless-browser screenshot + action execution
  test/
    (test files per §6)
```

### 2.2 manifest.json contract

```json
{
  "name": "browser-vision",
  "version": "0.1.0-m1",
  "description": "Browser-vision POC: headless capture + coordinate-grid actions",
  "entry": "index.js",
  "tools": ["view", "click", "type", "scroll"],
  "lifecycle": {
    "register": "register",
    "init": "init",
    "destroy": "destroy"
  }
}
```

### 2.3 Lifecycle hooks

| Hook | Trigger | Contract |
|---|---|---|
| `register()` | Host loads extension | Must register all 4 tools with the host tool registry. Must not start a browser session. |
| `init()` | Host activates extension | Must create the headless-browser instance (or lazy-init on first `view`). Must not block > 2 s. |
| `destroy()` | Host unloads extension | Must close the browser instance, release resources. Must be idempotent. |

### 2.4 Tool registration contract

Each tool registered with the host must expose:

- `name`: string, one of `view` | `click` | `type` | `scroll`.
- `description`: human-readable one-liner.
- `params`: JSON-schema object describing required inputs.
- `handler(params) → result`: async function. The host calls this; the extension returns a structured result.

The host must reject calls to unregistered tools. The extension must not register tools not listed in `manifest.json`.

### 2.5 Extension Card (non-breaking addendum)

Purpose: provide a compact metadata card for host display, marketplace listing, or operator inspection. It is separate from `manifest.json`.

Artifact: `extension/extension-card.json`.

Contract:

```json
{
  "name": "browser-vision",
  "purpose": "Headless browser capture and coordinate-grid actions for agent use.",
  "version": "0.1.0-m1",
  "tools": [
    { "name": "view", "one_liner": "Load a URL and return a screenshot with coordinate-grid metadata." },
    { "name": "click", "one_liner": "Click a grid cell after a mandatory pre-click screenshot." },
    { "name": "type", "one_liner": "Type text into a focused grid cell on a fine-grained grid." },
    { "name": "scroll", "one_liner": "Scroll from a grid cell in a direction on a coarse or medium grid." }
  ]
}
```

Rules:

- Must be valid JSON.
- `name` and `version` must exactly match `manifest.json`.
- `purpose` must be one sentence.
- `tools` must list exactly the four tools in the same order as `manifest.json.tools`.
- Each `one_liner` must be a single sentence consistent with the corresponding §4 tool contract.
- The card is informational/display metadata. It does not register tools and does not replace `manifest.json`.
- Non-breaking: tool handlers in `tools/` must not read or depend on the card at runtime.

### 2.6 CLI Invocation Pattern (non-breaking addendum)

Purpose: define one agent-to-extension entry point at scaffold level.

Command shape:

```
bv <tool> --params-json '{...}'
```

Where:

- `bv` is the scaffold-level command surface implemented by `extension/cli.js`.
- `<tool>` is one of `view`, `click`, `type`, `scroll`.
- `--params-json` is a single JSON object string matching the tool's params contract in §4.

Examples:

```
bv view --params-json '{"url":"https://example.com","precision":48}'
bv click --params-json '{"col":9,"row":9,"precision":48}'
bv type --params-json '{"col":9,"row":10,"precision":24,"text":"hello"}'
bv scroll --params-json '{"col":9,"row":9,"precision":48,"direction":"down","amount":2}'
```

Scaffold contract:

- `extension/cli.js` exports `parseBvCommand(argv)` and `dispatchBvCommand(handlers, tool, paramsJson)`.
- `parseBvCommand(argv)` accepts an argument array without the Node/process prefix. It returns `{ tool, paramsJson, error? }`.
- `dispatchBvCommand(handlers, tool, paramsJson)` parses `paramsJson`, looks up `handlers[tool]`, calls `handler(params)`, and returns the handler result.
- `index.js` exports `getHandlers()` returning the handler map used by `register()`.
- The CLI dispatcher must not implement tool logic.
- The CLI dispatcher must not call Playwright directly.
- The CLI dispatcher must not bypass precision, coordinate, or page-state validation inside tool handlers.
- The host or test harness owns lifecycle: call `register()` once, dispatch one or more commands, then `destroy()`.
- For M1, `cli.js` may be used in-process by the host harness. A standalone `bv` executable is not required for M1.

Dispatcher error contract:

| Condition | Result |
|---|---|
| Unknown tool | `{ "error": "UNKNOWN_TOOL", "detail": "..." }` |
| Missing `--params-json` | `{ "error": "MISSING_PARAMS_JSON" }` |
| Invalid JSON | `{ "error": "INVALID_PARAMS_JSON", "detail": "..." }` |
| Handler returns an error | Return the handler's error object unchanged. |

If a process wrapper is added later, it must print the JSON result to stdout, use exit code `0` for success, and exit code `1` for dispatcher or handler errors.

### 2.7 Steps for M1.1

1. Create `extension/` directory with the layout in §2.1.
2. Write `manifest.json` per §2.2.
3. Write `extension-card.json` per §2.5.
4. Implement `index.js` with `register()`, `init()`, `destroy()` stubs and `getHandlers()`.
5. Implement `cli.js` with `parseBvCommand` and `dispatchBvCommand` per §2.6.
6. In `register()`, call the host's `registerTool(name, description, params, handler)` for each of the 4 tools.
7. In `init()`, instantiate the headless-browser (Playwright — see §2.8).
8. In `destroy()`, call `browser.close()`.
9. Verify: host loads the extension, all 4 tools appear in the host tool registry, lifecycle starts and stops cleanly, `extension-card.json` validates, and the CLI dispatcher parses valid/invalid commands and dispatches to handler stubs.

### 2.8 Browser engine

Use **Playwright** (Node.js) for headless-browser control.

- `page.screenshot()` for capture.
- `page.mouse.click(x, y)`, `page.keyboard.type(text)`, `page.mouse.wheel(deltaX, deltaY)` for actions.
- No Xvfb dependency (headless mode).
- Deterministic timing via `page.waitForLoadState('networkidle')`.

Install inside the extension directory: `npm install playwright` + `npx playwright install chromium`.

## 3. Coordinate-Grid Protocol (M1.2)

### 3.1 Purpose

The coordinate-grid protocol maps a screenshot to a grid of addressable cells. The agent references cells by grid coordinate rather than raw pixel, reducing precision ambiguity.


### 3.2 Grid precision parameter (adjustable)

The viewport for M1 POC is **960 × 960 px** (fixed). All grid calculations assume this viewport.

Instead of a fixed set of named tiers, the agent selects grid precision by setting a single integer parameter:

- **`precision: N`** — grid spacing in pixels (the cell size).

Grid dimensions are derived from N:

- `cols = floor(960 / N)`
- `rows = floor(960 / N)`
- `cell_width_px = N`
- `cell_height_px = N`

When `960 % N != 0`, the last column and/or row will be narrower/shorter than N px. The cell-center formula in §3.4 still applies using the nominal N value; the agent should prefer coordinates within the full-size cells for precision-critical actions.

**Valid range:** `1 <= N <= 960`, positive integer only.

| N | Grid | Cell (px) | Use case |
|---|---|---|---|
| 96 | 10 × 10 | 96 × 96 | Broad navigation, scroll targets |
| 48 | 20 × 20 | 48 × 48 | Click targets, form fields (default) |
| 24 | 40 × 40 | 24 × 24 | Pixel-precise interactions, small buttons |
| 1 | 960 × 960 | 1 × 1 | Maximum density (edge case) |
| 960 | 1 × 1 | 960 × 960 | Minimum density (edge case) |

**Default:** `N = 48` (20 × 20 grid), equivalent to the former "medium" tier.

**Invalid values:** non-integer (e.g., 48.5), zero, negative, or N > 960 all produce `INVALID_PRECISION` error.

### 3.3 Grid metadata schema

Every `view` result and every action result must include:

```json
{
  "grid": {
    "precision": 48,
    "cols": 20,
    "rows": 20,
    "cell_width_px": 48,
    "cell_height_px": 48,
    "viewport": { "width": 960, "height": 960 }
  }
}
```

(Values shown for `N = 48`, the default. Adjust `cols`, `rows`, `cell_width_px`, and `cell_height_px` for other precision values per §3.2.)

### 3.4 Coordinate addressing

- A grid coordinate is `(col, row)` with 0-based indexing.
- `(0, 0)` = top-left cell. `(cols-1, rows-1)` = bottom-right cell.
- Center of cell `(c, r)` in pixels: `x = c * cell_width_px + cell_width_px / 2`, `y = r * cell_height_px + cell_height_px / 2`.

### 3.5 Precision selection rules

- `view` returns precision `48` by default.
- `click` accepts any valid precision `N`, where `1 <= N <= 960` and `N` is a positive integer. The extension validates the coordinate against the derived grid bounds.
- `type` accepts any valid precision `N` where `N <= 48` (fine-grained grids only).
- `scroll` accepts any valid precision `N` where `N >= 24` (coarse or medium-grained grids only).
- If the agent sends a coordinate out of bounds for the selected precision, the extension returns an error. Do not clamp.
- If `N` is not a positive integer, is less than 1, or is greater than 960, the extension returns `INVALID_PRECISION`.

### 3.6 Steps for M1.2

1. Create `grid/protocol.js`.
2. Implement `VIEWPORT = { width: 960, height: 960 }` constant.
3. Implement `setPrecision(N) → { valid: boolean, cols, rows, cell_width_px, cell_height_px, error?: string }` — validates N is a positive integer in [1, 960], derives `cols = floor(960 / N)` and `rows = floor(960 / N)`, returns `{ valid: false, error: "INVALID_PRECISION" }` for non-integer, zero, negative, or N > 960.
4. Implement `validateCoordinate(N, col, row) → { valid: boolean, error?: string }` — derives cols/rows from N, rejects any coordinate where `col < 0`, `col >= cols`, `row < 0`, or `row >= rows`.
5. Implement `cellCenter(N, col, row) → { x: number, y: number }` — computes pixel center of the given cell using the formula in §3.4.
6. Implement `gridMetadata(N) → object` matching the schema in §3.3 (fields: `precision`, `cols`, `rows`, `cell_width_px`, `cell_height_px`, `viewport`).
7. Unit-test all four functions including edge cases (see §6).

## 4. Tool Contracts (M1.3 + M1.4)

### 4.1 `view` tool

**Params:**

```json
{
  "url": { "type": "string", "required": true, "description": "Page URL to load and capture" },
  "precision": { "type": "integer", "required": false, "default": 48, "description": "Grid spacing in px (cell size). Must be a positive integer 1–960." }
}
```

**Result:**

```json
{
  "screenshot_path": "/tmp/bv-capture-<timestamp>.png",
  "screenshot_width": 960,
  "screenshot_height": 960,
  "grid": { "...": "per §3.3 schema" },
  "url": "<final URL after redirects>",
  "timestamp": "<ISO-8601>"
}
```

**Behavior:**

1. Navigate to `url` (or reuse current page if already loaded).
2. Wait for `networkidle`.
3. Capture screenshot at 960×960 viewport.
4. Validate `precision` via `setPrecision(N)`. If invalid, return `INVALID_PRECISION` error immediately (do not proceed).
5. Attach grid metadata for the requested precision (see §3.3).
6. Return structured result.

**Errors:**

- Navigation timeout (> 10 s): return `{ "error": "NAV_TIMEOUT", "detail": "..." }`.
- Invalid precision (non-integer, zero, negative, or > 960): return `{ "error": "INVALID_PRECISION", "detail": "precision must be a positive integer 1–960; got <value>" }`.

### 4.2 `click` tool

**Params:**

```json
{
  "col": { "type": "integer", "required": true },
  "row": { "type": "integer", "required": true },
  "precision": { "type": "integer", "required": true, "description": "Grid spacing in px (cell size). Must be a positive integer 1–960." }
}
```

**Result:**

```json
{
  "clicked_at": { "x": 480, "y": 480 },
  "pre_click_screenshot_path": "/tmp/bv-preclick-<timestamp>.png",
  "post_click_screenshot_path": "/tmp/bv-postclick-<timestamp>.png",
  "grid": { "...": "per §3.3 schema (precision field)" },
  "timestamp": "<ISO-8601>"
}
```

**Behavior (ordered — do not skip steps):**

1. Validate precision `N` via `setPrecision(N)`. Reject if invalid (`INVALID_PRECISION`).
2. Validate `(col, row)` against the derived grid bounds for `N`. Reject if out of bounds.
3. **Pre-click re-screenshot:** capture a fresh screenshot of the current page state. Save to `pre_click_screenshot_path`. This step is mandatory and non-optional.
4. Compute pixel center via `cellCenter(N, col, row)`.
5. Execute `page.mouse.click(x, y)`.
6. Wait 300 ms for page to settle.
7. Capture post-click screenshot. Save to `post_click_screenshot_path`.
8. Return structured result including both screenshot paths.

**Errors:**

- Invalid precision: `{ "error": "INVALID_PRECISION", "detail": "precision must be a positive integer 1–960; got 0" }`.
- Out-of-bounds coordinate: `{ "error": "COORD_OUT_OF_BOUNDS", "detail": "col 25 exceeds 20 cols for precision 48" }`.
- No page loaded (no prior `view` or navigation): `{ "error": "NO_PAGE", "detail": "Call view first" }`.
- Pre-click screenshot failure: `{ "error": "PRECLICK_CAPTURE_FAILED", "detail": "..." }` — do NOT proceed to click.

### 4.3 `type` tool

**Params:**

```json
{
  "col": { "type": "integer", "required": true },
  "row": { "type": "integer", "required": true },
  "precision": { "type": "integer", "required": true, "description": "Grid spacing in px (cell size). Must be a positive integer 1–48 for type (fine-grained grids only)." },
  "text": { "type": "string", "required": true }
}
```

**Result:**

```json
{
  "typed_at": { "x": 480, "y": 480 },
  "text_length": 42,
  "pre_type_screenshot_path": "/tmp/bv-pretype-<timestamp>.png",
  "grid": { "...": "per §3.3 schema (precision field)" },
  "timestamp": "<ISO-8601>"
}
```

**Behavior (ordered — do not skip steps):**

1. Validate precision `N` via `setPrecision(N)`. If invalid (non-integer, zero, negative, or > 960), return `INVALID_PRECISION` and stop.
2. Enforce type precision cap: if `N > 48`, return `PRECISION_NOT_ALLOWED` and stop. Type requires fine-grained grids only.
3. Validate `(col, row)` against the derived grid bounds for `N`. Reject if out of bounds.
4. **Pre-type screenshot:** capture a fresh screenshot of the current page state. Save to `pre_type_screenshot_path`. If capture fails, return `PRETYPE_CAPTURE_FAILED` and do NOT proceed to type.
5. Compute pixel center via `cellCenter(N, col, row)`.
6. Click the cell center to focus the field.
7. Wait 150 ms for focus.
8. Execute `page.keyboard.type(text)`.
9. Return structured result.

**Errors:**

- `precision > 48`: `{ "error": "PRECISION_NOT_ALLOWED", "detail": "type requires precision <= 48 (fine-grained); got 96" }`.
- Invalid precision (non-integer, 0, negative, > 960): `{ "error": "INVALID_PRECISION", "detail": "precision must be a positive integer 1–960; got 0" }`.
- Out-of-bounds coordinate: `{ "error": "COORD_OUT_OF_BOUNDS", "detail": "col 25 exceeds 20 cols for precision 48" }`.
- No page loaded (no prior `view` or navigation): `{ "error": "NO_PAGE", "detail": "Call view first" }`.
- Pre-type screenshot failure: `{ "error": "PRETYPE_CAPTURE_FAILED", "detail": "..." }` — do NOT proceed to type.

### 4.4 `scroll` tool

**Params:**

```json
{
  "col": { "type": "integer", "required": true },
  "row": { "type": "integer", "required": true },
  "precision": { "type": "integer", "required": true, "description": "Grid spacing in px (cell size). Must be a positive integer 24–960 for scroll (coarse/medium-grained grids only)." },
  "direction": { "type": "string", "enum": ["up", "down", "left", "right"], "required": true },
  "amount": { "type": "integer", "required": false, "default": 3, "description": "Number of scroll steps (each step = 1 cell height/width in px)" }
}
```

**Result:**

```json
{
  "scrolled_at": { "x": 480, "y": 480 },
  "delta": { "x": 0, "y": 144 },
  "post_scroll_screenshot_path": "/tmp/bv-postscroll-<timestamp>.png",
  "grid": { "...": "per §3.3 schema (precision field)" },
  "timestamp": "<ISO-8601>"
}
```

**Behavior (ordered — do not skip steps):**

1. Validate precision `N` via `setPrecision(N)`. Reject if invalid (`INVALID_PRECISION`).
2. Enforce scroll precision floor: if `N < 24`, return `PRECISION_NOT_ALLOWED` (scroll requires coarse/medium grids, `N >= 24`).
3. Validate `(col, row)` against the derived grid bounds for `N`. Reject if out of bounds.
4. Compute pixel center via `cellCenter(N, col, row)`.
5. Compute delta: `deltaY = ±(amount * N)` for up/down; `deltaX = ±(amount * N)` for left/right.
6. Move mouse to cell center, execute `page.mouse.wheel(deltaX, deltaY)`.
7. Wait 300 ms.
8. Capture post-scroll screenshot. Save to `post_scroll_screenshot_path`.
9. Return structured result.

**Errors:**

- Precision below floor: `{ "error": "PRECISION_NOT_ALLOWED", "detail": "scroll requires precision >= 24 (coarse/medium); got 12" }`.
- Invalid precision (non-integer, 0, negative, > 960): `{ "error": "INVALID_PRECISION", "detail": "precision must be a positive integer 1–960; got 0" }`.
- Out-of-bounds coordinate: `{ "error": "COORD_OUT_OF_BOUNDS", "detail": "col 40 exceeds 40 cols for precision 24" }`.
- No page loaded (no prior `view` or navigation): `{ "error": "NO_PAGE", "detail": "Call view first" }`.

## 5. Pre-Click Re-Screenshot (Mandatory Step)

This section is normative. The pre-click re-screenshot is not a suggestion.

### 5.1 Rule

Every `click` invocation MUST capture a fresh screenshot of the current page state immediately before executing the mouse click. The screenshot is saved to a timestamped file and its path is returned in the result.

### 5.2 Enforcement

- The `click` handler must call the capture function before `page.mouse.click()`.
- If the capture fails (browser crash, timeout), the handler MUST return an error and MUST NOT execute the click.
- The action log (see §5.3) must show the pre-click step as a distinct entry.

### 5.3 Action log format

Each tool invocation appends one line to `/tmp/bv-action-log.jsonl`:

```json
{
  "ts": "2026-09-27T13:00:00.000Z",
  "tool": "click",
  "step": "pre_click_screenshot",
  "screenshot": "/tmp/bv-preclick-1759114800000.png",
  "grid": { "precision": 48, "cols": 20, "rows": 20 }
}
{
  "ts": "2026-09-27T13:00:00.050Z",
  "tool": "click",
  "step": "execute",
  "at": { "x": 480, "y": 480 }
}
{
  "ts": "2026-09-27T13:00:00.400Z",
  "tool": "click",
  "step": "post_click_screenshot",
  "screenshot": "/tmp/bv-postclick-1759114800050.png"
}
```

G2 evidence requires that the action log shows the `pre_click_screenshot` step for every click.

## 6. Test Plan and Edge Cases

### 6.1 Test files

| File | Covers |
|---|---|
| `test/grid-protocol.test.js` | §3: `setPrecision`, `validateCoordinate`, `cellCenter`, `gridMetadata`; edge cases (N=1, N=960, non-integer, 0, negative, >960) |
| `test/lifecycle.test.js` | §2: register, init, destroy; tool registration; §2.5 extension card; §2.6 CLI invocation pattern |
| `test/view.test.js` | §4.1: view returns screenshot + grid metadata with precision parameter; default precision 48 |
| `test/click.test.js` | §4.2: click executes, pre-click screenshot present, precision validation, out-of-bounds rejected |
| `test/type.test.js` | §4.3: type executes, precision cap (N ≤ 48) enforced |
| `test/scroll.test.js` | §4.4: scroll executes, delta correct, precision floor (N ≥ 24) enforced |
| `test/preclick.test.js` | §5: pre-click screenshot is mandatory, click blocked on capture failure |

### 6.2 Key test cases

**grid-protocol.test.js:**

- `setPrecision(48)` returns `{ valid: true, cols: 20, rows: 20, cell_width_px: 48, cell_height_px: 48 }`.
- `setPrecision(96)` returns `{ valid: true, cols: 10, rows: 10, cell_width_px: 96, cell_height_px: 96 }`.
- `setPrecision(1)` returns `{ valid: true, cols: 960, rows: 960, cell_width_px: 1, cell_height_px: 1 }` (edge: maximum grid density).
- `setPrecision(960)` returns `{ valid: true, cols: 1, rows: 1, cell_width_px: 960, cell_height_px: 960 }` (edge: minimum grid density).
- `setPrecision(0)` returns `{ valid: false, error: "INVALID_PRECISION" }`.
- `setPrecision(-5)` returns `{ valid: false, error: "INVALID_PRECISION" }`.
- `setPrecision(961)` returns `{ valid: false, error: "INVALID_PRECISION" }`.
- `setPrecision(48.5)` (non-integer) returns `{ valid: false, error: "INVALID_PRECISION" }`.
- `setPrecision("48")` (string, not number) returns `{ valid: false, error: "INVALID_PRECISION" }`.
- `validateCoordinate(48, 0, 0)` returns `{ valid: true }`; `validateCoordinate(48, 19, 19)` returns `{ valid: true }`.
- `validateCoordinate(48, -1, 0)` returns `{ valid: false, error: "COORD_OUT_OF_BOUNDS" }`.
- `validateCoordinate(48, 20, 0)` returns `{ valid: false, error: "COORD_OUT_OF_BOUNDS" }`.
- `validateCoordinate(48, 0, 20)` returns `{ valid: false, error: "COORD_OUT_OF_BOUNDS" }`.
- `validateCoordinate(48, 20, 20)` returns `{ valid: false, error: "COORD_OUT_OF_BOUNDS" }`.
- `validateCoordinate(1, 959, 959)` returns `{ valid: true }` (N=1 edge: 960×960 grid, last cell).
- `validateCoordinate(1, 960, 0)` returns `{ valid: false, error: "COORD_OUT_OF_BOUNDS" }` (N=1 edge: one past last col).
- `validateCoordinate(960, 0, 0)` returns `{ valid: true }` (N=960 edge: 1×1 grid).
- `validateCoordinate(960, 1, 0)` returns `{ valid: false, error: "COORD_OUT_OF_BOUNDS" }` (N=960 edge: one past last col).
- `cellCenter(48, 0, 0)` = `{ x: 24, y: 24 }`; `cellCenter(48, 19, 19)` = `{ x: 936, y: 936 }`.
- `cellCenter(96, 0, 0)` = `{ x: 48, y: 48 }`; `cellCenter(96, 9, 9)` = `{ x: 912, y: 912 }`.
- `cellCenter(1, 0, 0)` = `{ x: 0.5, y: 0.5 }`; `cellCenter(1, 959, 959)` = `{ x: 959.5, y: 959.5 }`.
- `cellCenter(960, 0, 0)` = `{ x: 480, y: 480 }`.
- `gridMetadata(48)` returns `{ precision: 48, cols: 20, rows: 20, cell_width_px: 48, cell_height_px: 48, viewport: { width: 960, height: 960 } }`.
- `gridMetadata(1)` returns `{ precision: 1, cols: 960, rows: 960, cell_width_px: 1, cell_height_px: 1, ... }`.
- `gridMetadata(960)` returns `{ precision: 960, cols: 1, rows: 1, cell_width_px: 960, cell_height_px: 960, ... }`.

**lifecycle.test.js:**

- After `register()`, host tool registry contains exactly 4 tools.
- After `destroy()`, browser instance is closed; calling `destroy()` twice does not throw.
- Calling a tool before `register()` throws or returns an error.
- `extension/extension-card.json` exists and parses as JSON.
- Extension card `name` and `version` match `manifest.json`.
- Extension card lists exactly `view`, `click`, `type`, `scroll` in manifest order, each with a non-empty `one_liner`.
- `parseBvCommand(["view", "--params-json", '{"url":"https://example.com"}'])` returns `{ tool: "view", paramsJson: '{"url":"https://example.com"}' }`.
- `parseBvCommand(["bogus", "--params-json", "{}"])` returns an `UNKNOWN_TOOL` error.
- `parseBvCommand(["view"])` returns a `MISSING_PARAMS_JSON` error.
- `parseBvCommand(["view", "--params-json", "not-json"])` returns an `INVALID_PARAMS_JSON` error.
- `dispatchBvCommand(handlers, "view", '{"url":"https://example.com"}')` calls the `view` handler with the parsed params object and returns the handler result.
- `dispatchBvCommand(handlers, "view", "not-json")` returns `INVALID_PARAMS_JSON` without calling the handler.

**view.test.js:**

- `view({ url: "https://example.com" })` returns a screenshot file that exists on disk.
- Returned grid metadata reflects the requested precision (e.g., `precision: 96` → `cols: 10, rows: 10`).
- Default precision is `48` when omitted (`grid.cols = 20`).
- `view({ url: "...", precision: 0 })` returns `INVALID_PRECISION` error.
- `view({ url: "...", precision: 48.5 })` returns `INVALID_PRECISION` error.
- `view({ url: "...", precision: -1 })` returns `INVALID_PRECISION` error.
- `view({ url: "...", precision: 961 })` returns `INVALID_PRECISION` error.

**click.test.js:**

- Valid click on a deterministic test page updates the page state (e.g., a counter increments).
- Result includes `pre_click_screenshot_path` and the file exists.
- Out-of-bounds coordinate returns `COORD_OUT_OF_BOUNDS` and no click is executed.
- `NO_PAGE` error when no prior `view`.
- `click({ col: 0, row: 0, precision: 0 })` returns `INVALID_PRECISION`.
- `click({ col: 0, row: 0, precision: 1.5 })` returns `INVALID_PRECISION`.
- `click({ col: 0, row: 0, precision: -3 })` returns `INVALID_PRECISION`.
- `click({ col: 0, row: 0, precision: 1 })` succeeds on a 960×960 grid (edge: N=1).
- `click({ col: 0, row: 0, precision: 960 })` succeeds on a 1×1 grid (edge: N=960).

**type.test.js:**

- Typing into a known input field on the test page populates the field.
- `type` with `precision: 96` returns `PRECISION_NOT_ALLOWED` (N > 48).
- `type` with `precision: 48` succeeds (boundary: N = 48 is the maximum allowed).
- `type` with `precision: 1` succeeds (edge: maximum density).
- `type` with `precision: 0` returns `INVALID_PRECISION`.

**scroll.test.js:**

- Scrolling down on a tall test page changes the visible content.
- `scroll` with `precision: 12` returns `PRECISION_NOT_ALLOWED` (N < 24).
- `scroll` with `precision: 24` succeeds (boundary: N = 24 is the minimum allowed).
- `scroll` with `precision: 960` succeeds (edge: 1×1 grid, N = 960).
- `scroll` with `precision: 0` returns `INVALID_PRECISION`.

**preclick.test.js:**

- Mock the capture function to fail. Verify `click` returns `PRECLICK_CAPTURE_FAILED` and does NOT call `page.mouse.click`.
- Action log contains a `pre_click_screenshot` entry before the `execute` entry for every successful click.

### 6.3 Deterministic test page

Use a local HTML file served via `file://` or a local HTTP server:

- A 960×960 page with a 10×10 grid of numbered cells (for precision N=96 verification).
- A text input field at a known grid position.
- A scrollable div with content beyond the viewport.
- A counter that increments on click.

Path: `test/fixtures/test-page.html`.

### 6.4 Run command

```
cd extension && npx jest --testPathPattern test/ --verbose
```

All 7 test files must pass. Total test count target: ≥ 35 assertions.

## 7. G1 Evidence Requirements

G1 is the first pipeline pause (after M1.3). The following evidence must exist and be openable:

| # | Evidence item | Format / path |
|---|---|---|
| E1 | BM host loads the extension | CLI output or log showing `register()` called and 4 tools registered |
| E2 | Lifecycle starts and stops cleanly | Log showing `init()` then `destroy()` with no errors |
| E3 | All 4 tools registered | Host tool registry dump (screenshot or CLI output) |
| E4 | `view` returns a headless-browser screenshot | Screenshot file path + result JSON |
| E5 | Screenshot includes coordinate-grid metadata | Result JSON showing `grid` object with precision, cols, rows, cell sizes |
| E6 | Test output | `npx jest` run output (all green) saved to `extension/test-output-g1.txt` |

**Green bar:** All 6 items exist, are openable, and require no M2/M3 work.

**Red bar:** Extension cannot load, tool registration fails, `view` cannot produce a screenshot with grid metadata, or the slice requires Xvfb/security work.

## 8. File Touch List

| File | Action | Step |
|---|---|---|
| `extension/manifest.json` | Create | M1.1 |
| `extension/extension-card.json` | Create | M1.1 |
| `extension/index.js` | Create | M1.1 |
| `extension/cli.js` | Create | M1.1 |
| `extension/tools/view.js` | Create | M1.3 |
| `extension/tools/click.js` | Create | M1.4 |
| `extension/tools/type.js` | Create | M1.4 |
| `extension/tools/scroll.js` | Create | M1.4 |
| `extension/grid/protocol.js` | Create | M1.2 |
| `extension/capture/headless.js` | Create | M1.3 |
| `extension/package.json` | Create | M1.1 |
| `test/fixtures/test-page.html` | Create | M1.2 |
| `test/grid-protocol.test.js` | Create | M1.2 |
| `test/lifecycle.test.js` | Create | M1.1 |
| `test/view.test.js` | Create | M1.3 |
| `test/click.test.js` | Create | M1.4 |
| `test/type.test.js` | Create | M1.4 |
| `test/scroll.test.js` | Create | M1.4 |
| `test/preclick.test.js` | Create | M1.4 |
| `extension/test-output-g1.txt` | Create (test run) | M1.6 |

No files outside `extension/` are modified in M1. The BM host changes (tool registry API, install surface) are handled by Charles per M1.1/M1.5 and are outside this spec's file list.

---
*Revision note 2026-09-27: §2.5 and §2.6 non-breaking addendum incorporated into M1.1 build. All 7 test suites (78 tests) pass against this spec.*
