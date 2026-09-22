/**
 * Node harness: Settings → AI Connections renders System AI as the first
 * control under the heading. An unset or missing pick shows the first
 * connection. Rendering does not write the stored pick.
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
const world = {
    settings: [],
    connections: [],
    connectionsOk: true,
    settingsOk: true,
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
    return { ok: true };
};

global.showRowError = () => {};

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.ConnectionsSection = ConnectionsSection;\n`);

function setting(key, value) {
    return { key, value, category: "llm", updated_at: "2026-01-01T00:00:00Z" };
}

async function settle() {
    for (let i = 0; i < 8; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

async function dispatchChange(control) {
    const event = { target: control };
    for (const fn of [...(control.listeners.change || [])]) await fn(event);
}

function capture(root, paintSaves) {
    const block = root.querySelector("[data-system-ai]");
    const heading = root.querySelector("h2");
    const control = block ? block.querySelector("select") : null;
    const add = root.querySelector("#btn-add-connection");
    const controls = root.querySelectorAll("button, select, input, textarea").map((el) => ({
        tag: el.tagName,
        id: el.id || "",
        key: el.dataset.settingKey || "",
    }));
    const options = control
        ? control.querySelectorAll("option").map((option) => ({
            value: option.getAttribute("value"),
            label: option.textContent.trim(),
            title: option.getAttribute("title"),
            selected: option.hasAttribute("selected"),
        }))
        : null;
    return {
        heading: heading ? heading.textContent.trim() : "",
        underHeading: Boolean(heading && block && heading.parent === block.parent),
        directlyUnderHeading: Boolean(
            heading
            && block
            && heading.parent
            && heading.parent.children.filter((node) => node.nodeType === 1)[1] === block
        ),
        label: block && block.querySelector("label") ? block.querySelector("label").textContent.trim() : "",
        paragraphs: block ? block.querySelectorAll("p").map((node) => node.textContent.trim()) : [],
        pageText: root.textContent,
        controls,
        value: control ? control.value : null,
        disabled: control ? control.hasAttribute("disabled") : null,
        options,
        saves: paintSaves,
    };
}

async function paint() {
    const before = saves.length;
    document.body.replaceChildren();
    const el = new FakeEl("div");
    document.body.append(el);
    await ConnectionsSection.render(el);
    await settle();
    return { el, view: capture(el, saves.slice(before)) };
}

async function main() {
    const plain = { id: "conn-plain", name: "Local", model: "mock-small", api_base_url: "http://127.0.0.1:9" };
    const quoted = { id: "conn-quote", name: 'Bob "fast" <Local>', model: "gpt-4", api_base_url: "http://127.0.0.1:9" };
    world.connections = [plain, quoted];
    world.settings = [setting("system_ai_connection", "")];

    const unsetPaint = await paint();
    const unset = unsetPaint.view;
    const select = unsetPaint.el.querySelector("select");
    select.value = "conn-quote";
    const savesBeforeChange = saves.length;
    await dispatchChange(select);
    const swapped = saves.slice(savesBeforeChange);

    world.settings = [setting("system_ai_connection", "conn-quote")];
    const kept = (await paint()).view;

    world.settings = [setting("system_ai_connection", "stale-id")];
    const gone = (await paint()).view;

    world.connectionsOk = false;
    world.settings = [setting("system_ai_connection", "conn-quote")];
    const failedLoad = (await paint()).view;

    world.connectionsOk = true;
    world.settingsOk = false;
    const settingsFailed = (await paint()).view;

    world.settingsOk = true;
    world.connections = [];
    world.settings = [setting("system_ai_connection", "")];
    const emptyList = (await paint()).view;

    world.settings = [setting("system_ai_connection", "stale-id")];
    const emptyListKept = (await paint()).view;

    process.stdout.write(JSON.stringify({
        ok: true,
        fetches,
        unset,
        swapped,
        kept,
        gone,
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
