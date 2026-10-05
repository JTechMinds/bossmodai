/* Build-time Tailwind config for scripts/build_tailwind.sh (standalone CLI
   v3.4.17). The app never loads this file: its output is the committed
   ui/static/css/tailwind.generated.css.

   The `bm` colours mirror ui/static/css/tokens.css, which is the source of
   truth; tests/test_ui_tokens.py fails if they drift apart.

   Play's defaults are kept on purpose — preflight on, no `prefix`, no
   `important` — because the Play runtime this replaces used them, and the
   cascade every surface was tuned against must not move.

   `relative: true` resolves the globs against this file, so the build gives
   the same output whatever directory it is run from. The CLI only sees class
   names written out literally in these files: build a class at runtime and it
   will be missing. tests/test_tailwind_generated.py guards that. */
module.exports = {
    content: {
        relative: true,
        files: [
            '../ui/static/js/**/*.js',
            // Vendored libraries are not app markup; scanning them only adds
            // classes nothing renders.
            '!../ui/static/js/vendor/**',
            '../ui/templates/**/*.html',
        ],
    },
    theme: {
        extend: {
            colors: {
                bm: {
                    bg: '#f6f7f9',
                    surface: '#ffffff',
                    border: '#e6e8ec',
                    text: '#1b1f24',
                    muted: '#565e6b',
                    hint: '#6b7280',
                    accent: '#2d6be5',
                    'accent-hover': '#2456bd',
                },
            },
        },
    },
};
