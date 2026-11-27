/**
 * BossMod AI — what an anchor in rendered markdown may be, and what it does.
 *
 * core/markdown.js parses, walks the tree against its tag and attribute
 * allowlist, and hands every surviving `<a>` here (hardenLink). This module
 * owns the rest of an anchor's story: the scheme allowlist, classifying the
 * href, hardening an external link, and a path link's behaviour. It loads
 * immediately before markdown.js.
 *
 * The vendored marked v15 carries no mention of `javascript` and dropped its
 * `sanitize` option four majors ago. Nothing upstream is filtering URL
 * schemes; that check is here or it does not happen.
 *
 * An anchor is one of three kinds (linkTarget). An EXTERNAL link (http, https,
 * mailto) keeps its href and opens outside the app through
 * core/external-open.js. A PATH link (`/projects/x.md`, `runbook.md`) names a
 * file in somebody's workspace, which no browser can fetch: resolving it
 * against the page's own URL is how it used to become a dead
 * `http://127.0.0.1:…/projects/x.md` tab. A path link therefore carries NO
 * href at all — the http(s) interceptor would steal any href, and a
 * middle-click or "Open in new window" would still reach the dead page — and
 * opens through the opener its surface passes as `links`. A surface that
 * passes none shows the path as plain text. Everything else is REFUSED and
 * renders as its text.
 */
const BossModMarkdownLinks = (() => {

    /** What an anchor may point at. Everything else renders as its own text. */
    const ALLOWED_SCHEMES = Object.freeze(['http:', 'https:', 'mailto:']);

    /** An href that names its own scheme (`https:`, `javascript:`, `c:`). */
    const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

    /**
     * Refusals worth a warning: possibly hostile or broken input. An empty or
     * fragment-only href is expected and documented, so it is not logged.
     */
    const LOGGED_REFUSALS = new Set(['scheme', 'unparseable', 'undecodable']);

    /**
     * How a surface opens the path links in what it renders.
     *
     * @typedef {object} MarkdownLinks
     * @property {(path: string) => (string|null)} resolve  The path to open
     *   for a decoded href path, or null (or '') to show the link as plain
     *   text — a path this surface cannot place.
     * @property {(path: string) => (Promise<void>|void)} open  Opens a resolved
     *   path. A rejection marks the link failed with its message.
     */

    /**
     * Throw when a surface hands over a `links` that could not open anything.
     *
     * @param {MarkdownLinks} [links]
     * @returns {void}
     * @throws {Error} When `links` is given without both functions.
     */
    function assertLinks(links) {
        if (links === undefined) return;
        if (!links || typeof links.resolve !== 'function' || typeof links.open !== 'function') {
            throw new Error('[markdown] links needs resolve and open functions');
        }
    }

    /**
     * Classify an href. Pure: it reads only the string (and, for a
     * protocol-relative URL, the document base).
     *
     * Checked in order on the trimmed attribute: empty; a named scheme, kept
     * only when it is in ALLOWED_SCHEMES; protocol-relative `//host`, kept as
     * the http(s) URL it means; a bare `#…` or `?…`, which has nothing in a
     * rendered body to point at; and otherwise a path. A path loses its query
     * and fragment — `#5-acceptance-checks` opens the file at its top — and is
     * percent-decoded, because the vendored marked runs every href through
     * `encodeURI` (`a b.md` arrives as `a%20b.md`).
     *
     * @param {string|null|undefined} href  The raw attribute.
     * @returns {{kind: 'external', href: string}
     *   |{kind: 'path', path: string}
     *   |{kind: 'refused', reason: 'empty'|'scheme'|'unparseable'|'fragment'|'undecodable'}}
     */
    function linkTarget(href) {
        const raw = String(href == null ? '' : href).trim();
        if (!raw) return { kind: 'refused', reason: 'empty' };
        if (SCHEME.test(raw)) {
            let url;
            try {
                url = new URL(raw);
            } catch {
                return { kind: 'refused', reason: 'unparseable' };
            }
            return ALLOWED_SCHEMES.includes(url.protocol)
                ? { kind: 'external', href: url.href } : { kind: 'refused', reason: 'scheme' };
        }
        if (raw.startsWith('//')) {
            let url;
            try {
                url = new URL(raw, document.baseURI);
            } catch {
                return { kind: 'refused', reason: 'unparseable' };
            }
            return url.protocol === 'http:' || url.protocol === 'https:'
                ? { kind: 'external', href: url.href } : { kind: 'refused', reason: 'scheme' };
        }
        if (raw.startsWith('#') || raw.startsWith('?')) return { kind: 'refused', reason: 'fragment' };
        const cut = raw.search(/[?#]/);
        let path;
        try {
            path = decodeURIComponent(cut === -1 ? raw : raw.slice(0, cut));
        } catch {
            return { kind: 'refused', reason: 'undecodable' };
        }
        if (!path) return { kind: 'refused', reason: 'empty' };
        return { kind: 'path', path };
    }

    /**
     * The `resolve` rule for text written in a conversation: a chat message,
     * or a task's saved copy of one. Such text records no working folder, so
     * only an absolute path names one file; a relative one (`docs/x.md`)
     * stays plain text, because guessing its base would open the wrong file
     * or nothing. Lives here because both of its callers
     * (conversation/message.js and places/tasks/task-message.js) load after
     * this module.
     *
     * @param {string} path  A decoded href path (linkTarget).
     * @returns {string|null} `path` when it starts with `/`, else null.
     */
    function resolveAbsolutePath(path) {
        return typeof path === 'string' && path.startsWith('/') ? path : null;
    }

    /**
     * Open a path link, and say so on the link when it cannot open.
     *
     * The same failure treatment as a Tasks deliverable card and the Done
     * note's link: `is-failed` and the reason in the tooltip, never a click
     * that silently does nothing. A retry clears the last failure first.
     *
     * @param {HTMLElement} el
     * @param {string} path  The resolved path.
     * @param {MarkdownLinks['open']} open
     * @returns {Promise<void>} Never rejects; the failure is on the link and
     *   in the console.
     */
    async function activatePathLink(el, path, open) {
        el.classList.remove('is-failed');
        el.setAttribute('title', path);
        try {
            await open(path);
        } catch (err) {
            el.classList.add('is-failed');
            el.setAttribute('title', (err && err.message) || 'Could not open that path');
            console.error('[markdown] could not open', path, err);
        }
    }

    /**
     * Turn an anchor into a path link: an `<a>` with no URL that opens in the
     * app.
     *
     * It stays an `<a>` so `.md a` styles it and long text wraps inline; a
     * `<button>` is an atomic inline box that cannot wrap and would overflow
     * a bubble. With no href it is not natively a link, so it gains
     * `role="link"`, a tab stop, and Enter (not Space — link semantics). Set
     * AFTER stripAttributes, so an agent's own `class`, `role` or `tabindex`
     * never survives; these are ours.
     *
     * @param {HTMLElement} el
     * @param {string} path  The resolved path; also the tooltip.
     * @param {MarkdownLinks['open']} open
     * @returns {void}
     */
    function makePathLink(el, path, open) {
        el.removeAttribute('href');
        el.removeAttribute('target');
        el.removeAttribute('rel');
        el.setAttribute('class', 'md-path-link');
        el.setAttribute('role', 'link');
        el.setAttribute('tabindex', '0');
        el.setAttribute('title', path);
        el.addEventListener('click', (event) => {
            event.preventDefault();
            void activatePathLink(el, path, open);
        });
        el.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            void activatePathLink(el, path, open);
        });
    }

    /**
     * Decide what an anchor becomes, by its kind (linkTarget).
     *
     * An external link keeps its resolved href, and `target` and `rel` are
     * OVERWRITTEN rather than merely permitted: an agent naming its own
     * `target="_self"` would otherwise navigate the shell away from the app,
     * and a new tab must never be handed a window reference. A path link
     * becomes one only when the surface passed `links` and its `resolve`
     * places the path (makePathLink). A refused scheme, an unparseable URL or
     * an undecodable path is logged rather than swallowed — the anchor's text
     * still renders, which is the documented behaviour, not a failure hidden
     * behind a default.
     *
     * Called by core/markdown.js after stripAttributes has reduced the anchor
     * to its allowed attributes.
     *
     * @param {HTMLElement} el
     * @param {MarkdownLinks} [links]  The surface's path opener; absent means
     *   path links render as plain text.
     * @returns {boolean} false when the link must be unwrapped to its text.
     */
    function hardenLink(el, links) {
        const href = el.getAttribute('href');
        const target = linkTarget(href);
        if (target.kind === 'refused') {
            if (LOGGED_REFUSALS.has(target.reason)) {
                console.warn(`[markdown] refused link (${target.reason}); rendering it as text`, href);
            }
            return false;
        }
        if (target.kind === 'external') {
            el.setAttribute('href', target.href);
            el.setAttribute('target', '_blank');
            el.setAttribute('rel', 'noopener noreferrer');
            return true;
        }
        if (!links) return false;
        const resolved = links.resolve(target.path);
        if (!resolved) return false;
        makePathLink(el, resolved, links.open);
        return true;
    }

    return { assertLinks, linkTarget, resolveAbsolutePath, hardenLink };
})();
