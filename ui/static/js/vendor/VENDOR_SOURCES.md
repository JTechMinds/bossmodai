# Vendored UI chrome (HA-OPS-P1-02)

| File | Upstream | Pin |
| --- | --- | --- |
| `tailwindcss.js` | https://cdn.tailwindcss.com (Play CDN compiler) | snapshot at vendoring |
| `lucide.min.js` | https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js | 0.469.0 |
| `split.min.js` | https://unpkg.com/split.js@1.6.5/dist/split.min.js | 1.6.5 |

These sit next to `highlight.min.js` / `marked.min.js` so the desktop UI
does not need network for chrome. Tailwind Play still needs `unsafe-eval`
in the Tauri CSP (same as before).
