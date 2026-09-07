/**
 * BossMod AI — what kind of file this is, and how its bytes become nodes.
 *
 * Split out of the viewer panel because "is this markdown, an image, or code,
 * and how is it rendered" is a different question from "what does the panel
 * look like" — and because it is the one file in the new UI that turns HTML
 * text into DOM, which deserves to be small enough to read in full.
 *
 * Code never goes in as markup: `hljs.highlightElement` highlights a node that
 * already holds the text, so file contents are never concatenated into a
 * string. Markdown is the exception and is documented at renderMarkdownInto.
 */
const BossModFileContent = (() => {
    const { h, clear } = BossModDom;

    const EXT_LANG_MAP = {
        '.py': 'python', '.js': 'javascript', '.jsx': 'javascript',
        '.ts': 'typescript', '.tsx': 'typescript', '.json': 'json',
        '.yaml': 'yaml', '.yml': 'yaml', '.sql': 'sql',
        '.html': 'html', '.css': 'css', '.sh': 'bash', '.bash': 'bash',
        '.xml': 'xml', '.svg': 'xml', '.toml': 'ini', '.md': 'markdown',
        '.ini': 'ini', '.cfg': 'ini', '.log': 'plaintext',
        '.txt': 'plaintext', '.csv': 'plaintext', '.env': 'bash',
        '.graphql': 'graphql', '.rst': 'plaintext', '.tex': 'latex',
    };

    const IMAGE_EXTENSIONS = new Set([
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
    ]);

    /**
     * @param {string} filename
     * @returns {string} '' when the name carries no extension.
     */
    function extension(filename) {
        const name = String(filename || '');
        const dot = name.lastIndexOf('.');
        return dot === -1 ? '' : name.slice(dot).toLowerCase();
    }

    /**
     * @param {string} filename
     * @returns {string|null} The highlight.js language id, or null.
     */
    function getLanguage(filename) {
        return EXT_LANG_MAP[extension(filename)] || null;
    }

    /** @param {string} filename @returns {boolean} */
    function isImage(filename) {
        return IMAGE_EXTENSIONS.has(extension(filename));
    }

    /** @param {string} filename @returns {boolean} */
    function isMarkdown(filename) {
        return extension(filename) === '.md';
    }

    /** @param {string} filename @returns {boolean} */
    function isJson(filename) {
        return extension(filename) === '.json';
    }

    /**
     * Pretty-print JSON, leaving anything unparseable exactly as it came.
     *
     * @param {string} raw
     * @returns {string}
     */
    function prettyJson(raw) {
        try {
            return JSON.stringify(JSON.parse(raw), null, 2);
        } catch (err) {
            // A .json file that does not parse is a real file the operator
            // still needs to read; showing it verbatim is the answer, not an
            // error. Logged so a genuine parser problem stays visible.
            console.warn('[file-content] not valid JSON; showing it unformatted', err);
            return raw;
        }
    }

    /**
     * A highlighted <pre><code> block.
     *
     * @param {string} code
     * @param {string|null} lang
     * @returns {HTMLElement}
     */
    function codeBlock(code, lang) {
        const codeEl = h('code', { class: 'hljs' });
        codeEl.textContent = code;
        if (lang && hljs.getLanguage(lang)) codeEl.classList.add(`language-${lang}`);
        hljs.highlightElement(codeEl);
        return h('pre', { class: 'file-view-code' }, codeEl);
    }

    /**
     * Render markdown into a container.
     *
     * This is the ONE place in the new UI where HTML text becomes nodes, and it
     * is parsed rather than assigned: `marked` has no node-returning API, and
     * dropping rendered markdown would be a feature removed. The trust boundary
     * is unchanged from the module this replaces — the operator's own workspace
     * markdown — and DOMParser is inert, so nothing runs on the way in. Fenced
     * code inside it is highlighted afterwards, on nodes.
     *
     * @param {HTMLElement} el
     * @param {string} raw
     * @returns {void}
     */
    function renderMarkdownInto(el, raw) {
        clear(el);
        const parsed = new DOMParser().parseFromString(
            marked.parse(raw, { breaks: true, gfm: true }), 'text/html');
        el.append(...Array.from(parsed.body.childNodes));
        el.querySelectorAll('pre code').forEach((node) => hljs.highlightElement(node));
    }

    /**
     * Paint a text file's content into a container.
     *
     * @param {HTMLElement} el
     * @param {string} name     The file name; decides markdown vs code vs JSON.
     * @param {string} content
     * @returns {void}
     */
    function renderInto(el, name, content) {
        if (isMarkdown(name)) {
            renderMarkdownInto(el, content);
            return;
        }
        clear(el);
        el.append(codeBlock(isJson(name) ? prettyJson(content) : content, getLanguage(name)));
    }

    return {
        extension, getLanguage, isImage, isMarkdown, isJson,
        prettyJson, codeBlock, renderMarkdownInto, renderInto,
    };
})();
