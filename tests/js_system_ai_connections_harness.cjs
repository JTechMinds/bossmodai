/**
 * Node harness: Settings → AI Connections renders the page description under
 * the heading, then System AI as one row of two toolbar dropdowns — the
 * connection and its thinking level — over one hint line. An unset or missing
 * pick shows the first connection. Rendering does not write the stored pick.
 * The thinking dropdown offers the effective connection's levels, saves a
 * level change, and resets an orphaned level to `default` before a
 * connection switch is saved; refusals put the control back.
 * Invoked by tests/test_system_ai_compaction_settings.py. Not a browser bundle.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

installDom();
installIconsStub();

const VOID = new Set(["INPUT", "BR", "HR", "IMG"]);

function decode(text) {
    return String(text).replace(/&(?:amp|lt|gt|quot);/g, (entity) => ({
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
    }[entity]));
}

function parseAttrs(raw) {
    const attrs = {};
    const re = /([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g;
    let match;
    while ((match = re.exec(raw))) {
        if (match[2] !== undefined) attrs[match[1]] = decode(match[2]);
        else if (match[3] !== undefined) attrs[match[1]] = decode(match[3]);
        else attrs[match[1]] = "";
    }
    return attrs;
}

function parseFragment(html) {
    const root = new FakeEl("div");
    const stack = [root];
    const re = /<!--[\s\S]*?-->|<\/([A-Za-z][A-Za-z0-9]*)\s*>|<([A-Za-z][A-Za-z0-9]*)\b([^>]*)>/g;
    let last = 0;
    let match;
    while ((match = re.exec(html))) {
        const text = html.slice(last, match.index);
        if (text) stack[stack.length - 1].append(decode(text));
        last = re.lastIndex;
        if (match[0].startsWith("<!--")) continue;
        if (match[1]) {
            const tag = match[1].toUpperCase();
            while (stack.length > 1 && stack[stack.length - 1].tagName !== tag) stack.pop();
            if (stack.length > 1) stack.pop();
            continue;
        }
        const el = new FakeEl(match[2]);
        for (const [name, value] of Object.entries(parseAttrs(match[3] || ""))) {
            el.setAttribute(name, value);
        }
        if (el.tagName === "INPUT") el.value = el.getAttribute("value") || "";
        stack[stack.length - 1].append(el);
        const selfClosed = /\/\s*$/.test(match[3] || "");
        if (!VOID.has(el.tagName) && !selfClosed) stack.push(el);
    }
    const tail = html.slice(last);
    if (tail) stack[stack.length - 1].append(decode(tail));
    for (const select of root.querySelectorAll("select")) {
        const options = select.querySelectorAll("option");
        const selected = options.find((option) => option.hasAttribute("selected")) || options[0];
        select.value = selected ? (selected.getAttribute("value") ?? "") : "";
    }
    return root.children;
}

Object.defineProperty(FakeEl.prototype, "innerHTML", {
    configurable: true,
    get() {
        if (this.children.length) return "";
        return String(this._text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    },
    set(value) {
        this.replaceChildren(...parseFragment(String(value)));
    },
});

const fetches = [];
const saves = [];
const rowErrors = [];
const world = {
    settings: [],
    connections: [],
    connectionsOk: true,
    settingsOk: true,
    // Setting key → the detail a PUT of it is refused with.
    refuse: {},
};

global.apiFetch = async (url) => {
    fetches.push(url);
    if (url === "/api/settings") {
        if (!world.settingsOk) return { ok: false, status: 500, json: async () => ({}) };
        return { ok: true, status: 200, json: async () => world.settings };
    }
    if (url === "/api/connections") {
        if (!world.connectionsOk) return { ok: false, status: 500, json: async () => ({}) };
        return { ok: true, status: 200, json: async () => world.connections };
    }
    throw new Error(`unexpected fetch ${url}`);
};

global.apiFetchOk = async (url, init) => {
    saves.push({ url, method: init && init.method });
    const key = decodeURIComponent(url.split("?")[0].split("/").pop());
    if (world.refuse[key]) throw new Error(world.refuse[key]);
    return { ok: true };
};

global.showRowError = (_container, message) => { rowErrors.push(message); };
global.BossModOperatorInvalidate = { notifyLocal() {} };
global.BossModAgentStatus = { AGENT_COLOR_PALETTE: [] };

// A recording stand-in for core/menu-select.js with the same contract the
// module relies on: a trigger button named by the label, a value that must be
// one of the options, and setOptions. The real control is proved in
// js_toolbar_controls_harness.cjs.
const menuSelects = [];
global.BossModMenuSelect = {
    create(deps) {
        const variant = deps.variant === undefined ? "button" : deps.variant;
        let list = deps.options.map((option) => ({ value: option.value, label: option.label }));
        let current = deps.value;
        const check = () => {
            if (!list.some((option) => option.value === current)) throw new Error(`"${current}" is not an option`);
        };
        check();
        const element = new FakeEl("span");
        const trigger = new FakeEl("button");
        trigger.setAttribute("aria-label", deps.label);
        element.append(trigger);
        const api = {
            element,
            label: deps.label,
            variant,
            getValue: () => current,
            options: () => list.map((option) => ({ ...option })),
            setOptions(next, value) {
                list = next.map((option) => ({ value: option.value, label: option.label }));
                if (value !== undefined) current = value;
                check();
            },
            async pick(value) {
                current = value;
                await deps.onChange(value);
            },
        };
        menuSelects.push(api);
        return api;
    },
};

/** The stand-in created with `label`, or null. */
function menuSelect(label) {
    return menuSelects.find((select) => select.label === label) || null;
}

// settings-system.js registers a repaint hook on render; the harness drives
// renders directly, so the hook is a no-op.
global.SettingsView = { bindRepaint() {} };

const NAMES = ["BossModFormat", "BossModAgentFields", "BossModSystemAi", "ConnectionsSection"];
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

function setting(key, value) {
    return { key, value, category: "llm", updated_at: "2026-01-01T00:00:00Z" };
}

async function settle() {
    for (let i = 0; i < 8; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

function elements(node) {
    return node.children.filter((child) => child.nodeType === 1);
}

function describe(select) {
    return select
        ? { label: select.label, variant: select.variant, value: select.getValue(), options: select.options() }
        : null;
}

function capture(root, paintSaves) {
    const block = root.querySelector("[data-system-ai]");
    const heading = root.querySelector("h2");
    const siblings = heading && heading.parent ? elements(heading.parent) : [];
    const row = block ? block.querySelector("[data-system-ai-row]") : null;
    const controls = root.querySelectorAll("button, select, input, textarea").map((el) => ({
        tag: el.tagName,
        id: el.id || "",
        label: el.getAttribute("aria-label") || "",
    }));
    return {
        heading: heading ? heading.textContent.trim() : "",
        // h2, then the description, then the System AI block, in one column.
        order: siblings.map((node) => (
            node === block ? "system-ai" : `${node.tagName.toLowerCase()}:${node.textContent.trim()}`
        )),
        label: row ? elements(row)[0].textContent.trim() : "",
        nativeSelects: block ? block.querySelectorAll("select").length : null,
        menuSelects: menuSelects.map((select) => select.label),
        connection: describe(menuSelect("System AI connection")),
        thinking: describe(menuSelect("System AI thinking")),
        rowText: row ? row.textContent.replace(/\s+/g, " ").trim() : "",
        paragraphs: block ? block.querySelectorAll("p").map((node) => node.textContent.trim()) : [],
        pageText: root.textContent,
        controls,
        saves: paintSaves,
    };
}

async function paint() {
    const before = saves.length;
    menuSelects.length = 0;
    document.body.replaceChildren();
    const el = new FakeEl("div");
    document.body.append(el);
    await ConnectionsSection.render(el);
    await settle();
    return { el, view: capture(el, saves.slice(before)) };
}

function systemAi(id, thinking = "default") {
    return [setting("system_ai_connection", id), setting("system_ai_thinking", thinking)];
}

async function main() {
    const plain = {
        id: "conn-plain", name: "Local", model: "mock-small", api_base_url: "http://127.0.0.1:9",
        thinking_levels: { off: { reasoning_effort: "none" }, low: { reasoning_effort: "low" } },
    };
    const quoted = {
        id: "conn-quote", name: 'Bob "fast" <Local>', model: "gpt-4", api_base_url: "http://127.0.0.1:9",
        thinking_levels: { high: { reasoning_effort: "high" } },
    };
    world.connections = [plain, quoted];
    world.settings = systemAi("");

    const unset = (await paint()).view;
    let before = saves.length;
    await menuSelect("System AI connection").pick("conn-quote");
    const swapped = saves.slice(before);
    const swappedThinking = menuSelect("System AI thinking").options();

    world.settings = systemAi("conn-quote");
    const kept = (await paint()).view;

    world.settings = systemAi("stale-id");
    const gone = (await paint()).view;

    // A level change saves the setting, and a refused one goes back with the
    // server's detail shown.
    world.settings = systemAi("");
    await paint();
    const level = menuSelect("System AI thinking");
    before = saves.length;
    await level.pick("low");
    const levelSaved = { saves: saves.slice(before), value: level.getValue() };
    world.refuse.system_ai_thinking = "System AI connection 'Local' does not offer thinking level 'off'.";
    before = saves.length;
    let errorsBefore = rowErrors.length;
    await level.pick("off");
    const levelRefused = {
        saves: saves.slice(before),
        value: level.getValue(),
        errors: rowErrors.slice(errorsBefore),
    };
    delete world.refuse.system_ai_thinking;

    // Switching to a connection that lacks the stored level resets it first.
    world.settings = systemAi("", "low");
    await paint();
    before = saves.length;
    await menuSelect("System AI connection").pick("conn-quote");
    const orphanSwitch = {
        saves: saves.slice(before),
        value: menuSelect("System AI thinking").getValue(),
        options: menuSelect("System AI thinking").options(),
    };

    // A refused reset saves no connection and puts the connection back.
    world.settings = systemAi("", "low");
    world.refuse.system_ai_thinking = "nope: reset refused";
    await paint();
    before = saves.length;
    errorsBefore = rowErrors.length;
    await menuSelect("System AI connection").pick("conn-quote");
    const resetRefused = {
        saves: saves.slice(before),
        connection: menuSelect("System AI connection").getValue(),
        value: menuSelect("System AI thinking").getValue(),
        errors: rowErrors.slice(errorsBefore),
    };
    delete world.refuse.system_ai_thinking;

    // A refused connection save puts the connection back and keeps the levels.
    world.settings = systemAi("");
    world.refuse.system_ai_connection = "nope: connection refused";
    await paint();
    before = saves.length;
    errorsBefore = rowErrors.length;
    await menuSelect("System AI connection").pick("conn-quote");
    const connectionRefused = {
        saves: saves.slice(before),
        connection: menuSelect("System AI connection").getValue(),
        options: menuSelect("System AI thinking").options(),
        errors: rowErrors.slice(errorsBefore),
    };
    delete world.refuse.system_ai_connection;

    // A stored level the fallback connection lacks is shown, marked.
    world.settings = systemAi("stale-id", "high");
    const unofferedStored = (await paint()).view;

    world.settings = [setting("system_ai_connection", "")];
    const thinkingMissing = (await paint()).view;

    world.connectionsOk = false;
    world.settings = systemAi("conn-quote");
    const failedLoad = (await paint()).view;

    world.connectionsOk = true;
    world.settingsOk = false;
    const settingsFailed = (await paint()).view;

    world.settingsOk = true;
    world.connections = [];
    world.settings = systemAi("");
    const emptyList = (await paint()).view;

    world.settings = systemAi("stale-id");
    const emptyListKept = (await paint()).view;

    process.stdout.write(JSON.stringify({
        ok: true,
        fetches,
        unset,
        swapped,
        swappedThinking,
        kept,
        gone,
        levelSaved,
        levelRefused,
        orphanSwitch,
        resetRefused,
        connectionRefused,
        unofferedStored,
        thinkingMissing,
        failedLoad,
        settingsFailed,
        emptyList,
        emptyListKept,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
