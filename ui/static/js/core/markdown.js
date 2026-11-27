/**
 * BossMod AI — markdown to nodes, and the allowlist that makes it safe.
 *
 * The ONE place in the tree where HTML text becomes DOM. It parsed to live in
 * places/files/file-content.js, whose docstring named its trust boundary as
 * "the operator's own workspace markdown"; chat bodies are agent output, and
 * conversation/message.js said so in the opposite direction — "untrusted,
 * set through textContent, never innerHTML". Rendering markdown in the
 * transcript closed that gap by moving the renderer here and giving it an
 * allowlist, rather than by pointing the transcript at a renderer built for
 * text nobody had to distrust.
 *
 * Four ordered steps, and THE ORDER IS THE SECURITY PROPERTY:
 *
 *   marked.parse  →  DOMParser (inert)  →  sanitize  →  highlight
 *
 * DOMParser does not run scripts, but it also does not make a tree safe: an
 * `<img src=x onerror=…>` in an agent turn is live the moment it is appended
 * to the document. So the tree is walked against a per-tag attribute
 * allowlist before anything reaches the page. Highlighting runs LAST, which
 * is what keeps `<span>` off the allowlist entirely — hljs emits its spans
 * into a tree nothing inspects again.
 *
 * Links and URL schemes are core/markdown-links.js's, which loads first: the
 * walk hands it every `<a>` that survives the attribute allowlist, and it
 * decides whether that anchor is an external link, a path link that opens in
 * the app, or plain text.
 */
const BossModMarkdown = (() => {

    const ELEMENT_NODE = 1;
    const TEXT_NODE = 3;

    /**
     * Tags removed WITH their contents.
     *
     * Everything else unknown is unwrapped, because losing a sentence to a
     * stray `<span>` is its own bug. These five are the exception: unwrapping
     * a `<style>` repaints the shell from the transcript, and unwrapping a
     * `<script>` dumps its source into the bubble as prose.
     */
    const STRIPPED = new Set([
        'SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE', 'NOSCRIPT',
    ]);

    /**
     * Tag -> the attributes it may keep. Everything not named here is dropped.
     *
     * A whitelist, deliberately: a list of banned attributes is a list somebody
     * has to keep current, and the first `on…` nobody has heard of is the one
     * that gets through. Nothing is removed by name, so an
     * `onpointerrawupdate` is dropped by the same rule that drops `onclick`.
     *
     * `class` is allowed on <code> ALONE, and validated further at
     * keepLanguageClass — anywhere else it would let agent output borrow the
     * shell's own styling and paint itself as, say, a system error.
     *
     * The set is what the vendored marked v15 actually emits, read off its
     * renderer rather than assumed: `align` on cells, `start` on ordered
     * lists, and `<input checked="" disabled="" type="checkbox">` for a GFM
     * task item.
     */
    const ALLOWED = Object.freeze({
        P: [], BR: [], STRONG: [], EM: [], DEL: [], HR: [],
        H1: [], H2: [], H3: [], H4: [], H5: [], H6: [],
        UL: [], OL: ['start'], LI: [],
        BLOCKQUOTE: [], PRE: [], CODE: ['class'],
        TABLE: [], THEAD: [], TBODY: [], TR: [], TH: ['align'], TD: ['align'],
        A: ['href', 'title', 'target', 'rel'],
        INPUT: ['type', 'checked', 'disabled'],
    });

    /** A fence's language, as marked writes it. */
    const LANGUAGE_CLASS = /^language-([a-z0-9+#-]+)$/;

    /**
     * Run `use(hljs)` now when highlight.js is loaded, else once it lands. It
     * is no `<script>` tag (core/lazy-script.js): the first fence declaring a
     * language loads it, so an undefined global is expected until then. A
     * failed load is logged and the code stays plain, readable text. Exported
     * for places/files/file-content.js.
     *
     * @param {(hljs: object) => void} use
     * @returns {void}
     */
    function withHighlighter(use) {
        if (typeof hljs !== 'undefined') use(hljs);
        else BossModLazyScript.load('highlight', 'hljs').then(use, (err) => {
            console.error('[markdown] highlight.js did not load; code stays unhighlighted:', err);
        });
    }

    /**
     * Replace a node with a plain text node, or drop it when there is nothing
     * to say. This is how an <img> keeps its alt text.
     *
     * @param {Node} node
     * @param {string} text
     * @returns {void}
     */
    function replaceWithText(node, text) {
        const parent = node.parentNode;
        if (parent && text) parent.insertBefore(document.createTextNode(text), node);
        node.remove();
    }

    /**
     * Drop an element and promote its children in its place.
     *
     * The moved children have NOT been visited yet — they were behind a node
     * the walk was about to descend into — so each one is sanitised here.
     * Missing that is how a payload rides out inside a tag we merely did not
     * recognise.
     *
     * @param {HTMLElement} el
     * @param {MarkdownLinks} [links]  Passed on to the promoted children.
     * @returns {void}
     */
    function unwrap(el, links) {
        const parent = el.parentNode;
        if (!parent) {
            el.remove();
            return;
        }
        const moved = Array.from(el.childNodes);
        for (const child of moved) parent.insertBefore(child, el);
        el.remove();
        for (const child of moved) sanitizeNode(child, links);
    }

    /**
     * Drop every attribute the tag is not allowed to keep.
     *
     * @param {HTMLElement} el
     * @param {string[]} allowed
     * @returns {void}
     */
    function stripAttributes(el, allowed) {
        for (const { name } of Array.from(el.attributes)) {
            if (!allowed.includes(name)) el.removeAttribute(name);
        }
    }

    /**
     * A GFM task-list checkbox, or nothing.
     *
     * `disabled` is forced rather than trusted: the transcript is a record, and
     * a checkbox an operator can toggle implies a state change that nothing
     * behind it would ever persist.
     *
     * @param {HTMLElement} el
     * @returns {boolean} false when the input must be removed.
     */
    function hardenCheckbox(el) {
        if (el.getAttribute('type') !== 'checkbox') return false;
        el.setAttribute('disabled', '');
        return true;
    }

    /**
     * Keep a fence's language class only when hljs can resolve it.
     *
     * Not a security rule — stripAttributes already reduced this to `class` on
     * a <code> — but a language hljs does not know makes it warn on every
     * repaint, and a console full of noise is a console nobody reads.
     *
     * Lower-cased before it is matched OR looked up, because `marked` copies a
     * fence's tag through verbatim: an agent writing ```Python yields
     * `language-Python`, which a lower-case pattern rejects and which would
     * then fall through to hljs's auto-detection — the one path that guesses
     * wrong (a Go snippet reads as C#). hljs itself is case-insensitive, so
     * the only thing standing between ```Go and correct colour is this.
     *
     * Before highlight.js loads the normalised class is kept; highlightFence
     * applies the same test once it has.
     *
     * @param {HTMLElement} el
     * @returns {void}
     */
    function keepLanguageClass(el) {
        const raw = el.getAttribute('class');
        if (!raw) return;
        const match = LANGUAGE_CLASS.exec(raw.trim().toLowerCase());
        const known = match && (typeof hljs === 'undefined' || hljs.getLanguage(match[1]));
        if (known) el.setAttribute('class', `language-${match[1]}`);
        else el.removeAttribute('class');
    }

    /**
     * Give a table its own scroll box.
     *
     * Without it a wide table widens the bubble, the bubble widens the
     * transcript, and the operator scrolls the whole conversation sideways to
     * read one row. The wrapper is ours and is never re-sanitised, which is why
     * it may be a <div> that the allowlist has no entry for.
     *
     * @param {HTMLElement} table
     * @returns {void}
     */
    function wrapForScroll(table) {
        const parent = table.parentNode;
        if (!parent) return;
        const box = document.createElement('div');
        box.className = 'md-scroll';
        parent.insertBefore(box, table);
        box.append(table);
    }

    /**
     * Sanitise one node in place, descending into whatever survives.
     *
     * @param {Node} node
     * @param {MarkdownLinks} [links]  The surface's path opener (hardenLink).
     * @returns {void}
     */
    function sanitizeNode(node, links) {
        if (node.nodeType === TEXT_NODE) return;
        // Comments and anything else that is neither text nor an element. A
        // comment carries no information the operator asked for and is the
        // classic parser-confusion vector.
        if (node.nodeType !== ELEMENT_NODE) {
            node.remove();
            return;
        }
        const tag = node.tagName;
        if (STRIPPED.has(tag)) {
            node.remove();
            return;
        }
        if (tag === 'IMG') {
            // The information without the request: a remote fetch from a chat
            // bubble both leaks who is reading and fails in an offline shell.
            replaceWithText(node, node.getAttribute('alt') || '');
            return;
        }
        const allowed = ALLOWED[tag];
        if (!allowed) {
            unwrap(node, links);
            return;
        }
        stripAttributes(node, allowed);
        if (tag === 'A' && !BossModMarkdownLinks.hardenLink(node, links)) {
            unwrap(node, links);
            return;
        }
        if (tag === 'INPUT' && !hardenCheckbox(node)) {
            node.remove();
            return;
        }
        if (tag === 'CODE') keepLanguageClass(node);
        if (tag === 'TABLE') wrapForScroll(node);
        sanitize(node, links);
    }

    /**
     * Sanitise every child of a node, in place.
     *
     * Exported because it is the whole security surface and it operates on
     * nodes: Node has no DOMParser, so this is the half of `render` that can
     * be tested directly (tests/js_markdown_harness.cjs).
     *
     * @param {Node} root  Mutated in place.
     * @param {MarkdownLinks} [links]  How this surface opens path links;
     *   absent, they render as plain text.
     * @returns {void}
     * @throws {Error} When `links` is given without both functions.
     */
    function sanitize(root, links) {
        BossModMarkdownLinks.assertLinks(links);
        // A snapshot, because unwrapping a child rewrites this list underneath
        // the walk.
        for (const node of Array.from(root.childNodes)) sanitizeNode(node, links);
    }

    /**
     * Highlight fenced code blocks — `<pre><code>`, never inline `<code>`.
     *
     * A fence is only ever highlighted as the language it DECLARED. An
     * untagged one keeps the code surface and gets no colour, which is what
     * GitHub does and what the alternative argues for on its own: hljs's
     * `highlightAuto` guesses across every registered grammar, and a wrong
     * guess is worse than none — measured against the real bundle, a Go
     * snippet comes back as C# and a raw diff comes back as CSS. Colour that
     * is confidently wrong reads as meaning.
     *
     * The test for "did it declare one" is the class, and it is trustworthy
     * because sanitize ran first: stripAttributes leaves <code> nothing but
     * `class`, and keepLanguageClass either rewrites it to a language hljs
     * resolved or removes it. So a class here IS a known language (or, before
     * hljs has loaded, one highlightFence checks again), and an unknown tag
     * (```brainfuck) has already become the untagged case.
     *
     * Walked by hand rather than through `querySelectorAll('pre code')`: the
     * descendant selector is the one form the DOM this runs against in tests
     * cannot express, and a renderer that can only be tested in a browser is a
     * renderer that is not tested.
     *
     * @param {Node} root
     * @param {HTMLElement[]} [declared]  Accumulator for the recursion.
     * @returns {void}
     */
    function highlightCode(root, declared) {
        const fences = declared || [];
        for (const node of Array.from(root.childNodes)) {
            if (node.nodeType !== ELEMENT_NODE) continue;
            if (node.tagName === 'CODE'
                && node.parentNode && node.parentNode.tagName === 'PRE') {
                if (node.getAttribute('class')) fences.push(node);
                // The class the vendored hljs stylesheet paints the code
                // SURFACE with. An untagged fence is still a code block.
                node.classList.add('hljs');
                continue;
            }
            highlightCode(node, fences);
        }
        if (!declared && fences.length) {
            withHighlighter((lib) => fences.forEach((node) => highlightFence(lib, node)));
        }
    }

    /**
     * Highlight a declared fence, or drop a language highlight.js lacks (kept
     * only when keepLanguageClass ran before it loaded).
     * @param {object} lib @param {HTMLElement} node @returns {void}
     */
    function highlightFence(lib, node) {
        const language = String(node.getAttribute('class')).split(/\s+/)
            .map((name) => LANGUAGE_CLASS.exec(name)).find(Boolean);
        if (language && lib.getLanguage(language[1])) lib.highlightElement(node);
        else node.setAttribute('class', 'hljs');
    }

    /**
     * Render markdown to safe nodes.
     *
     * `breaks: true` because a chat message is written with single newlines
     * and an operator who pressed Enter meant it; `gfm: true` for the tables,
     * task lists and strikethrough agents actually emit.
     *
     * Returns an ARRAY rather than a DocumentFragment: core/dom.js's h()
     * documents that children are flattened one level, so an array drops
     * straight into a node tree as a child, and `append`/`replaceChildren`
     * spread it just as happily.
     *
     * @param {string} text
     * @param {MarkdownLinks} [links]  How this surface opens path links;
     *   absent, they render as plain text.
     * @returns {Node[]} Empty for empty input.
     * @throws {Error} When `links` is given without both functions.
     */
    function render(text, links) {
        BossModMarkdownLinks.assertLinks(links);
        const source = String(text || '');
        if (!source) return [];
        const parsed = new DOMParser().parseFromString(
            marked.parse(source, { gfm: true, breaks: true }), 'text/html');
        sanitize(parsed.body, links);
        highlightCode(parsed.body);
        return Array.from(parsed.body.childNodes);
    }

    /**
     * Replace a container's contents with rendered markdown.
     *
     * @param {HTMLElement} el
     * @param {string} text
     * @param {MarkdownLinks} [links]  As for render().
     * @returns {void}
     * @throws {Error} When `links` is given without both functions.
     */
    function renderInto(el, text, links) {
        el.replaceChildren(...render(text, links));
    }

    return { render, renderInto, sanitize, withHighlighter };
})();
