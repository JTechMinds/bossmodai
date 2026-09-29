# Vendored UI chrome (HA-OPS-P1-02)

| File | Upstream | Pin |
| --- | --- | --- |
| `tailwindcss.js` | https://cdn.tailwindcss.com (Play CDN compiler) | snapshot at vendoring |
| `lucide.min.js` | https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js | 0.469.0 |
| `tabulator.min.js` | https://registry.npmjs.org/tabulator-tables/-/tabulator-tables-6.5.3.tgz → `package/dist/js/tabulator.min.js` (MIT) | 6.5.3 |
| `../../css/vendor/tabulator.min.css` | same tarball → `package/dist/css/tabulator.min.css` (MIT) | 6.5.3 |

These sit next to `highlight.min.js` / `marked.min.js` so the desktop UI
does not need network for chrome. Tailwind Play still needs `unsafe-eval`
in the Tauri CSP (same as before).

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
