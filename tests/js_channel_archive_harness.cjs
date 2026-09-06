/**
 * Node harness: Archive stays clickable after keep-shell thread switches.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 */
const fs = require("fs");

const byId = new Map();

class FakeEl {
    constructor(tag = "div", attrs = {}) {
        this.tagName = String(tag).toUpperCase();
        this.attrs = { ...attrs };
        this.children = [];
        this.parent = null;
        this.className = attrs.class || "";
        this.id = attrs.id || "";
        this.dataset = { ...(attrs.dataset || {}) };
        this.disabled = false;
        this.onclick = null;
        this.listeners = {};
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.clientHeight = 40;
        this.value = attrs.value || "";
        this.style = {};
        this._text = "";
        this._html = "";
        if (this.id) byId.set(this.id, this);
    }

    getAttribute(name) {
        if (name === "id") return this.id || null;
        if (name === "class") return this.className || null;
        if (name === "data-channel-id") return this.dataset.channelId || null;
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    }

    setAttribute(name, value) {
        this.attrs[name] = String(value);
        if (name === "id") {
            this.id = String(value);
            byId.set(this.id, this);
        }
        if (name === "class") this.className = String(value);
        if (name === "data-channel-id") this.dataset.channelId = String(value);
    }

    get textContent() {
        if (this.children.length) {
            return this.children.map((child) => child.textContent).join("");
        }
        return this._text;
    }

    set textContent(value) {
        this._text = String(value);
        this._html = escapeText(value);
        this.children = [];
    }

    get innerHTML() {
        return this._html;
    }

    set innerHTML(value) {
        this._html = String(value);
        this._text = "";
        for (const child of this.children) detach(child);
        this.children = [];
        hydrate(this, String(value));
    }

    appendChild(child) {
        child.parent = this;
        this.children.push(child);
        if (child.id) byId.set(child.id, child);
        this.scrollHeight = Math.max(this.scrollHeight, this.children.length * 20 + this.clientHeight);
        return child;
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const out = [];
        const visit = (node) => {
            if (matches(node, selector)) out.push(node);
            for (const child of node.children) visit(child);
        };
        for (const child of this.children) visit(child);
        return out;
    }

    addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
    }

    async click() {
        const ev = { preventDefault() {}, key: "", shiftKey: false };
        const fns = [...(this.listeners.click || [])];
        if (typeof this.onclick === "function") fns.push(this.onclick);
        await Promise.all(fns.map((fn) => fn(ev)));
    }
}

function escapeText(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

function detach(node) {
    if (node.id && byId.get(node.id) === node) byId.delete(node.id);
    node.parent = null;
    for (const child of node.children) detach(child);
}

function parseAttrs(raw) {
    const attrs = { dataset: {} };
    const re = /([:@A-Za-z0-9_-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g;
    let match;
    while ((match = re.exec(raw || ""))) {
        const key = match[1];
        const value = match[2] != null ? match[2] : (match[3] != null ? match[3] : "");
        if (key === "id") attrs.id = value;
        else if (key === "class") attrs.class = value;
        else if (key === "data-channel-id") attrs.dataset.channelId = value;
        else attrs[key] = value;
    }
    return attrs;
}

function hydrate(parent, html) {
    const re = /<([A-Za-z0-9]+)([^>]*)>/g;
    let match;
    while ((match = re.exec(html))) {
        const attrs = parseAttrs(match[2]);
        const classes = String(attrs.class || "").split(/\s+/);
        const keep = attrs.id || attrs.dataset.channelId || classes.includes("channels-list-item");
        if (!keep) continue;
        parent.appendChild(new FakeEl(match[1], attrs));
    }
}

function matches(el, selector) {
    if (selector.startsWith("#")) return el.id === selector.slice(1);
    if (selector.startsWith(".")) {
        return String(el.className).split(/\s+/).includes(selector.slice(1));
    }
    if (selector.startsWith("[") && selector.endsWith("]")) {
        const body = selector.slice(1, -1);
        if (body.includes("=")) {
            const eq = body.indexOf("=");
            const key = body.slice(0, eq);
            const want = body.slice(eq + 1).replace(/^['"]|['"]$/g, "");
            return el.getAttribute(key) === want || el.dataset?.[key] === want;
        }
        return el.getAttribute(body) != null || el.dataset?.[body] != null;
    }
    return el.tagName === selector.toUpperCase();
}

const documentStub = {
    createElement(tag) {
        return new FakeEl(tag);
    },
    getElementById(id) {
        return byId.get(id) || null;
    },
    querySelector() {
        return null;
    },
    querySelectorAll() {
        return [];
    },
    addEventListener() {},
};

global.document = documentStub;
global.window = {
    document: documentStub,
    confirm() {
        return true;
    },
};
global.console = console;

const store = [
    thread("a", "Ann"),
    thread("b", "Bea"),
    thread("c", "Cal"),
    thread("d", "Dee"),
];

function thread(id, name) {
    return {
        id,
        name,
        status: "active",
        members: [{ id: `m-${id}`, name, status: "idle" }],
        member_count: 1,
        latest_message: null,
        updated_at: "2026-01-01T00:00:00Z",
    };
}

function activeThreads() {
    return store.filter((item) => item.status === "active").map((item) => ({
        ...item,
        members: item.members.map((member) => ({ ...member })),
    }));
}

global.apiFetch = async (url, opts = {}) => {
    const method = String(opts.method || "GET").toUpperCase();
    if (url === "/api/channels" && method === "GET") {
        return { ok: true, async json() { return activeThreads(); } };
    }
    const match = String(url).match(/^\/api\/channels\/([^/]+)$/);
    if (!match) throw new Error(`unhandled ${method} ${url}`);
    const item = store.find((row) => row.id === match[1]);
    if (!item) return { ok: false, async text() { return "missing"; } };
    if (method === "DELETE") {
        item.status = "archived";
        item.archived_at = "2026-01-01T00:00:00Z";
        return { ok: true, async json() { return { ...item }; } };
    }
    if (method === "GET") {
        return {
            ok: true,
            async json() {
                return { channel: { ...item, members: item.members.map((member) => ({ ...member })) }, messages: [] };
            },
        };
    }
    throw new Error(`unhandled ${method} ${url}`);
};

const [utilsPath, threadDomPath, channelsViewPath] = process.argv.slice(2);
eval(`${fs.readFileSync(utilsPath, "utf8")}\n;global.BossModUtils = BossModUtils;\n`);
eval(`${fs.readFileSync(threadDomPath, "utf8")}\n;global.ChannelThreadDom = ChannelThreadDom;\n`);
eval(`${fs.readFileSync(channelsViewPath, "utf8")}\n;global.ChannelsView = ChannelsView;\n`);

if (!global.ChannelsView || typeof ChannelsView.render !== "function") {
    throw new Error("ChannelsView missing");
}

function archiveBtn() {
    return document.getElementById("channel-archive-btn");
}

function listItem(channelId) {
    const root = byId.get("channels-list");
    const items = root ? root.querySelectorAll(".channels-list-item") : [];
    return items.find((el) => el.dataset.channelId === channelId) || null;
}

function assertEnabled(label) {
    const btn = archiveBtn();
    if (!btn) throw new Error(`${label}: archive button missing`);
    if (btn.disabled) throw new Error(`${label}: archive button stayed disabled`);
}

async function main() {
    const root = new FakeEl("div");
    await ChannelsView.render(root);

    const first = archiveBtn();
    if (!first) throw new Error("initial thread must mount Archive");
    assertEnabled("initial A");
    if (!ChannelThreadDom.isMounted(byId.get("channel-detail"))) {
        throw new Error("thread shell must be mounted");
    }

    await first.click();
    if (archiveBtn() !== first) {
        throw new Error("archive handoff must keep the same shell button");
    }
    assertEnabled("archive A → land on B");

    await first.click();
    if (archiveBtn() !== first) {
        throw new Error("second archive must keep the same shell button");
    }
    assertEnabled("archive B → land on C");

    first.disabled = true;
    const dee = listItem("d");
    if (!dee) throw new Error("thread D missing after archives");
    await dee.click();
    if (archiveBtn() !== first) {
        throw new Error("C→D switch must keep the same shell button");
    }
    assertEnabled("switch C→D");

    first.disabled = true;
    const cal = listItem("c");
    if (!cal) throw new Error("thread C missing after switch");
    await cal.click();
    if (archiveBtn() !== first) {
        throw new Error("D→C switch must keep the same shell button");
    }
    assertEnabled("switch D→C");

    process.stdout.write(JSON.stringify({
        ok: true,
        archiveHandoffEnabled: true,
        keepShellSwitchEnabled: true,
        sameButton: true,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
