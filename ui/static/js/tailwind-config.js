/* Mirrors ui/static/css/tokens.css. tokens.css is the source of truth.
   tests/test_ui_tokens.py fails if these drift apart. */
tailwind.config = {
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
                }
            }
        }
    }
}
