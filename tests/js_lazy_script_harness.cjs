/**
 * Node harness: vendored scripts loaded on first use, and their two consumers.
 *
 * Part A runs the REAL core/lazy-script.js against the shared fake DOM, with a
 * <head> and a same-origin `window.location` added: the fake has no network,
 * so the harness plays the browser and fires each injected script's
 * onload/onerror itself.
 *
 * Part B swaps in a lazy loader the harness settles by hand, and drives the
 * three call sites through the state a real launch starts in — highlight.js
 * and Tabulator NOT defined:
 *   core/markdown.js           a declared fence asks for highlight.js
 *   places/files/file-content  a code file asks for it too
 *   core/data-table.js         a table asks for Tabulator
 *
 * Invoked by tests/test_lazy_vendor_scripts.py. Not a browser bundle.
 *
 * argv: [2] core/dom.js [3] core/lazy-script.js [4] core/markdown.js
 *       [5] places/files/file-content.js [6] core/data-table.js
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const document = installDom();
const ORIGIN = "https://bossmod.localhost";
global.window.location = { origin: ORIGIN, href: `${ORIGIN}/` };
document.head = document.createElement("head");
// The real document's querySelectorAll covers <head>; the fake's covers <body>.
const bodyQueryAll = document.querySelectorAll.bind(document);
document.querySelectorAll = (selector) => [
    ...document.head.querySelectorAll(selector), ...bodyQueryAll(selector)];

const [domPath, lazyPath, markdownPath, fileContentPath, dataTablePath] = process.argv.slice(2);
const load = (file, name) => eval(`${fs.readFileSync(file, "utf8")}\n;global.${name} = ${name};\n`);
load(domPath, "BossModDom");
load(lazyPath, "BossModLazyScript");
const Real = global.BossModLazyScript;

const verdict = { ok: true };
const tick = () => new Promise((resolve) => setImmediate(resolve));
const scripts = () => document.head.querySelectorAll("script");

function meta(library, content) {
    const node = document.createElement("meta");
    node.setAttribute("name", "bossmod-lazy-script");
    node.setAttribute("data-library", library);
    node.setAttribute("content", content);
    document.head.append(node);
}

/** Wrap console.error for the length of `fn`, returning what it logged. */
async function logged(fn) {
    const lines = [];
    const real = console.error;
    console.error = (...args) => { lines.push(args.map(String).join(" ")); };
    try {
        await fn();
    } finally {
        console.error = real;
    }
    return lines;
}

(async () => {
    // ── A. The loader itself ──────────────────────────────────────────────
    meta("highlight", "/static/js/vendor/highlight.min.js?h=abc");
    meta("tabulator", "/static/js/vendor/tabulator.min.js?h=def");
    meta("elsewhere", "https://cdn.example.com/x.js");

    const first = Real.load("highlight", "hljs");
    const again = Real.load("highlight", "hljs");
    await tick();
    const injected = scripts();
    verdict.injectsOneScriptFromTheMeta = injected.length === 1
        && injected[0].src === `${ORIGIN}/static/js/vendor/highlight.min.js?h=abc`
        && first === again;
    const hljsStub = { name: "hljs" };
    global.window.hljs = hljsStub;
    injected[0].onload();
    verdict.resolvesWithTheGlobal = (await first) === hljsStub;
    verdict.aDefinedGlobalInjectsNothing = (await Real.load("highlight", "hljs")) === hljsStub
        && scripts().length === 1;

    // A network failure rejects, removes its tag, and is not remembered.
    const failing = Real.load("tabulator", "Tabulator");
    await tick();
    const failedTag = scripts()[scripts().length - 1];
    failedTag.onerror();
    let failure = null;
    await failing.catch((err) => { failure = err; });
    verdict.aFailedLoadRejectsAndRemovesItsTag = Boolean(failure)
        && failure.message.includes("tabulator.min.js") && !scripts().includes(failedTag);
    const retry = Real.load("tabulator", "Tabulator");
    await tick();
    const retryTag = scripts()[scripts().length - 1];
    verdict.aFailedLoadIsRetried = retry !== failing && retryTag !== failedTag
        && retryTag.src.endsWith("tabulator.min.js?h=def");
    // A script that ran but defined nothing is a broken vendor file, not a library.
    retryTag.onload();
    let undefinedGlobal = null;
    await retry.catch((err) => { undefinedGlobal = err; });
    verdict.aScriptThatDefinesNothingRejects = Boolean(undefinedGlobal)
        && undefinedGlobal.message.includes("window.Tabulator");

    const before = scripts().length;
    let offOrigin = null;
    await Real.load("elsewhere", "Elsewhere").catch((err) => { offOrigin = err; });
    verdict.offOriginIsRefusedBeforeInjecting = Boolean(offOrigin)
        && offOrigin.message.includes("'self' only") && scripts().length === before;
    let noMeta = null;
    await Real.load("nothing", "Nothing").catch((err) => { noMeta = err; });
    verdict.aMissingMetaRejectsNamingIt = Boolean(noMeta) && noMeta.message.includes('data-library="nothing"')
        && scripts().length === before;

    // ── B. The consumers, with a loader the harness settles by hand ───────
    delete global.window.hljs;
    const requests = [];
    global.BossModLazyScript = {
        load(library, globalName) {
            return new Promise((resolve, reject) => { requests.push({ library, globalName, resolve, reject }); });
        },
    };
    const highlighted = [];
    const fakeHljs = {
        getLanguage: (name) => (name === "python" ? {} : undefined),
        highlightElement: (node) => { highlighted.push(node); },
    };
    let nextBody = null;
    global.marked = { parse: (text) => String(text) };
    global.DOMParser = class { parseFromString() { return { body: nextBody }; } };
    load(markdownPath, "BossModMarkdown");
    load(fileContentPath, "BossModFileContent");
    load(dataTablePath, "BossModDataTable");

    function fence(language) {
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        if (language) code.setAttribute("class", `language-${language}`);
        code.append(document.createTextNode("x = 1"));
        pre.append(code);
        return { pre, code };
    }

    // Untagged code never asks for highlight.js.
    const plain = fence(null);
    nextBody = document.createElement("body");
    nextBody.append(plain.pre);
    global.BossModMarkdown.render("plain");
    verdict.untaggedCodeLoadsNothing = requests.length === 0 && plain.code.getAttribute("class") === "hljs";

    // A declared fence asks once per render; the colour lands when it loads.
    const python = fence("Python");
    const unknown = fence("brainfuck");
    nextBody = document.createElement("body");
    nextBody.append(python.pre, unknown.pre);
    global.BossModMarkdown.render("two fences");
    const asked = requests.length === 1 && requests[0].library === "highlight" && requests[0].globalName === "hljs";
    const waitingPlain = highlighted.length === 0
        && python.code.getAttribute("class") === "language-python hljs";
    requests[0].resolve(fakeHljs);
    await tick();
    verdict.aDeclaredFenceLoadsHighlightJs = asked && waitingPlain;
    verdict.theFenceIsHighlightedWhenItLands = highlighted.length === 1 && highlighted[0] === python.code;
    verdict.anUnknownLanguageIsDroppedOnceKnown = unknown.code.getAttribute("class") === "hljs"
        && !highlighted.includes(unknown.code);

    // A failed load is reported, and the code stays readable text.
    const lost = fence("python");
    nextBody = document.createElement("body");
    nextBody.append(lost.pre);
    global.BossModMarkdown.render("lost");
    const failedLines = await logged(async () => {
        requests[requests.length - 1].reject(new Error("highlight.min.js failed to load"));
        await tick();
    });
    verdict.aFailedHighlighterIsLogged = failedLines.length === 1
        && failedLines[0].includes("highlight.min.js failed to load")
        && lost.code.textContent === "x = 1";

    // The file viewer's code block goes through the same door.
    const asksBefore = requests.length;
    const block = global.BossModFileContent.codeBlock("print(1)", "python");
    const fileCode = block.querySelector("code");
    const fileAsked = requests.length === asksBefore + 1 && requests[requests.length - 1].library === "highlight";
    requests[requests.length - 1].resolve(fakeHljs);
    await tick();
    verdict.aCodeFileLoadsHighlightJs = fileAsked && highlighted.includes(fileCode)
        && fileCode.getAttribute("class").includes("language-python");

    // Once loaded, highlighting is immediate and asks for nothing.
    global.hljs = fakeHljs;
    const asksLoaded = requests.length;
    const now = fence("python");
    nextBody = document.createElement("body");
    nextBody.append(now.pre);
    global.BossModMarkdown.render("loaded");
    verdict.aLoadedHighlighterIsUsedAtOnce = requests.length === asksLoaded && highlighted.includes(now.code);

    // Tabulator: loading state, built when it lands.
    const built = [];
    class FakeTabulator {
        constructor(el, opts) { this.el = el; this.opts = opts; this.destroyed = false; built.push(this); }
        on() {}
        setData() {}
        destroy() { this.destroyed = true; }
    }
    const tableOptions = {
        caption: "Inbox", columns: [{ key: "subject", label: "Subject" }], rowLabel: () => "Open",
        onActivate: () => {}, loadPage: async () => ({ rows: [], has_more: false }), pageSize: 25,
        emptyText: "Nothing here yet.",
    };
    const table = global.BossModDataTable.create(tableOptions);
    const tabulatorAsk = requests[requests.length - 1];
    verdict.aTableLoadsTabulatorAndShowsLoading = tabulatorAsk.library === "tabulator"
        && tabulatorAsk.globalName === "Tabulator" && built.length === 0
        && table.element.textContent.includes("Loading…");
    global.Tabulator = FakeTabulator;
    tabulatorAsk.resolve(FakeTabulator);
    await tick();
    verdict.theTableIsBuiltWhenItLands = built.length === 1
        && built[0].el.getAttribute("aria-label") === "Inbox" && built[0].opts.paginationMode === "remote";
    const asksTables = requests.length;
    const second = global.BossModDataTable.create(tableOptions);
    verdict.aLaterTableBuildsAtOnce = built.length === 2 && requests.length === asksTables;
    second.destroy();
    table.destroy();
    delete global.Tabulator;

    // A failed load is the error state, logged, and "Try again" retries it.
    const broken = global.BossModDataTable.create(tableOptions);
    const tableLines = await logged(async () => {
        requests[requests.length - 1].reject(new Error("tabulator.min.js failed to load"));
        await tick();
    });
    const alertEl = broken.element.querySelector(".data-table-error");
    verdict.aFailedTableLoadShowsTheError = tableLines.length === 1
        && alertEl && !alertEl.hidden && alertEl.textContent.includes("tabulator.min.js failed to load");
    const asksBeforeRetry = requests.length;
    alertEl.querySelector("button").click();
    const retried = requests.length === asksBeforeRetry + 1 && requests[requests.length - 1].library === "tabulator";
    global.Tabulator = FakeTabulator;
    requests[requests.length - 1].resolve(FakeTabulator);
    await tick();
    verdict.tryAgainRetriesTheLoad = retried && built.length === 3 && alertEl.hidden;
    broken.destroy();
    delete global.Tabulator;

    // Closed before the library arrived: nothing is built into a dead dialog.
    const gone = global.BossModDataTable.create(tableOptions);
    gone.destroy();
    requests[requests.length - 1].resolve(FakeTabulator);
    await tick();
    verdict.aTableDestroyedWhileLoadingIsNeverBuilt = built.length === 3;

    console.log(JSON.stringify(verdict));
})().catch((err) => { process.stderr.write(String(err && err.stack ? err.stack : err)); process.exit(1); });
