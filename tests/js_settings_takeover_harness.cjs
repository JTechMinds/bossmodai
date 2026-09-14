/**
 * Node harness: the Settings takeover's one fact, `data-view` on <body>.
 * Invoked by tests/test_ui_settings_takeover.py. Not a browser bundle.
 *
 * Whether the takeover is VISIBLE is the cascade's answer and is asserted from
 * shell.css, because a fake DOM has no cascade to ask. What a fake DOM can
 * prove is the half JS still owns: that open() and close() write that one
 * attribute and nothing else, that `isOpen()` agrees with what they wrote, that
 * the section options an opener hands in reach the section once and are then
 * gone, and that every transition reaches an onViewChange listener — which is
 * how the header's gear knows what it is announcing.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

// BossModFormat.escapeHtml round-trips a string through textContent and reads
// `innerHTML` back off the scratch node, which the shared fake has no accessor
// for. Same shim tests/js_agent_form_harness.cjs installs, and for the same
// reason: an own property on the elements the DOCUMENT hands out, so the nodes
// this harness builds by hand below keep the shared fake's behaviour.
const createElement = documentStub.createElement.bind(documentStub);
documentStub.createElement = (tag) => {
    const el = createElement(tag);
    Object.defineProperty(el, "innerHTML", {
        get() {
            return String(this.textContent)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");
        },
        set(value) { this.textContent = value; },
        configurable: true,
    });
    return el;
};

const paths = process.argv.slice(2);
const NAMES = ["BossModFormat", "SettingsView"];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { SettingsView } = global;

// ─── The frame the takeover lives in, as index.html builds it ───
//
// Built with FakeEl rather than documentStub.createElement so they do NOT carry
// the innerHTML shim above: renderNav's `nav.innerHTML = html` is then an inert
// property write, which is what leaves the nav button below in place for the
// click that re-enters switchSection. The shim is for escapeHtml's scratch div
// and nothing else.
function element(tag, id, className) {
    const el = new FakeEl(tag);
    el.ownerDocument = documentStub;
    if (id) el.id = id;
    if (className) el.className = className;
    return el;
}

const mainLayout = element("div", "main-layout");
const settingsLayout = element("div", "settings-layout");
const nav = element("div", "settings-nav");
const content = element("div", "settings-content");
settingsLayout.append(nav, content);
documentStub.body.append(settingsLayout, mainLayout);

// The one nav button. renderNav binds every `[data-section]` it finds, so this
// is the harness's click path into switchSection — the only way to reach it a
// second time without going back through open(), which is what makes the
// one-shot check below a check of switchSection rather than of open().
const navButton = element("button", "", "");
navButton.setAttribute("data-section", "cli-policy");
nav.append(navButton);

// ─── The collaborators, as render spies ───

const renderCalls = [];
function section(name) {
    return { render: (el, options) => { renderCalls.push({ name, el, options }); } };
}
// Every global settings-view.js switches on. The spec counts seven — those are
// the seven section FILES; CliPolicySection is the eighth name in the switch
// and is the one this harness hands options to, so it is stubbed alongside them.
for (const name of ["ConnectionsSection", "PersonalitiesSection", "SystemSection",
    "CliPolicySection", "TelegramSection", "AdvancedSystemSection",
    "PromptTemplateSection", "RuntimeContractsSection"]) {
    global[name] = section(name);
}

let refreshCount = 0;
global.BossModBanners = { refreshModelAvailability: () => { refreshCount += 1; } };

function callsFor(name) {
    return renderCalls.filter((call) => call.name === name);
}

async function main() {
    // ─── 1. Nothing has claimed the view yet ───

    const noViewBeforeOpen = documentStub.body.dataset.view === undefined;
    if (!noViewBeforeOpen) {
        throw new Error("something set data-view before open() was ever called");
    }

    // The header's gear is told the takeover's state rather than deducing it
    // from its own click, so what a listener sees has to be every transition.
    const seen = [];
    const stopWatching = SettingsView.onViewChange((open) => { seen.push(open); });

    // ─── 2. Opening writes 'settings' and renders the default section ───

    SettingsView.open();
    const openSetsTheView = documentStub.body.dataset.view === "settings";
    if (!openSetsTheView) {
        throw new Error(`open() left data-view at ${documentStub.body.dataset.view}`);
    }
    if (SettingsView.isOpen() !== true) throw new Error("open() must report isOpen()");
    const listenerSeesOpen = seen.length === 1 && seen[0] === true;
    if (!listenerSeesOpen) throw new Error(`after open() a listener saw ${JSON.stringify(seen)}`);
    const rendersTheDefaultSection = callsFor("ConnectionsSection").length === 1;
    if (!rendersTheDefaultSection) {
        throw new Error(`Connections rendered ${callsFor("ConnectionsSection").length} times`);
    }
    if (callsFor("ConnectionsSection")[0].el !== content) {
        throw new Error("the section was not handed #settings-content");
    }
    // The old swap wrote `.hidden` onto both panels. Neither may be touched.
    for (const [name, node] of [["#main-layout", mainLayout], ["#settings-layout", settingsLayout]]) {
        if (node.className !== "") throw new Error(`open() wrote classes onto ${name}`);
    }

    // ─── 3. Closing writes 'app' and re-reads model availability ───

    SettingsView.close();
    const closeSetsTheView = documentStub.body.dataset.view === "app";
    if (!closeSetsTheView) {
        throw new Error(`close() left data-view at ${documentStub.body.dataset.view}`);
    }
    if (SettingsView.isOpen() !== false) throw new Error("close() must report isOpen()");
    const listenerSeesClose = seen.length === 2 && seen[1] === false;
    if (!listenerSeesClose) throw new Error(`after close() a listener saw ${JSON.stringify(seen)}`);
    const closeRefreshesModelAvailability = refreshCount === 1;
    if (!closeRefreshesModelAvailability) {
        throw new Error(`refreshModelAvailability ran ${refreshCount} times`);
    }

    // ─── 4. Section options reach the section once, and once only ───

    SettingsView.open("cli-policy", { rule: "x" });
    const cliCalls = callsFor("CliPolicySection");
    if (cliCalls.length !== 1) throw new Error(`CLI policy rendered ${cliCalls.length} times`);
    const optionsReachTheSection =
        cliCalls[0].options !== null
        && typeof cliCalls[0].options === "object"
        && cliCalls[0].options.rule === "x";
    if (!optionsReachTheSection) {
        throw new Error(`the section got ${JSON.stringify(cliCalls[0].options)}`);
    }

    // Back into switchSection through the nav, with no second open() to reset
    // anything. renderNav re-binds on every call and the button is never
    // replaced, so the click fires one handler per render — and every one of
    // them must find the options already spent.
    await navButton.dispatchClick();
    const spent = callsFor("CliPolicySection").slice(1);
    const optionsAreOneShot =
        spent.length > 0 && spent.every((call) => call.options === null);
    if (!optionsAreOneShot) {
        throw new Error(`re-entering switchSection got ${JSON.stringify(spent.map((c) => c.options))}`);
    }

    // ─── 5. The disposer actually detaches ───
    //
    // The frame never unmounts, so nothing in the app calls this today. It is
    // the contract onViewChange returns, and a disposer that does not dispose
    // is worse than none: the leak only shows up under a second listener.
    if (seen.length !== 3) throw new Error(`re-opening pushed ${seen.length} transitions`);
    stopWatching();
    SettingsView.close();
    const disposerStopsTheListener = seen.length === 3;
    if (!disposerStopsTheListener) {
        throw new Error(`a disposed listener still saw ${JSON.stringify(seen)}`);
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        noViewBeforeOpen,
        listenerSeesOpen,
        listenerSeesClose,
        disposerStopsTheListener,
        openSetsTheView,
        rendersTheDefaultSection,
        closeSetsTheView,
        closeRefreshesModelAvailability,
        optionsReachTheSection,
        optionsAreOneShot,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
