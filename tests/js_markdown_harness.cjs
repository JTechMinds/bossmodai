/**
 * Node harness: the markdown sanitiser, which is the only thing standing
 * between an agent's output and the DOM. Invoked by tests/test_ui_markdown.py.
 *
 * Node has no DOMParser, so the PARSE step is stubbed and the harness supplies
 * the tree a parser would have produced. That is the honest split rather than a
 * limitation: `marked` is a vendored library with its own test suite, and what
 * this module adds on top of it — the allowlist, the scheme check, the
 * unwrapping — operates on nodes and is exactly what is exercised here.
 *
 * Every property below is a way an agent turn could have reached into the page.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();

const paths = process.argv.slice(2);
if (paths.length !== 1) {
    throw new Error(`expected 1 module path, got ${paths.length}`);
}

// ─── The two vendored globals the module reads ───

/** What `marked.parse` was handed, so the harness can assert the options. */
const parseCalls = [];

/** The tree the stubbed parser hands back. Set per case. */
let nextBody = null;

global.marked = {
    parse(text, options) {
        parseCalls.push({ text, options });
        return String(text);
    },
};

global.DOMParser = class {
    parseFromString() {
        if (!nextBody) throw new Error("[harness] no body staged for the parser");
        return { body: nextBody };
    }
};

/** Nodes hljs was asked to highlight, in call order. */
const highlighted = [];

global.hljs = {
    // Only the languages the real vendored set registers matter here; `python`
    // stands in for "registered" and `brainfuck` for "not".
    getLanguage: (name) => (name === "python" || name === "json" ? {} : undefined),
    highlightElement(node) {
        highlighted.push(node);
    },
};

eval(`${fs.readFileSync(paths[0], "utf8")}\n;global.BossModMarkdown = BossModMarkdown;\n`);
const { sanitize, render } = global.BossModMarkdown;

// ─── Tree builders ───

function el(tag, attrs, ...children) {
    const node = documentStub.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    for (const child of children) {
        node.append(typeof child === "string" ? documentStub.createTextNode(child) : child);
    }
    return node;
}

/** A detached <body> holding the staged nodes, as the parser would return it. */
function body(...children) {
    return el("body", null, ...children);
}

/** Every tag name still present in a tree, depth first. */
function tags(root) {
    const out = [];
    for (const node of root.childNodes) {
        if (node.nodeType !== 1) continue;
        out.push(node.tagName);
        out.push(...tags(node));
    }
    return out;
}

const results = {};

function check(name, value) {
    if (value !== true) throw new Error(`${name}: ${JSON.stringify(value)}`);
    results[name] = true;
}

// ─── scriptRemovedWithContents ───
// An inert parse does not make a tree safe. `<script>` must go WITH its text:
// unwrapping it would dump the source into the bubble as prose.
{
    const root = body(el("p", null, "before"), el("script", null, "alert(1)"), el("p", null, "after"));
    sanitize(root);
    check("scriptRemovedWithContents",
        tags(root).join(",") === "P,P" && root.textContent === "beforeafter");
}

// ─── styleRemovedWithContents ───
// Same rule, different damage: an unwrapped <style> repaints the whole shell.
{
    const root = body(el("style", null, ".msg{display:none}"), el("p", null, "hi"));
    sanitize(root);
    check("styleRemovedWithContents",
        tags(root).join(",") === "P" && root.textContent === "hi");
}

// ─── iframeRemoved ───
{
    const root = body(el("iframe", { src: "https://example.com" }));
    sanitize(root);
    check("iframeRemoved", tags(root).length === 0);
}

// ─── handlerAttributeStripped ───
// The allowlist is per tag, so `on*` cannot survive by construction — but this
// is the property that actually matters, so it is asserted directly.
{
    const node = el("p", { onclick: "steal()", onmouseover: "steal()" }, "hi");
    const root = body(node);
    sanitize(root);
    check("handlerAttributeStripped",
        node.getAttribute("onclick") === null
        && node.getAttribute("onmouseover") === null
        && root.textContent === "hi");
}

// ─── javascriptHrefUnwrapped ───
// The vendored marked v15 does not filter URL schemes (it carries no mention
// of `javascript` at all), so this check is ours or it does not happen.
{
    const root = body(el("a", { href: "javascript:alert(1)" }, "click me"));
    sanitize(root);
    check("javascriptHrefUnwrapped",
        tags(root).length === 0 && root.textContent === "click me");
}

// ─── dataHrefUnwrapped ───
{
    const root = body(el("a", { href: "data:text/html;base64,PHNjcmlwdD4=" }, "report"));
    sanitize(root);
    check("dataHrefUnwrapped", tags(root).length === 0 && root.textContent === "report");
}

// ─── httpsHrefKeptAndHardened ───
{
    const node = el("a", { href: "https://example.com/docs" }, "the docs");
    sanitize(body(node));
    check("httpsHrefKeptAndHardened",
        node.tagName === "A"
        && node.getAttribute("href") === "https://example.com/docs"
        && node.getAttribute("target") === "_blank"
        && node.getAttribute("rel") === "noopener noreferrer");
}

// ─── mailtoHrefKept ───
{
    const node = el("a", { href: "mailto:ops@example.com" }, "mail ops");
    sanitize(body(node));
    check("mailtoHrefKept", node.tagName === "A"
        && node.getAttribute("href") === "mailto:ops@example.com");
}

// ─── relativeHrefResolved ───
// Resolved against the document base rather than rejected: a relative link is
// the app's own route, and `new URL` needs the base to say so.
{
    const node = el("a", { href: "/files" }, "files");
    sanitize(body(node));
    check("relativeHrefResolved",
        node.tagName === "A" && node.getAttribute("href") === "https://bossmod.localhost/files");
}

// ─── targetInjectionOverwritten ───
// An agent naming its own target/rel must not keep them.
{
    const node = el("a", { href: "https://example.com", target: "_self", rel: "opener" }, "x");
    sanitize(body(node));
    check("targetInjectionOverwritten",
        node.getAttribute("target") === "_blank"
        && node.getAttribute("rel") === "noopener noreferrer");
}

// ─── imageBecomesAltText ───
// A remote fetch from a chat bubble both leaks and fails offline, so the alt
// text is what survives — the information, without the request.
{
    const root = body(el("img", { src: "https://tracker.example/pixel.gif", alt: "a bar chart" }));
    sanitize(root);
    check("imageBecomesAltText",
        tags(root).length === 0 && root.textContent === "a bar chart");
}

// ─── unknownTagUnwrappedKeepingText ───
// Unwrapped, not deleted: an agent's stray <span> must not eat the sentence
// it happens to be wrapped around.
{
    const root = body(el("div", null, el("span", null, "kept"), " and this"));
    sanitize(root);
    check("unknownTagUnwrappedKeepingText",
        tags(root).length === 0 && root.textContent === "kept and this");
}

// ─── unwrappedChildrenAreStillSanitised ───
// The recursion's sharp edge: children moved out of an unwrapped node have not
// been visited yet, so a payload could ride out inside one.
{
    const root = body(el("section", null, el("script", null, "alert(1)"), el("b", null, "safe")));
    sanitize(root);
    check("unwrappedChildrenAreStillSanitised",
        tags(root).length === 0 && root.textContent === "safe");
}

// ─── nestedHandlerStripped ───
{
    const inner = el("em", { onerror: "steal()" }, "deep");
    sanitize(body(el("blockquote", null, el("p", null, inner))));
    check("nestedHandlerStripped", inner.getAttribute("onerror") === null);
}

// ─── commentRemoved ───
{
    const root = body(el("p", null, "hi"));
    root.append(documentStub.createComment(" a comment "));
    sanitize(root);
    check("commentRemoved", root.childNodes.length === 1 && root.textContent === "hi");
}

// ─── codeLanguageClassKept ───
{
    const node = el("code", { class: "language-python" }, "print(1)");
    sanitize(body(el("pre", null, node)));
    check("codeLanguageClassKept", node.getAttribute("class") === "language-python");
}

// ─── unknownLanguageClassDropped ───
// Not a security property — a class hljs cannot resolve makes it warn on every
// repaint, and a console nobody can read is a console nobody reads.
{
    const node = el("code", { class: "language-brainfuck" }, "+++");
    sanitize(body(el("pre", null, node)));
    check("unknownLanguageClassDropped", node.getAttribute("class") === null);
}

// ─── mixedCaseLanguageNormalised ───
// `marked` copies a fence's tag through verbatim, so ```Python reaches here as
// `language-Python`. Rejecting it would drop the class and hand the block to
// hljs's auto-detection, which is the one path that guesses wrong.
{
    const node = el("code", { class: "language-PYTHON" }, "print(1)");
    sanitize(body(el("pre", null, node)));
    check("mixedCaseLanguageNormalised", node.getAttribute("class") === "language-python");
}

// ─── bogusCodeClassDropped ───
{
    const node = el("code", { class: "market-detail-installed" }, "x");
    sanitize(body(el("pre", null, node)));
    check("bogusCodeClassDropped", node.getAttribute("class") === null);
}

// ─── classOnAnyOtherTagDropped ───
// `class` is allowed on <code> alone; anywhere else it lets agent output
// borrow the shell's own styling.
{
    const node = el("p", { class: "transcript-status is-error" }, "not an error");
    sanitize(body(node));
    check("classOnAnyOtherTagDropped", node.getAttribute("class") === null);
}

// ─── taskCheckboxKeptAndDisabled ───
{
    const node = el("input", { type: "checkbox", checked: "" }, );
    sanitize(body(el("li", null, node)));
    check("taskCheckboxKeptAndDisabled",
        node.tagName === "INPUT"
        && node.getAttribute("type") === "checkbox"
        && node.getAttribute("disabled") !== null);
}

// ─── nonCheckboxInputRemoved ───
// A text input in a transcript is a credential prompt with no owner.
{
    const root = body(el("input", { type: "password", name: "token" }));
    sanitize(root);
    check("nonCheckboxInputRemoved", tags(root).length === 0);
}

// ─── tableWrappedForScroll ───
// A wide table must scroll in its own box; without the wrapper it widens the
// transcript and the whole conversation scrolls sideways.
{
    const table = el("table", null, el("tbody", null, el("tr", null, el("td", { align: "left" }, "a"))));
    const root = body(table);
    sanitize(root);
    const wrapper = root.childNodes[0];
    check("tableWrappedForScroll",
        wrapper.tagName === "DIV"
        && wrapper.getAttribute("class") === "md-scroll"
        && wrapper.childNodes[0] === table);
}

// ─── tableAlignmentKept ───
{
    const cell = el("td", { align: "right", onclick: "x()" }, "1");
    sanitize(body(el("table", null, el("tbody", null, el("tr", null, cell)))));
    check("tableAlignmentKept",
        cell.getAttribute("align") === "right" && cell.getAttribute("onclick") === null);
}

// ─── orderedListStartKept ───
{
    const node = el("ol", { start: "3" }, el("li", null, "third"));
    sanitize(body(node));
    check("orderedListStartKept", node.getAttribute("start") === "3");
}

// ─── renderPassesGfmAndBreaks ───
// `breaks` is what makes a single newline a line break, which is the shape a
// chat message is written in.
{
    nextBody = body(el("p", null, "hi"));
    render("hi");
    const last = parseCalls[parseCalls.length - 1];
    check("renderPassesGfmAndBreaks",
        last.options.gfm === true && last.options.breaks === true);
}

// ─── renderReturnsNodesNotMarkup ───
{
    nextBody = body(el("p", null, "one"), el("p", null, "two"));
    const nodes = render("ignored");
    check("renderReturnsNodesNotMarkup",
        Array.isArray(nodes) && nodes.length === 2 && nodes[0].tagName === "P");
}

// ─── renderSanitisesWhatTheParserProduced ───
{
    nextBody = body(el("p", { onclick: "steal()" }, "hi"), el("script", null, "alert(1)"));
    const nodes = render("ignored");
    check("renderSanitisesWhatTheParserProduced",
        nodes.length === 1
        && nodes[0].tagName === "P"
        && nodes[0].getAttribute("onclick") === null);
}

// ─── renderHighlightsFencedCodeOnly ───
// Highlighting runs AFTER the sanitiser, which is what lets <span> stay off
// the allowlist: hljs's own spans are added to a tree nothing inspects again.
// Inline <code> is not a block and is left alone.
{
    highlighted.length = 0;
    const fenced = el("code", { class: "language-python" }, "print(1)");
    const inline = el("code", null, "x");
    nextBody = body(el("pre", null, fenced), el("p", null, inline));
    render("ignored");
    check("renderHighlightsFencedCodeOnly",
        highlighted.length === 1
        && highlighted[0] === fenced
        && fenced.getAttribute("class").includes("hljs"));
}

// ─── untaggedFenceKeepsSurfaceButNoColour ───
// GitHub's behaviour, and the reason for it: highlightAuto guesses across every
// registered grammar, and against the real bundle a Go snippet comes back as C#
// and a raw diff as CSS. The block still gets `hljs` — it is a code block, it
// just is not a language.
{
    highlighted.length = 0;
    const untagged = el("code", null, "some output\n  indented");
    nextBody = body(el("pre", null, untagged));
    render("ignored");
    check("untaggedFenceKeepsSurfaceButNoColour",
        highlighted.length === 0
        && untagged.getAttribute("class") === "hljs"
        && untagged.textContent === "some output\n  indented");
}

// ─── unknownLanguageFenceFallsBackToUntagged ───
// ```brainfuck loses its class in the sanitiser, so by the time highlighting
// runs it IS the untagged case — one behaviour, not two.
{
    highlighted.length = 0;
    const fence = el("code", { class: "language-brainfuck" }, "+++");
    nextBody = body(el("pre", null, fence));
    render("ignored");
    check("unknownLanguageFenceFallsBackToUntagged",
        highlighted.length === 0 && fence.getAttribute("class") === "hljs");
}

// ─── renderOfEmptyTextIsEmpty ───
{
    nextBody = body();
    check("renderOfEmptyTextIsEmpty", render("").length === 0);
}

console.log(JSON.stringify({ ok: true, ...results }));
