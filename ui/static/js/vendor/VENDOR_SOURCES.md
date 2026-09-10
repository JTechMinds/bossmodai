# Vendored UI chrome (HA-OPS-P1-02)

| File | Upstream | Pin |
| --- | --- | --- |
| `tailwindcss.js` | https://cdn.tailwindcss.com (Play CDN compiler) | snapshot at vendoring |
| `lucide.min.js` | https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js | 0.469.0 |

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
