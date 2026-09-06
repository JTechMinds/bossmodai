/**
 * Node harness: thread cache + in-place chrome/transcript swap.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
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
        this.dataset = { ...(attrs.dataset || {}) };
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

    replaceChildren(...nodes) {
        this.children = [];
        this._html = "";
        this._text = "";
        for (const node of nodes) this.appendChild(node);
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

global.document = {
    createElement(tag) {
        return new FakeEl(tag);
    },
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.ChannelThreadDom = ChannelThreadDom;\n`);

const ChannelThreadDom = global.ChannelThreadDom;
if (!ChannelThreadDom || typeof ChannelThreadDom.createCache !== "function") {
    throw new Error("ChannelThreadDom missing");
}

const empty = new FakeEl("div");
if (ChannelThreadDom.isMounted(empty) !== false) {
    throw new Error("empty pane should not look mounted");
}

const detail = new FakeEl("div");
const title = new FakeEl("h3", { id: "channel-title" });
const count = new FakeEl("p", { id: "channel-member-count" });
const members = new FakeEl("div", { id: "channel-members" });
const messages = new FakeEl("div", { id: "channel-messages" });
const input = new FakeEl("textarea", { id: "channel-input" });
title.textContent = "Debrah, Jim";
count.textContent = "2 participants";
input.value = "draft for A";
detail.appendChild(title);
detail.appendChild(count);
detail.appendChild(members);
detail.appendChild(messages);
detail.appendChild(input);
detail.dataset.channelId = "a";

if (ChannelThreadDom.isMounted(detail) !== true) {
    throw new Error("shell with messages + input should be mounted");
}

const cache = ChannelThreadDom.createCache();
const threadA = {
    channel: { id: "a", name: "Debrah, Jim", members: [{ id: "1", name: "Debrah" }] },
    messages: [{ id: "m1", author_name: "You", content: "hello A" }],
};
const threadB = {
    channel: { id: "b", name: "Joey", members: [{ id: "2", name: "Joey" }, { id: "3", name: "Jim" }] },
    messages: [{ id: "m2", author_name: "You", content: "hello B" }],
};
cache.remember("a", threadA);
cache.remember("b", threadB);
if (cache.recall("a").messages[0].content !== "hello A") {
    throw new Error("cache must recall last-loaded messages");
}
if (cache.append("a", { id: "m1", content: "dup" }) !== false) {
    throw new Error("cache append must dedup");
}
if (cache.append("a", { id: "m3", content: "later" }) !== true) {
    throw new Error("cache append must accept a new message");
}
if (cache.recall("a").messages.length !== 2) {
    throw new Error("live append should land in the cached transcript");
}

ChannelThreadDom.updateChrome(detail, threadB.channel);
if (detail.dataset.channelId !== "b" || title.textContent !== "Joey") {
    throw new Error("switch must update chrome in place");
}
if (count.textContent !== "2 participants") {
    throw new Error("member count should follow the new roster");
}
if (input.value !== "draft for A") {
    throw new Error("chrome update must not wipe the composer");
}

ChannelThreadDom.replaceTranscript(messages);
if (messages.children.length !== 0) {
    throw new Error("switch must clear the prior transcript");
}
ChannelThreadDom.showEmptyTranscript(messages);
if (!String(messages.innerHTML).includes("No thread messages yet.")) {
    throw new Error("uncached switch should show an empty transcript, not a loading pane");
}

const recalled = cache.recall("a");
if (recalled.messages.map((item) => item.id).join(",") !== "m1,m3") {
    throw new Error("re-click must still have the cached A transcript");
}
if (cache.forget("b") !== true || cache.recall("b") !== null) {
    throw new Error("archive must drop the cached thread");
}

process.stdout.write(JSON.stringify({
    ok: true,
    keepsShell: ChannelThreadDom.isMounted(detail),
    cachesTranscript: true,
    swapsChrome: title.textContent === "Joey",
    noLoadingFlash: !String(messages.innerHTML).includes("Loading"),
}));
