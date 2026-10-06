/**
 * BossMod AI — load a vendored script the first time something needs it.
 *
 * Two vendored libraries have one narrow consumer each and used to be parsed on
 * every launch anyway: the table library behind core/data-table.js (448 KB,
 * one dialog) and highlight.js behind core/markdown.js (127 KB, only once a
 * code block declares a language). They are no longer `<script>` tags; the
 * module that needs one asks for it here and gets a promise of the library.
 *
 * Where a script lives is the template's fact, not this module's: index.html
 * names each one as
 *
 *   <meta name="bossmod-lazy-script" data-library="highlight"
 *         content="{{ static_url('js/vendor/highlight.min.js') }}">
 *
 * so the content-hash cache-buster `static_url` adds is kept, and no path is
 * spelled twice. A library with no such meta is a broken template and the
 * load rejects naming it.
 *
 * 'self' only. The Tauri CSP is `script-src 'self'`, which would block another
 * origin anyway — but as a CSP report with no caller attached. This refuses it
 * first, with a message that names the library and the URL.
 *
 * A failed load is not remembered: the next `load` tries again, which is what
 * a "Try again" button over a failed table needs.
 */
const BossModLazyScript = (() => {

    /** The template's declaration of one lazily loaded script. */
    const META = 'meta[name="bossmod-lazy-script"]';

    /** library name -> the in-flight or settled-successfully load. */
    const loads = new Map();

    /**
     * The URL index.html declared for a library.
     *
     * @param {string} library  The meta's `data-library`, e.g. "highlight".
     * @returns {string} An absolute, same-origin URL.
     * @throws {Error} When no meta names the library, it has no URL, or the
     *   URL is on another origin.
     */
    function urlFor(library) {
        const meta = Array.from(document.querySelectorAll(META))
            .find((node) => node.getAttribute('data-library') === library);
        if (!meta) {
            throw new Error(`[lazy-script] index.html declares no <meta name="bossmod-lazy-script" data-library="${library}">`);
        }
        const src = meta.getAttribute('content');
        if (!src) throw new Error(`[lazy-script] the "${library}" meta has no content URL`);
        const url = new URL(src, document.baseURI);
        if (url.origin !== window.location.origin) {
            throw new Error(`[lazy-script] "${library}" points off-origin (${url.href}); scripts load from 'self' only`);
        }
        return url.href;
    }

    /**
     * Append one classic `<script src>` to <head> and settle when it has run.
     *
     * @param {string} src
     * @returns {Promise<void>} Rejects when the browser reports a load error.
     */
    function inject(src) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = src;
            script.onload = () => resolve();
            script.onerror = () => {
                // Removed so a retry appends a fresh tag instead of leaving a
                // dead one behind per attempt.
                script.remove();
                reject(new Error(`[lazy-script] ${src} failed to load`));
            };
            document.head.append(script);
        });
    }

    /**
     * The library, loading it on the first call.
     *
     * Resolves at once when the global is already defined — by an earlier
     * load, or by a harness that installed it.
     *
     * @param {string} library  Which `bossmod-lazy-script` meta to load.
     * @param {string} globalName  The global the script defines, e.g. "hljs".
     * @returns {Promise<object>} The library object.
     * @throws Never synchronously; every failure (no meta, off-origin URL,
     *   network error, a script that ran but defined nothing) is a rejection
     *   whose message names the library.
     */
    function load(library, globalName) {
        if (window[globalName]) return Promise.resolve(window[globalName]);
        if (loads.has(library)) return loads.get(library);
        const pending = Promise.resolve()
            .then(() => inject(urlFor(library)))
            .then(() => {
                const lib = window[globalName];
                if (!lib) {
                    throw new Error(`[lazy-script] "${library}" loaded but did not define window.${globalName}`);
                }
                return lib;
            });
        loads.set(library, pending);
        // Forget a failure so the next call retries. This branch only clears
        // the cache: the rejection itself still reaches every caller of
        // `pending`, which is who reports it.
        pending.catch(() => { loads.delete(library); });
        return pending;
    }

    return { load };
})();
