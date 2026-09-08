/**
 * Node harness: every button the shell renders has an accessible name.
 *
 * Mounts the header, roster, and footer into a fake DOM and walks the result,
 * because "icon-only buttons carry aria-label" is a property of what renders,
 * not of how the source is spelled.
 *
 * It counts what it walked before it judges it, and reports the counts. A
 * naming check over an empty walk is not a pass — see CONTROL_FLOOR below.
 *
 * Invoked by tests/test_ui_a11y_contract.py. Not a browser bundle.
 */
const fs = require("fs");

function makeEl(tag) {
    const el = {
        tagName: String(tag).toUpperCase(),
        nodeType: 1,
        attributes: {},
        classes: new Set(),
        children: [],
        listeners: {},
        parentNode: null,
        value: "",
        selectionStart: 0,
        checked: false,
        disabled: false,
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        removeAttribute(k) { delete this.attributes[k]; },
        append(...kids) {
            kids.forEach((raw) => {
                const k = (raw && raw.nodeType) ? raw : { nodeType: 3, textContent: String(raw) };
                if (k.nodeType === 1) k.parentNode = this;
                this.children.push(k);
            });
        },
        remove() {},
        replaceChildren() { this.children = []; },
        addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
        removeEventListener() {},
        focus() {},
    };
    el.classList = {
        add: (c) => el.classes.add(c),
        remove: (c) => el.classes.delete(c),
        contains: (c) => el.classes.has(c),
        toggle: (c, on) => { if (on) el.classes.add(c); else el.classes.delete(c); },
    };
    return el;
}

global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
    body: makeEl("body"),
    getElementById() { return null; },
    addEventListener() {},
    removeEventListener() {},
    activeElement: null,
};
global.window = { document: global.document };
global.lucide = { createIcons() {} };

const [
    dom, avatar, store, bus, format, agentStatus, overlayFocus, overlays, rowMeta, places, header,
    rosterPeople, threadCreate, rosterThreads, roster, footer,
] = process.argv.slice(2);
const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
load(dom, "BossModDom");
load(avatar, "BossModAvatar");
load(store, "BossModStore");
load(bus, "BossModBus");
// A person row renders its last-activity timestamp through this.
load(format, "BossModFormat");
load(agentStatus, "BossModAgentStatus");
load(rowMeta, "BossModRosterRowMeta");
load(overlayFocus, "BossModOverlayFocus");
load(overlays, "BossModOverlays");
load(places, "BossModPlaces");
load(header, "BossModHeader");
load(rosterPeople, "BossModRosterPeople");
load(threadCreate, "BossModThreadCreate");
load(rosterThreads, "BossModRosterThreads");
load(roster, "BossModRoster");
load(footer, "BossModFooter");

/** Text a screen reader would announce: aria-hidden subtrees contribute nothing. */
function accessibleText(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.textContent;
    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return "";
    return (node.children || []).map(accessibleText).join("");
}

/** ids named by a <label for="..."> somewhere in the subtree. */
function labelledIds(node, out) {
    (node.children || []).forEach((child) => {
        if (child && child.nodeType === 1) {
            if (child.tagName === "LABEL" && child.getAttribute("for")
                && accessibleText(child).trim() !== "") {
                out.add(child.getAttribute("for"));
            }
            labelledIds(child, out);
        }
    });
    return out;
}

function controls(node, out) {
    (node.children || []).forEach((child) => {
        if (child && child.nodeType === 1) {
            if (child.tagName === "BUTTON" || child.tagName === "INPUT") out.push(child);
            controls(child, out);
        }
    });
    return out;
}

const settled = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 6; i += 1) await settled(); };

function apiFetch(url) {
    if (url.startsWith("/api/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([{ key: "company_name", value: "JTechMinds" }]) });
    }
    if (url.startsWith("/api/world")) {
        return Promise.resolve({
            ok: true,
            json: () => Promise.resolve([
                { id: "a1", name: "Jim", role: "Engineer", color: "#3b82f6", status: "idle", x: 1, y: 1 },
            ]),
        });
    }
    if (url.startsWith("/api/channels")) {
        return Promise.resolve({
            ok: true,
            json: () => Promise.resolve([
                { id: "c1", name: "Launch plan", kind: "shared", status: "active", member_count: 2, members: [] },
            ]),
        });
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
}

(async () => {
    const s = BossModStore.createStore({
        place: "chat",
        placeParams: {},
        conversationId: null,
        conversationKind: null,
        rosterQuery: "",
        roster: [],
        threads: [],
        needs: [{ id: "n1", kind: "consent", agentId: "a1", title: "Jim wants a folder", sub: "docs/" }],
        runtimePaused: false,
        hasUsableModel: true,
        connection: "connected",
    });
    const b = BossModBus.createBus(BossModBus.KNOWN_TOPICS);

    const headerEl = makeEl("header");
    const rosterEl = makeEl("aside");
    const footerEl = makeEl("footer");
    const noop = () => {};

    const disposers = [
        BossModHeader.mount(headerEl, {
            store: s, apiFetch, navigate: noop, openSettings: noop,
            // Shaped stub: this harness names controls, it never opens the queue.
            needs: {
                refresh: () => Promise.resolve(),
                resolve: () => Promise.resolve(),
                getError: () => "",
                subscribeError: () => () => {},
                destroy: () => {},
            },
        }),
        BossModRoster.mount(rosterEl, { store: s, bus: b, apiFetch, navigate: noop, onHire: noop }),
        BossModFooter.mount(footerEl, { store: s, bus: b }),
    ];
    await drain();

    const walked = {
        header: controls(headerEl, []).length,
        roster: controls(rosterEl, []).length,
        footer: controls(footerEl, []).length,
    };

    // A walk that finds nothing names nothing, and then reports that every
    // control it found was named. That is not a pass, it is a vacuum — and it
    // is what this harness reported while roster.js was failing to paint: the
    // exception went through a core/store.js subscriber, which catches and
    // logs rather than letting one bad subscriber stop the rest, so the rail
    // rendered zero rows and there was nothing left to disagree with.
    //
    // The floors sit under what the shell renders today, so removing one real
    // control does not fail this by itself. The footer is a status bar and
    // renders no controls at all; its floor is zero, and its count is reported
    // so that the day it grows one, the number is on screen rather than nowhere.
    const CONTROL_FLOOR = { header: 8, roster: 6, footer: 0 };
    for (const [region, floor] of Object.entries(CONTROL_FLOOR)) {
        if (walked[region] < floor) {
            throw new Error(
                `[a11y] the ${region} rendered ${walked[region]} controls, fewer than `
                + `the ${floor} it must; the naming check below would pass vacuously`,
            );
        }
    }

    // The sharper half of the same guard. A rail that painted no PEOPLE still
    // renders six controls — the search box, Hire, and the threads block — so a
    // total alone cannot tell "the list is empty" from "the list is short". The
    // fixture is one agent, so a working rail owes exactly one person row, and
    // that is a claim about this fixture rather than about a head count that
    // moves whenever the rail grows a button.
    const personRows = controls(rosterEl, []).filter((control) =>
        String(control.getAttribute("class") || "").split(/\s+/).includes("roster-person"));
    if (personRows.length < 1) {
        throw new Error(
            "[a11y] the rail rendered no person rows for the one agent in the "
            + "fixture; the naming walk below has no people to check",
        );
    }

    const unnamed = [];
    [headerEl, rosterEl, footerEl].forEach((root) => {
        const labelled = labelledIds(root, new Set());
        controls(root, []).forEach((control) => {
            const named = accessibleText(control).trim() !== ""
                || String(control.getAttribute("aria-label") || "").trim() !== ""
                || String(control.getAttribute("aria-labelledby") || "").trim() !== ""
                || labelled.has(control.getAttribute("id"));
            if (!named) unnamed.push(`${control.tagName}.${control.getAttribute("class")}`);
        });
    });
    if (unnamed.length !== 0) {
        throw new Error(`controls with no accessible name: ${unnamed.join(", ")}`);
    }

    disposers.forEach((off) => off());

    process.stdout.write(JSON.stringify({
        ok: true,
        everyShellControlIsNamed: true,
        controlsWalked: walked,
        personRowsWalked: personRows.length,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
