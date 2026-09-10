/**
 * Node harness: core/icons.js paints one scope, once, or fails out loud.
 *
 * Run against the REAL vendored lucide bundle (it is a UMD, so `require`
 * hands back the same `icons` map and `createElement` the browser gets) and
 * the shared fake DOM. Stubbing lucide here would prove nothing: the two
 * behaviours this module exists to correct — an ignored `nodes` option and an
 * SVG that carries `data-lucide` forward so it gets repainted forever — are
 * behaviours of the real bundle.
 *
 * Invoked by tests/test_ui_icons.py. Not a browser bundle.
 *
 * argv: [2] core/icons.js  [3] vendor/lucide.min.js  [4] ui/static/js root
 */
const fs = require("fs");
const path = require("path");
const { installDom } = require("./js_fake_dom.cjs");

const document = installDom();
global.window.lucide = require(path.resolve(process.argv[3]));

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModIcons = BossModIcons;\n`);

const { h } = (() => {
    /** The two lines of DOM building this harness needs, not a second dom.js. */
    function build(tag, attrs, ...children) {
        const el = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
        children.forEach((child) => el.append(child));
        return el;
    }
    return { h: build };
})();

/** @returns {Error} The error `fn` threw. @throws When it threw nothing. */
function thrown(fn) {
    try {
        fn();
    } catch (err) {
        return err;
    }
    throw new Error("expected a throw, got none");
}

const svgs = (root) => root.querySelectorAll("svg");
const placeholders = (root) => root.querySelectorAll("[data-lucide]");

// ── Scope is real ────────────────────────────────────────────────────────
// The defect in one case: painting one panel must not touch the other.
const mine = h("div", { id: "mine" }, h("i", { "data-lucide": "plus" }));
const theirs = h("div", { id: "theirs" }, h("i", { "data-lucide": "settings" }));
document.body.append(mine, theirs);

const paintedMine = BossModIcons.paint(mine, "harness.scope");
const scopeIsReal = paintedMine === 1
    && svgs(mine).length === 1
    && placeholders(mine).length === 0
    // Untouched: still a placeholder, still an <i>, still the same node.
    && placeholders(theirs).length === 1
    && theirs.children[0].tagName === "I";

// ── Idempotent by construction ───────────────────────────────────────────
// The painted node must survive a second pass, identity included: the shell
// paints the same tree on every render, and a rebuild here is the bug.
const firstPassNode = mine.children[0];
const secondPass = BossModIcons.paint(mine, "harness.repaint");
const secondPaintKeepsNodes = secondPass === 0
    && mine.children[0] === firstPassNode
    && firstPassNode.getAttribute("data-lucide") === null;

// ── Source attributes and id survive ─────────────────────────────────────
// The real markup from context/agent-form-advanced.js. #advanced-chevron is
// looked up AFTER painting and rotated by class toggle, so the id and the
// transition-transform class have to come across intact.
const toggle = h("button", { id: "advanced-toggle" },
    h("span", {}, "Advanced"),
    h("i", {
        "data-lucide": "chevron-right",
        class: "w-4 h-4 text-bm-muted shrink-0 transition-transform",
        id: "advanced-chevron",
    }));
document.body.append(toggle);
BossModIcons.paint(toggle, "harness.advanced");

const chevron = document.body.querySelector("#advanced-chevron");
const chevronClasses = String(chevron && chevron.getAttribute("class")).split(" ");
// The disclosure writes this on click; it has to reach the painted node.
if (chevron) chevron.style.transform = "rotate(90deg)";
const advancedChevronSurvives = Boolean(chevron)
    && chevron.tagName.toLowerCase() === "svg"
    && chevron.getAttribute("id") === "advanced-chevron"
    && ["w-4", "h-4", "text-bm-muted", "shrink-0", "transition-transform"]
        .every((name) => chevronClasses.includes(name))
    // Lucide's own two classes, composed in front, exactly as its replace does.
    && chevronClasses[0] === "lucide"
    && chevronClasses[1] === "lucide-chevron-right"
    && chevron.getAttribute("data-lucide") === null
    && chevron.style.transform === "rotate(90deg)"
    && chevron.children.length === 1;

// ── An unknown name fails loudly, and changes nothing ────────────────────
const mixed = h("div", {},
    h("i", { "data-lucide": "plus" }),
    h("i", { "data-lucide": "totally-not-an-icon" }));
document.body.append(mixed);
const unknownError = thrown(() => BossModIcons.paint(mixed, "harness.unknown"));
const unknownIconThrows = unknownError.message.includes("totally-not-an-icon")
    && unknownError.message.includes("TotallyNotAnIcon")
    && unknownError.message.includes("harness.unknown")
    // Resolved before anything was replaced: the good icon is untouched, so a
    // bad name leaves a fixable tree rather than a half-painted one.
    && placeholders(mixed).length === 2
    && svgs(mixed).length === 0;
// Off the document again: the sweep below is over everything still attached,
// and this subtree is deliberately unpaintable.
mixed.remove();

// ── The root itself counts ───────────────────────────────────────────────
const lone = h("i", { "data-lucide": "menu", class: "responsive-menu-icon" });
const holder = h("div", {}, lone);
document.body.append(holder);
const paintsTheRootItself = BossModIcons.paint(lone, "harness.self") === 1
    && holder.children[0].tagName.toLowerCase() === "svg"
    && holder.children[0].getAttribute("class").includes("responsive-menu-icon");

// ── A placeholder with no parent cannot be swapped: say so ───────────────
const orphan = h("i", { "data-lucide": "menu" });
const detachedRootThrows = thrown(() => BossModIcons.paint(orphan, "harness.orphan"))
    .message.includes("no parent");

// ── The document sweep settles ───────────────────────────────────────────
// The five shell callers sweep the document because they paint placeholders
// other modules build (conversation/composer.js builds two and paints none).
// The sweep stays; what changes is that the second one is free.
document.body.append(h("div", {}, h("i", { "data-lucide": "bell" })));
const firstSweep = BossModIcons.paintDocument("harness.sweep");
const secondSweep = BossModIcons.paintDocument("harness.sweep");
const paintDocumentSweepsAndSettles = firstSweep > 0 && secondSweep === 0;

// ── A missing bundle is a broken build, not a state to paint around ──────
const restore = global.window.lucide;
global.window.lucide = undefined;
const missingVendorThrows = thrown(() => BossModIcons.paint(document.body, "harness.novendor"))
    .message.includes("window.lucide is missing");
global.window.lucide = restore;

// ── Every name the app ships resolves ────────────────────────────────────
// paint() throws on an unknown name by design. That is only safe while every
// name in the tree is a real one, so the tree is the test.
function jsFiles(dir) {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return entry.name === "vendor" ? [] : jsFiles(full);
        return entry.name.endsWith(".js") ? [full] : [];
    });
}

const NAME_IN_SOURCE = /data-lucide['"]?\s*[:=]\s*['"]([a-z0-9-]+)['"]/g;
const unresolvable = [];
let namesSeen = 0;
for (const file of jsFiles(path.resolve(process.argv[4]))) {
    const text = fs.readFileSync(file, "utf8");
    for (const [, name] of text.matchAll(NAME_IN_SOURCE)) {
        namesSeen += 1;
        if (!(BossModIcons.iconKey(name) in global.window.lucide.icons)) {
            unresolvable.push(`${path.basename(file)}: ${name}`);
        }
    }
}
const everyShippedNameResolves = namesSeen >= 20 && unresolvable.length === 0;

// ── Name conversion matches the icon set at the edges the vendor cares about ─
const nameConversionMatchesIconSet = ["bar-chart-3", "octagon-x", "message-circle",
    "trash-2", "x", "shield-check"]
    .every((name) => BossModIcons.iconKey(name) in global.window.lucide.icons)
    && BossModIcons.iconKey("bar-chart-3") === "BarChart3"
    && BossModIcons.iconKey("octagon-x") === "OctagonX"
    && BossModIcons.iconKey("message-circle") === "MessageCircle";

process.stdout.write(`${JSON.stringify({
    ok: true,
    scopeIsReal,
    secondPaintKeepsNodes,
    advancedChevronSurvives,
    unknownIconThrows,
    paintsTheRootItself,
    detachedRootThrows,
    paintDocumentSweepsAndSettles,
    missingVendorThrows,
    everyShippedNameResolves,
    nameConversionMatchesIconSet,
    unresolvable,
})}\n`);
