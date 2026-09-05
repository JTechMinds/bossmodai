/**
 * Node harness for meeting incremental DOM updates.
 * Invoked by tests/test_meeting_ui_incremental.py. Not a browser bundle.
 */
const fs = require("fs");

class FakeEl {
    constructor(tag = "div", attrs = {}) {
        this.tagName = String(tag).toUpperCase();
        this.attrs = { ...attrs };
        this.children = [];
        this.parent = null;
        this.className = attrs.class || "";
        this.id = attrs.id || "";
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.clientHeight = 40;
        this._text = "";
        this._html = "";
        this.value = attrs.value || "";
        this.style = {};
    }

    getAttribute(name) {
        if (name === "id") return this.id || null;
        if (name === "class") return this.className || null;
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    }

    setAttribute(name, value) {
        this.attrs[name] = String(value);
        if (name === "id") this.id = String(value);
        if (name === "class") this.className = String(value);
    }

    get textContent() {
        if (this.children.length) {
            return this.children.map((child) => child.textContent).join("");
        }
        return this._text;
    }

    set textContent(value) {
        this._text = String(value);
        this._html = "";
        this.children = [];
    }

    get innerText() {
        return this.textContent;
    }

    set innerText(value) {
        this.textContent = value;
    }

    get innerHTML() {
        return this._html;
    }

    set innerHTML(value) {
        this._html = String(value);
        this.children = [];
        this._text = "";
    }

    appendChild(child) {
        child.parent = this;
        this.children.push(child);
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

    remove() {
        if (!this.parent) return;
        this.parent.children = this.parent.children.filter((child) => child !== this);
        this.parent = null;
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
            return el.getAttribute(key) === want;
        }
        return el.getAttribute(body) != null;
    }
    return el.tagName === selector.toUpperCase();
}

const documentStub = {
    createElement(tag) {
        return new FakeEl(tag);
    },
    getElementById() {
        return null;
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
global.window = { lucide: null, document: documentStub };
global.BossModUtils = {
    escapeHtml(text) {
        return String(text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    },
    createLoadGeneration() {
        let current = 0;
        return {
            next() {
                current += 1;
                return current;
            },
            isCurrent(id) {
                return id === current;
            },
        };
    },
};

// eslint-disable-next-line no-eval
eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.MeetingSessionDom = MeetingSessionDom;\n`);

const MeetingSessionDom = global.MeetingSessionDom;
if (typeof MeetingSessionDom !== "object" || MeetingSessionDom === null) {
    throw new Error("MeetingSessionDom missing");
}

function mountSession() {
    const container = new FakeEl("div", { id: "subview-meeting" });
    const messages = new FakeEl("div", { id: "meeting-messages" });
    const input = new FakeEl("textarea", { id: "meeting-input" });
    const title = new FakeEl("h3", { id: "meeting-title" });
    const room = new FakeEl("p", { id: "meeting-room" });
    const participants = new FakeEl("div", { id: "meeting-participants" });
    title.textContent = "Standup";
    room.textContent = "Meeting Room";
    input.value = "draft still here";
    messages.scrollTop = 12;
    messages.scrollHeight = 80;
    messages.clientHeight = 40;
    container.appendChild(title);
    container.appendChild(room);
    container.appendChild(participants);
    container.appendChild(messages);
    container.appendChild(input);
    return { container, messages, input, title, room, participants };
}

const empty = new FakeEl("div");
if (MeetingSessionDom.isMounted(empty) !== false) {
    throw new Error("empty container should not look mounted");
}

const mounted = mountSession();
if (MeetingSessionDom.isMounted(mounted.container) !== true) {
    throw new Error("session with messages + input should be mounted");
}

if (MeetingSessionDom.shouldReloadOnWorldUpdate("meeting", "meeting") !== false) {
    throw new Error("same activity kind must not reload");
}
if (MeetingSessionDom.shouldReloadOnWorldUpdate("idle", "meeting") !== true) {
    throw new Error("entering meeting must reload");
}
if (MeetingSessionDom.shouldReloadOnWorldUpdate("meeting", "work") !== true) {
    throw new Error("leaving meeting must reload");
}

const first = MeetingSessionDom.appendMessage(mounted.messages, {
    id: "m1",
    author_type: "agent",
    author_name: "Ada",
    content: "hello",
});
const dup = MeetingSessionDom.appendMessage(mounted.messages, {
    id: "m1",
    author_type: "agent",
    author_name: "Ada",
    content: "hello again",
});
if (first !== true || dup !== false) {
    throw new Error("append should add once and dedup by id");
}
if (mounted.messages.children.length !== 1) {
    throw new Error(`expected 1 message node, got ${mounted.messages.children.length}`);
}
if (mounted.input.value !== "draft still here") {
    throw new Error("append must not touch the composer");
}
if (mounted.messages.scrollTop !== mounted.messages.scrollHeight) {
    throw new Error("new message should stick to bottom");
}

mounted.messages.scrollTop = 8;
MeetingSessionDom.updateSessionChrome(mounted.container, {
    title: "Retro",
    room_name: "Board Room",
    participants: [{ name: "Ada" }, { name: "Grace" }],
});
MeetingSessionDom.syncMessages(mounted.messages, [
    { id: "m1", author_name: "Ada", content: "hello" },
    { id: "m2", author_name: "Grace", content: "ack" },
]);
if (mounted.title.textContent !== "Retro") {
    throw new Error("title chrome should update in place");
}
if (mounted.room.textContent !== "Board Room") {
    throw new Error("room chrome should update in place");
}
if (!String(mounted.participants.innerHTML).includes("Ada") || !String(mounted.participants.innerHTML).includes("Grace")) {
    throw new Error("participant chips should update in place");
}
if (mounted.messages.children.length !== 2) {
    throw new Error("sync should append only the new message");
}
if (mounted.input.value !== "draft still here") {
    throw new Error("in-place sync must preserve typed draft");
}
if (mounted.messages.scrollTop !== 8) {
    throw new Error("sync while away from bottom should keep the prior scroll");
}

process.stdout.write(JSON.stringify({
    ok: true,
    mounted: true,
    preservedDraft: mounted.input.value === "draft still here",
    messageCount: mounted.messages.children.length,
}));
