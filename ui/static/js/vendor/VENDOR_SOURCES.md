# Vendored UI chrome (HA-OPS-P1-02)

| File | Upstream | Pin |
| --- | --- | --- |
| `lucide.min.js` | https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js | 0.469.0 |
| `lucide.subset.js` | generated from `lucide.min.js` by `scripts/build_lucide_subset.cjs` | 0.469.0 |
| `tabulator.min.js` | https://registry.npmjs.org/tabulator-tables/-/tabulator-tables-6.5.3.tgz → `package/dist/js/tabulator.min.js` (MIT) | 6.5.3 |
| `../../css/vendor/tabulator.min.css` | same tarball → `package/dist/css/tabulator.min.css` (MIT) | 6.5.3 |

These sit next to `highlight.min.js` / `marked.min.js` so the desktop UI
does not need network for chrome.

Tailwind is not vendored. Its utilities are pre-built into
`ui/static/css/tailwind.generated.css` by `scripts/build_tailwind.sh`, using the
Tailwind standalone CLI v3.4.17 (a dev-time binary, never committed or loaded),
so nothing compiles CSS in the page. The Tauri CSP is `script-src 'self'`: no
`unsafe-eval`, and no script from any other origin.

`split.min.js` was here and is retired — its only caller was the deleted
`app.js`. It is asserted absent from disk by `tests/test_offline_ui.py`, so a
row for it in this table described a file that had not existed for four phases.

## No highlight.js language packs

`highlight.min.js` is the **common** build: it registers 36 grammars by itself,
including go, rust, java, ruby, php, swift, kotlin, c/cpp/csharp and diff.

Eleven `hljs-lang-*.min.js` packs were vendored alongside it — bash, css, ini,
javascript, json, markdown, python, sql, typescript, xml, yaml — and every one
was already in the bundle at the same 11.11.1 version. Loading all eleven took
`hljs.listLanguages()` from 36 to 36 and changed no highlighting output, under
explicit and auto-detected highlighting alike. They are retired by name in
`tests/test_offline_ui.py`.

Before vendoring a language pack, check `hljs.listLanguages()` first.

## Tabulator

Vendored 2026-09-29 from the npm tarball, byte-for-byte (tarball integrity
`sha512-nJ2QaqZzQvd9B4rf4LBnRxfb6sSqA/BX2OsXnSJ2d8k21oXDSTtFtOUrDap04a/6BDGivC/MByCxy43ugadJIg==`
matched the registry). sha256: `tabulator.min.js`
`ffac518cb793c672a2f0522b7fc80bf862234d96bb1e581c8aaf7f48f130eafa`,
`tabulator.min.css` `405ae24218357d80df2a5f3addf138666d035f970dac4ebdb0265d0b5373efcb`.
The source maps the files name are not vendored. Only `core/data-table.js`
(`BossModDataTable`) may reference `Tabulator`; `css/data-table.css` re-skins
it with `tokens.css` variables.

## Lucide: a generated subset, not the bundle

`index.html` does **not** load `lucide.min.js` (358 KB, 1,743 icons). It loads
`lucide.subset.js`, which `node scripts/build_lucide_subset.cjs` writes from
it: the bundle's own `createElement`, sliced verbatim, plus the definitions of
only the icons the app names (108 at the time of writing, ~23 KB). The full
bundle stays here, byte-for-byte as vendored, as the generator's input.

Re-run the generator after using an icon the app did not use before.
`tests/test_lucide_subset.py` runs it with `--check`, so a stale subset fails
the suite; at runtime `core/icons.js` `console.error`s and throws on a name the
subset lacks. Upgrading Lucide means replacing `lucide.min.js` and re-running
the generator — it fails loudly if the bundle's `createElement` anchors moved.

## Loaded on first use: highlight.js and Tabulator

Neither is a `<script>` tag. `index.html` names each in a
`<meta name="bossmod-lazy-script" data-library="…" content="…">`, and
`core/lazy-script.js` (`BossModLazyScript.load`) appends the script from that
same-origin URL the first time it is needed: highlight.js on the first fenced
code block that declares a language (`core/markdown.js`) or the first code file
opened (`places/files/file-content.js`), Tabulator on the first data table
(`core/data-table.js`). Their stylesheets are still linked in `<head>`, so the
cascade does not change.
