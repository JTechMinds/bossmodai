/**
 * Node harness: archive prompt branches for threads with open tasks.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 */
const fs = require("fs");

const byId = new Map();
const calls = [];

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
    body: new FakeEl("body"),
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
    removeEventListener() {},
};

global.document = documentStub;
global.window = {
    document: documentStub,
    confirm() {
        throw new Error("window.confirm must not be used for open-task archive");
    },
};
global.console = console;

const store = [
    thread("open-a", "Ann", 2),
    thread("open-b", "Bea", 2),
    thread("none-c", "Cal", 0),
    thread("open-d", "Dee", 2),
];

function thread(id, name, openCount) {
    return {
        id,
        name,
        status: "active",
        openCount,
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

function openTasks(item) {
    const tasks = [];
    for (let i = 0; i < item.openCount; i += 1) {
        tasks.push({ id: `${item.id}-task-${i + 1}`, status: "pending", title: `Task ${i + 1}` });
    }
    return { count: tasks.length, tasks };
}

global.apiFetch = async (url, opts = {}) => {
    const method = String(opts.method || "GET").toUpperCase();
    calls.push({ method, url, body: opts.body || null });
    if (url === "/api/channels" && method === "GET") {
        return { ok: true, async json() { return activeThreads(); } };
    }
    const openMatch = String(url).match(/^\/api\/channels\/([^/]+)\/open-tasks$/);
    if (openMatch && method === "GET") {
        const item = store.find((row) => row.id === openMatch[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        return { ok: true, async json() { return openTasks(item); } };
    }
    if (url === "/api/tasks/cancel" && method === "POST") {
        return { ok: true, async json() { return []; } };
    }
    const archivePost = String(url).match(/^\/api\/channels\/([^/?]+)\/archive/);
    if (archivePost && method === "POST") {
        const item = store.find((row) => row.id === archivePost[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        item.status = "archived";
        item.archived_at = "2026-01-01T00:00:00Z";
        return { ok: true, async json() { return { ...item }; } };
    }
    const match = String(url).match(/^\/api\/channels\/([^/?]+)$/);
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

function methodsFor(urlPart) {
    return calls.filter((item) => String(item.url).includes(urlPart)).map((item) => item.method);
}

function buttonLabels(spec) {
    return spec.buttons.map((btn) => btn.label);
}

function countMarkupButtons(html) {
    return (String(html).match(/<button\b/g) || []).length;
}

async function main() {
    const emptySpec = ChannelsView.archivePromptSpec(0);
    const openSpec = ChannelsView.archivePromptSpec(2);
    const emptyMarkup = ChannelsView.archivePromptMarkup(emptySpec);
    const openMarkup = ChannelsView.archivePromptMarkup(openSpec);

    if (emptySpec.title !== "Archive thread?" || openSpec.title !== "Archive thread?") {
        throw new Error("archive title mismatch");
    }
    if (ChannelsView.openTaskArchiveCopy(0) !== "Hides it from the active list and seals the room — no new messages or access cards. Open tasks stay on the board.") {
        throw new Error("N=0 archive copy mismatch");
    }
    if (ChannelsView.openTaskArchiveCopy(2) !== "This thread has 2 open tasks. Sealing stops new posts and access cards.") {
        throw new Error("N>0 archive copy mismatch");
    }
    if (ChannelsView.shouldPromptOpenTasksOnArchive(0) !== false) {
        throw new Error("N=0 must use the two-button confirm, not the open-task choices");
    }
    if (ChannelsView.shouldPromptOpenTasksOnArchive(2) !== true) {
        throw new Error("N>0 must show the open-task choices");
    }
    if (emptySpec.buttons.length !== 2 || countMarkupButtons(emptyMarkup) !== 2) {
        throw new Error("N=0 must show a two-button modal");
    }
    if (buttonLabels(emptySpec).join("|") !== "Cancel|Archive") {
        throw new Error("N=0 buttons must be Cancel and Archive");
    }
    if (!emptyMarkup.includes('id="channel-archive-confirm"') || !emptyMarkup.includes('id="channel-archive-back"')) {
        throw new Error("N=0 markup must include Archive and Cancel");
    }
    if (emptyMarkup.includes("Cancel tasks") || emptyMarkup.includes("Archive only")) {
        throw new Error("N=0 modal must not include open-task actions");
    }
    if (openSpec.buttons.length !== 3 || countMarkupButtons(openMarkup) !== 3) {
        throw new Error("N>0 must show a three-button modal");
    }
    if (buttonLabels(openSpec).join("|") !== "Back|Archive only|Cancel tasks & archive") {
        throw new Error("N>0 buttons mismatch");
    }
    if (!openMarkup.includes('id="channel-archive-cancel-tasks"') || !openMarkup.includes('id="channel-archive-only"')) {
        throw new Error("N>0 markup must include cancel-and-archive and archive-only");
    }

    const root = new FakeEl("div");
    await ChannelsView.render(root);

    window.chooseArchiveOpenTasks = () => "cancel_and_archive";
    calls.length = 0;
    await archiveBtn().click();
    if (calls.some((item) => item.url === "/api/tasks/cancel")) {
        throw new Error("cancel-and-archive must not use a separate cancel POST");
    }
    if (!calls.some((item) => item.method === "POST" && String(item.url).includes("/api/channels/open-a/archive") && String(item.url).includes("cancel_open_tasks=true"))) {
        throw new Error("primary must cancel tasks via archive?cancel_open_tasks=true");
    }

    window.chooseArchiveOpenTasks = () => "archive_only";
    calls.length = 0;
    const bea = listItem("open-b");
    if (!bea) throw new Error("thread B missing");
    await bea.click();
    await archiveBtn().click();
    if (calls.some((item) => item.url === "/api/tasks/cancel")) {
        throw new Error("archive only must leave tasks open");
    }
    if (!calls.some((item) => item.method === "DELETE" && item.url === "/api/channels/open-b")) {
        throw new Error("archive only must still archive the thread");
    }

    const cal = listItem("none-c");
    if (!cal) throw new Error("thread C missing");
    await cal.click();
    window.chooseArchiveOpenTasks = () => "back";
    calls.length = 0;
    await archiveBtn().click();
    if (calls.some((item) => item.url === "/api/tasks/cancel") || methodsFor("/api/channels/none-c").includes("DELETE")) {
        throw new Error("N=0 Cancel must abort archive");
    }
    if (archiveBtn().disabled) {
        throw new Error("N=0 Cancel must re-enable Archive");
    }

    window.chooseArchiveOpenTasks = () => "archive_only";
    calls.length = 0;
    await archiveBtn().click();
    if (calls.some((item) => item.url === "/api/tasks/cancel")) {
        throw new Error("N=0 archive must not cancel tasks");
    }
    if (!calls.some((item) => item.method === "DELETE" && item.url === "/api/channels/none-c")) {
        throw new Error("N=0 confirm must archive after the modal");
    }

    window.chooseArchiveOpenTasks = () => "back";
    calls.length = 0;
    const dee = listItem("open-d");
    if (!dee) throw new Error("thread D missing");
    await dee.click();
    await archiveBtn().click();
    if (calls.some((item) => item.url === "/api/tasks/cancel") || methodsFor("/api/channels/open-d").includes("DELETE")) {
        throw new Error("Back must abort archive");
    }
    if (archiveBtn().disabled) {
        throw new Error("Back must re-enable Archive");
    }

    if (ChannelsView.isLiveThread("open-a") !== false || ChannelsView.isLiveThread("open-b") !== false) {
        throw new Error("archived threads must not be live");
    }
    if (ChannelsView.isLiveThread("open-d") !== true) {
        throw new Error("open thread must stay live");
    }
    ChannelsView.handleChannelMessage({
        channel_id: "open-a",
        content: "spam after archive",
        author_type: "agent",
        author_name: "Ada",
        message_id: "spam-1",
    });
    ChannelsView.handleChannelPresence({
        channel_id: "open-a",
        agent_id: "ada",
        agent_name: "Ada",
        phase: "thinking",
    });
    if (ChannelsView.isLiveThread("open-a") !== false) {
        throw new Error("live handlers must not revive an archived thread");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        cancelAndArchive: true,
        archiveOnly: true,
        zeroOpenTwoButtons: true,
        openTasksThreeButtons: true,
        zeroOpenConfirm: true,
        zeroOpenCancelAborts: true,
        backAborts: true,
        archivedNotLive: true,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
