/**
 * Node harness: header accessibility contract and the Pause interaction.
 *
 * Checks the properties that cannot be read off the source text: which
 * buttons end up with an accessible name, that aria-current tracks the live
 * store, that the bell's label states the current count, that Pause routes
 * through a dialog while Resume does not, and that the disposer drains every
 * subscription.
 *
 * Invoked by tests/test_ui_header.py. Not a browser bundle.
 */
const fs = require("fs");

let activeElement = null;

function makeEl(tag) {
    return {
        tagName: String(tag).toUpperCase(),
        nodeType: 1,
        attributes: {},
        children: [],
        listeners: {},
        parentNode: null,
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        removeAttribute(k) { delete this.attributes[k]; },
        hasAttribute(k) { return k in this.attributes; },
        append(...kids) {
            // Mirrors the real DOM: a bare string becomes a text node.
            kids.forEach((raw) => {
                const k = (raw && raw.nodeType) ? raw : { nodeType: 3, textContent: String(raw) };
                if (k.nodeType === 1) k.parentNode = this;
                this.children.push(k);
            });
        },
        remove() {
            if (!this.parentNode) return;
            const i = this.parentNode.children.indexOf(this);
            if (i !== -1) this.parentNode.children.splice(i, 1);
            this.parentNode = null;
        },
        replaceChildren() { this.children = []; },
        addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
        removeEventListener(n, fn) {
            const l = this.listeners[n] || [];
            const i = l.indexOf(fn);
            if (i !== -1) l.splice(i, 1);
        },
        focus() { activeElement = this; },
        contains(other) {
            if (other === this) return true;
            return this.children.some((c) => c && c.contains && c.contains(other));
        },
        // core/overlays.js asks a modal's body for its focusable content to
        // decide where the keyboard opens, and core/overlay-focus.js asks the
        // panel for the same to trap Tab. Answered by TAG rather than by
        // parsing the selector — the same fake tests/js_overlays_harness.cjs
        // uses, and honest about being a fake. Pause's dialog carries a
        // sentence and two buttons, so the answer here is the action row.
        querySelectorAll() {
            const FOCUSABLE_TAGS = ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"];
            const out = [];
            const walk = (node) => {
                (node.children || []).forEach((c) => {
                    if (!c || c.nodeType !== 1) return;
                    if (FOCUSABLE_TAGS.includes(c.tagName) && !c.disabled) out.push(c);
                    walk(c);
                });
            };
            walk(this);
            return out;
        },
    };
}

const body = makeEl("body");
global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
    body,
    listeners: {},
    addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
    removeEventListener(n, fn) {
        const l = this.listeners[n] || [];
        const i = l.indexOf(fn);
        if (i !== -1) l.splice(i, 1);
    },
    get activeElement() { return activeElement; },
};
global.window = { document: global.document };
let iconPasses = 0;
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub(() => { iconPasses += 1; });

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModStore = BossModStore;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModOverlayFocus = BossModOverlayFocus;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModOverlays = BossModOverlays;\n`);
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModPlaces = BossModPlaces;\n`);
eval(`${fs.readFileSync(process.argv[7], "utf8")}\n;global.BossModHeader = BossModHeader;\n`);

/** Text a screen reader would announce: aria-hidden subtrees contribute nothing. */
function accessibleText(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.textContent;
    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return "";
    return (node.children || []).map(accessibleText).join("");
}

function buttons(node, out) {
    (node.children || []).forEach((child) => {
        if (child && child.tagName === "BUTTON") out.push(child);
        if (child && child.children) buttons(child, out);
    });
    return out;
}

function byAttr(node, name, value, out) {
    (node.children || []).forEach((child) => {
        if (child && child.getAttribute && child.getAttribute(name) === value) out.push(child);
        if (child && child.children) byAttr(child, name, value, out);
    });
    return out;
}

function click(el) {
    (el.listeners.click || []).forEach((fn) => fn({ preventDefault() {}, stopPropagation() {} }));
}

const settled = () => new Promise((resolve) => setImmediate(resolve));

const apiCalls = [];
function apiFetch(url, init) {
    apiCalls.push({ url, init: init || null });
    if (url === "/api/settings") {
        return Promise.resolve({
            ok: true,
            json: () => Promise.resolve([
                { key: "tick_interval", value: "0.25", category: "simulation" },
                { key: "company_name", value: "JTechMinds", category: "company" },
            ]),
        });
    }
    if (url === "/api/runtime/state") {
        const paused = JSON.parse(init.body).paused;
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ paused }) });
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
}

(async () => {
    const store = BossModStore.createStore({
        place: "chat",
        placeParams: {},
        needs: [{ id: "a" }, { id: "b" }, { id: "c" }],
        runtimePaused: false,
    });
    const storeBaseline = store.subscriberCount();
    const el = makeEl("header");
    const navigated = [];
    let settingsOpened = 0;

    // The bell opens the popover, which resolves through this. Nothing here
    // clicks the bell, so a shaped stub is enough; the popover's own behaviour
    // is proven in js_needs_harness.cjs against the real store.
    const needsStub = {
        refresh: () => Promise.resolve(),
        resolve: () => Promise.resolve(),
        getError: () => "",
        subscribeError: () => () => {},
        destroy: () => {},
    };

    const dispose = BossModHeader.mount(el, {
        store,
        apiFetch,
        navigate: (id) => navigated.push(id),
        needs: needsStub,
        openSettings: () => { settingsOpened += 1; },
    });
    if (typeof dispose !== "function") throw new Error("mount must return a disposer");
    // The company-name load is a two-promise chain (request, then json()).
    await settled();
    await settled();
    await settled();

    // Every button that renders no accessible text needs an explicit name.
    const unlabelled = buttons(el, []).filter(
        (b) => accessibleText(b).trim() === "" && !String(b.getAttribute("aria-label") || "").trim()
    );
    if (unlabelled.length !== 0) {
        throw new Error(`icon-only buttons without aria-label: ${unlabelled.map((b) => b.getAttribute("class")).join(", ")}`);
    }

    // The nav marks exactly one active place, and it follows the store.
    const navItems = buttons(el, []).filter((b) => b.getAttribute("data-place"));
    if (navItems.length !== 6) throw new Error(`expected 6 nav items, got ${navItems.length}`);
    let current = byAttr(el, "aria-current", "page", []);
    if (current.length !== 1 || current[0].getAttribute("data-place") !== "chat") {
        throw new Error(`aria-current must mark chat alone, got ${current.map((c) => c.getAttribute("data-place")).join(",")}`);
    }
    store.setState({ place: "board" });
    current = byAttr(el, "aria-current", "page", []);
    if (current.length !== 1 || current[0].getAttribute("data-place") !== "board") {
        throw new Error("aria-current did not follow the store to board");
    }
    click(navItems[0]);
    if (navigated[navigated.length - 1] !== "chat") throw new Error("nav button must navigate");

    // The bell states the count, and restates it when the count changes.
    const bell = buttons(el, []).filter((b) => String(b.getAttribute("class") || "").includes("header-bell"))[0];
    if (!bell) throw new Error("header must render a bell button");
    if (!String(bell.getAttribute("aria-label")).includes("3")) {
        throw new Error(`bell label must state the count, got "${bell.getAttribute("aria-label")}"`);
    }
    store.setState({ needs: [{ id: "a" }] });
    if (!String(bell.getAttribute("aria-label")).includes("1")) {
        throw new Error(`bell label must restate the count, got "${bell.getAttribute("aria-label")}"`);
    }

    // The company name comes from GET /api/settings.
    if (!accessibleText(el).includes("JTechMinds")) {
        throw new Error(`company name missing from the header: ${accessibleText(el)}`);
    }

    // Pause: click opens a dialog. Cancelling changes nothing.
    const pause = buttons(el, []).filter((b) => String(b.getAttribute("class") || "").includes("header-pause"))[0];
    if (!pause) throw new Error("header must render a Pause button");
    click(pause);
    let dialogs = byAttr(body, "role", "dialog", []);
    if (dialogs.length !== 1) throw new Error(`Pause must open exactly one dialog, got ${dialogs.length}`);
    const puts = () => apiCalls.filter((c) => c.url === "/api/runtime/state");
    if (puts().length !== 0) throw new Error("the dialog must not pause before it is confirmed");
    const cancel = buttons(dialogs[0], []).filter((b) => b.textLabel === "Cancel")[0];
    if (!cancel) throw new Error("the Pause dialog needs a Cancel action");
    click(cancel);
    await settled();
    if (puts().length !== 0) throw new Error("cancelling must not pause the runtime");

    // Confirming pauses.
    click(pause);
    dialogs = byAttr(body, "role", "dialog", []);
    const confirmAction = buttons(dialogs[0], []).filter((b) => b.textLabel !== "Cancel")[0];
    click(confirmAction);
    await settled();
    await settled();
    if (puts().length !== 1) throw new Error(`expected one PUT, got ${puts().length}`);
    if (puts()[0].init.method !== "PUT") throw new Error("runtime state change must be a PUT");
    if (JSON.parse(puts()[0].init.body).paused !== true) throw new Error("confirm must send paused: true");
    if (store.getState().runtimePaused !== true) throw new Error("store must record the pause");

    // Resume is a single click with no dialog.
    const dialogsBefore = byAttr(body, "role", "dialog", []).length;
    click(pause);
    await settled();
    await settled();
    if (byAttr(body, "role", "dialog", []).length !== dialogsBefore) {
        throw new Error("Resume must not open a dialog");
    }
    if (puts().length !== 2) throw new Error(`expected a second PUT, got ${puts().length}`);
    if (JSON.parse(puts()[1].init.body).paused !== false) throw new Error("Resume must send paused: false");

    // The gear opens Settings.
    const gear = buttons(el, []).filter((b) => String(b.getAttribute("class") || "").includes("header-gear"))[0];
    if (!gear) throw new Error("header must render a settings button");
    click(gear);
    if (settingsOpened !== 1) throw new Error("the gear must open Settings");

    if (iconPasses < 1) throw new Error("header must render its Lucide icons");

    // The disposer drains every store subscription.
    dispose();
    if (store.subscriberCount() !== storeBaseline) {
        throw new Error(`store leak: baseline ${storeBaseline}, now ${store.subscriberCount()}`);
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        everyIconOnlyButtonIsLabelled: true,
        navMarksActivePlace: true,
        bellLabelStatesCount: true,
        pauseConfirmsResumeDoesNot: true,
        disposerDrainsSubscriptions: true,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
