/**
 * The three vendored globals core/markdown.js reads, stubbed, for harnesses
 * that are testing something else.
 *
 * Every surface that paints a message body or a .md file now renders through
 * core/markdown.js, so every harness that mounts one needs `marked`, `hljs`
 * and a `DOMParser` — and Node has none of them. A transcript harness is
 * asserting which messages are on screen, not marked's output, so it gets a
 * passthrough: the "html" is the source text, and the parser hands back a body
 * holding it as one text node.
 *
 * That means a stubbed render produces the message's text and no structure,
 * which is exactly what those harnesses read (`.msg-body` textContent). The
 * real pipeline — the allowlist, the scheme check, the unwrapping — is
 * exercised for real in tests/js_markdown_harness.cjs, which stages parsed
 * trees directly rather than pretending to parse.
 *
 * Not a browser bundle. Required by the .cjs harnesses under tests/.
 */

/**
 * Install `marked`, `hljs` and `DOMParser` on both `global` and `window`.
 *
 * @param {object} documentStub  From installDom(); the parser builds its body
 *   with the same node factory every other module in the harness uses.
 * @returns {{parseCalls: object[], highlighted: object[]}} Recorded calls, for
 *   a harness that wants to assert the surface rendered at all.
 * @throws {Error} When called without a document stub — a parser that cannot
 *   build a node would fail later, inside the module under test, where the
 *   cause is much harder to read.
 */
function installMarkdownStub(documentStub) {
    if (!documentStub || typeof documentStub.createElement !== "function") {
        throw new Error("[markdown-stub] installDom()'s document stub is required");
    }

    const parseCalls = [];
    const highlighted = [];

    const marked = {
        parse(text, options) {
            parseCalls.push({ text, options });
            return String(text);
        },
    };

    const hljs = {
        // No language pack is loaded, so nothing resolves and no fence keeps a
        // `language-*` class. Harnesses here assert text, not highlighting.
        getLanguage: () => undefined,
        highlightElement(node) {
            highlighted.push(node);
        },
    };

    class DOMParserStub {
        parseFromString(html) {
            const body = documentStub.createElement("body");
            body.append(documentStub.createTextNode(String(html)));
            return { body };
        }
    }

    global.marked = marked;
    global.hljs = hljs;
    global.DOMParser = DOMParserStub;
    if (global.window) {
        global.window.marked = marked;
        global.window.hljs = hljs;
        global.window.DOMParser = DOMParserStub;
    }

    return { parseCalls, highlighted };
}

module.exports = { installMarkdownStub };
