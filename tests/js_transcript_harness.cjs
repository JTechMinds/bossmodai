/**
 * Node harness: transcript dedupe, scroll anchoring, presence, and cache.
 * Invoked by tests/test_ui_conversation.py. Not a browser bundle.
 */
const fs = require("fs");

class FakeEl {
    constructor(tag = "div") {
        this.tagName = String(tag).toUpperCase();
        this.nodeType = 1;
        this.attrs = {};
        this.children = [];
        this.parent = null;
        this.listeners = {};
        this.hidden = false;
        this.disabled = false;
        this.value = "";
        this.style = {};
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.clientHeight = 0;
        this._text = "";
    }

    get id() {
        return this.attrs.id || "";
    }

    get className() {
        return this.attrs.class || "";
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    }

    setAttribute(name, value) {
        this.attrs[name] = String(value);
    }

    hasAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attrs, name);
    }

    get textContent() {
        if (this.children.length) {
            return this.children.map((child) => child.textContent).join("");
        }
        return this._text;
    }

    set textContent(value) {
        this._text = String(value);
        this.children = [];
    }

    append(...nodes) {
        for (const node of nodes) {
            // Real DOM semantics: appending an attached node MOVES it.
            if (node.parent) {
                node.parent.children = node.parent.children.filter((child) => child !== node);
            }
            node.parent = this;
            this.children.push(node);
        }
    }

    appendChild(node) {
        this.append(node);
        return node;
    }

    replaceChildren(...nodes) {
        for (const child of this.children) child.parent = null;
        this.children = [];
        this._text = "";
        this.append(...nodes);
    }

    remove() {
        if (!this.parent) return;
        this.parent.children = this.parent.children.filter((child) => child !== this);
        this.parent = null;
    }

    replaceWith(node) {
        if (!this.parent) return;
        const kids = this.parent.children;
        const idx = kids.indexOf(this);
        if (node.parent) {
            node.parent.children = node.parent.children.filter((child) => child !== node);
        }
        node.parent = this.parent;
        kids[idx] = node;
        this.parent = null;
    }

    addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
    }

    removeEventListener(type, fn) {
        this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== fn);
    }

    click() {
        for (const fn of this.listeners.click || []) fn({ preventDefault() {} });
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const out = [];
        const visit = (node) => {
            if (node.nodeType !== 1) return;
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
            return el.getAttribute(key) === want;
        }
        return el.hasAttribute(body);
    }
    return el.tagName === selector.toUpperCase();
}

const documentStub = {
    createElement(tag) {
        return new FakeEl(tag);
    },
    createTextNode(text) {
        return { nodeType: 3, textContent: String(text), parent: null };
    },
    body: new FakeEl("body"),
    getElementById() {
        return null;
    },
    addEventListener() {},
    removeEventListener() {},
};

global.document = documentStub;
global.window = { document: documentStub };

const [
    domPath, avatarPath, gatesPath, formatPath, emptyStatePath, transcriptPath, cachePath,
] = process.argv.slice(2);
eval(`${fs.readFileSync(domPath, "utf8")}\n;global.BossModDom = BossModDom;\n`);
// The empty state is the one status with a face and controls; it lives in its
// own module and the transcript delegates to it.
eval(`${fs.readFileSync(avatarPath, "utf8")}\n;global.BossModAvatar = BossModAvatar;\n`);
eval(`${fs.readFileSync(gatesPath, "utf8")}\n;global.BossModGates = BossModGates;\n`);
// The presence row's duration is formatted by the shared formatter, not a
// second opinion local to the transcript.
eval(`${fs.readFileSync(formatPath, "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(emptyStatePath, "utf8")}\n;global.BossModEmptyState = BossModEmptyState;\n`);
eval(`${fs.readFileSync(transcriptPath, "utf8")}\n;global.BossModTranscript = BossModTranscript;\n`);
eval(`${fs.readFileSync(cachePath, "utf8")}\n;global.BossModTranscriptCache = BossModTranscriptCache;\n`);

const BossModTranscript = global.BossModTranscript;
if (!BossModTranscript || typeof BossModTranscript.createTranscript !== "function") {
    throw new Error("BossModTranscript.createTranscript missing");
}
const BossModTranscriptCache = global.BossModTranscriptCache;
if (!BossModTranscriptCache || typeof BossModTranscriptCache.createCache !== "function") {
    throw new Error("BossModTranscriptCache.createCache missing");
}

function message(key, text, kind = "message") {
    return {
        key,
        author: "agent",
        authorName: "Ada",
        showAuthor: true,
        text,
        createdAt: "",
        kind,
        card: null,
        deskPath: null,
        systemReceipt: false,
    };
}

const presence = BossModGates.createChannelPresenceController();
let eventCards = 0;
// What the roster would say about each agent's active turn. The transcript
// never reads the roster itself, so a plain map is the whole of the seam.
const activityStarts = new Map();

const transcript = BossModTranscript.createTranscript({
    presence,
    activitySince: (agentId) => activityStarts.get(agentId) || null,
    renderMessage(m) {
        const node = new FakeEl("div");
        node.setAttribute("class", "msg");
        node.textContent = m.text;
        return node;
    },
    renderEventCard(m) {
        eventCards += 1;
        const node = new FakeEl("div");
        node.setAttribute("class", "event-card");
        node.textContent = m.text;
        return node;
    },
});

// A dependency that is absent must fail loudly rather than render half a view.
let threw = false;
try {
    BossModTranscript.createTranscript({ presence, renderMessage() {} });
} catch (err) {
    threw = true;
}
if (!threw) throw new Error("a missing renderEventCard must throw, not no-op");

let threwOnActivity = false;
try {
    BossModTranscript.createTranscript({
        presence,
        renderMessage() {},
        renderEventCard() {},
    });
} catch (err) {
    threwOnActivity = true;
}
if (!threwOnActivity) {
    throw new Error("a missing activitySince must throw, not silently drop the duration");
}

const listing = transcript.element.querySelector("[data-transcript]");
if (!listing) throw new Error("the scrolling list must carry data-transcript");
if (listing.getAttribute("role") !== "log") {
    throw new Error('the message list must be role="log"');
}
if (listing.hasAttribute("aria-live")) {
    throw new Error("the transcript must never be a live region (spec 4.2)");
}
if (!String(listing.className).split(/\s+/).includes("transcript")) {
    throw new Error("the scrolling list must carry the transcript class");
}

// ─── Dedupe, and the keyless append that must never collapse ───

transcript.setMessages([message("m1", "one"), message("m2", "two")]);
if (transcript.messageCount() !== 2) {
    throw new Error(`expected 2 messages, got ${transcript.messageCount()}`);
}
const duplicate = transcript.append(message("m1", "one again"));
if (duplicate !== false || transcript.messageCount() !== 2) {
    throw new Error("a repeated key must not double-render");
}
const keylessA = transcript.append(message("", "keyless one"));
const keylessB = transcript.append(message("", "keyless two"));
if (keylessA !== true || keylessB !== true || transcript.messageCount() !== 4) {
    throw new Error("an empty key must always append — collapsing them eats content");
}

// Non-message kinds go to the event-card renderer, not the message renderer.
transcript.append(message("m-note", "note body", "note"));
if (eventCards !== 1) throw new Error("a non-message kind must render as an event card");

// ─── Scroll anchoring ───

listing.clientHeight = 100;
listing.scrollHeight = 200;
listing.scrollTop = 100; // 200 - 100 - 100 = 0px from the bottom
if (transcript.isNearBottom() !== true) throw new Error("0px from the bottom is near");
transcript.append(message("m3", "three"));
const sticksWhenNearBottom = listing.scrollTop === listing.scrollHeight;
if (!sticksWhenNearBottom) throw new Error("a near-bottom append must stick to the bottom");

listing.scrollHeight = 1000;
listing.scrollTop = 200; // 1000 - 200 - 100 = 700px from the bottom
if (transcript.isNearBottom() !== false) throw new Error("700px from the bottom is not near");
transcript.append(message("m4", "four"));
if (listing.scrollTop !== 200) {
    throw new Error("a reading operator must never be yanked to the bottom");
}

// ─── The "N new messages" affordance ───

const live = transcript.element.querySelector('[aria-live="polite"]');
if (!live) throw new Error("the jump affordance must announce politely");
const jump = live.querySelector(".transcript-jump");
if (!jump) throw new Error("the jump button must live inside the live region");
if (jump.textContent !== "1 new message") {
    throw new Error(`expected "1 new message", got "${jump.textContent}"`);
}
transcript.append(message("m5", "five"));
if (jump.textContent !== "2 new messages") {
    throw new Error(`expected "2 new messages", got "${jump.textContent}"`);
}
if (jump.hidden !== false) throw new Error("the jump button must be visible with a count");
if (!jump.getAttribute("aria-label")) {
    throw new Error("an icon-or-count button still needs an accessible name");
}
jump.click();
if (jump.hidden !== true) throw new Error("jumping must reset the count and hide the button");
if (listing.scrollTop !== listing.scrollHeight) {
    throw new Error("jumping must land at the bottom");
}

// ─── setMessages replaces the list and touches nothing outside `element` ───

const sibling = new FakeEl("textarea");
sibling.value = "draft still here";
const beforeElement = transcript.element;
transcript.setMessages([message("only", "only one")]);
if (transcript.messageCount() !== 1) throw new Error("setMessages must replace, not merge");
if (transcript.element !== beforeElement) {
    throw new Error("setMessages must reuse the mounted shell, not rebuild it");
}
if (sibling.value !== "draft still here") {
    throw new Error("the transcript must never reach outside its own element");
}
// A replaced key is forgotten, so the same id may be re-rendered afterwards.
if (transcript.append(message("m1", "one")) !== true) {
    throw new Error("setMessages must reset the dedupe set");
}

// ─── Presence is scoped to one conversation ───

const presenceSlot = transcript.element.querySelector(".transcript-presence");
if (!presenceSlot) throw new Error("the transcript needs a presence slot");
presence.start("a", "ada", "Ada");
transcript.renderPresence("a");
if (presenceSlot.children.length !== 1) {
    throw new Error(`expected 1 presence row, got ${presenceSlot.children.length}`);
}
if (presenceSlot.children[0].textContent !== "Ada is thinking...") {
    throw new Error(`bad presence copy: "${presenceSlot.children[0].textContent}"`);
}
if (listing.children[listing.children.length - 1] !== presenceSlot) {
    throw new Error("the presence slot must stay the last child of the list");
}
presence.start("b", "bo", "Bo");
transcript.renderPresence("a");
if (presenceSlot.children.length !== 1) {
    throw new Error("another conversation's presence must not leak in");
}
transcript.renderPresence("b");
if (presenceSlot.children.length !== 1
    || presenceSlot.children[0].textContent !== "Bo is thinking...") {
    throw new Error("switching conversation must repaint from that conversation only");
}
presence.stopAll("b");
transcript.renderPresence("b");
if (presenceSlot.children.length !== 0) {
    throw new Error("an empty presence list must leave no residue");
}
transcript.renderPresence("a");
if (presenceSlot.children.length !== 1) {
    throw new Error("returning to a conversation must restore its presence");
}

// ─── A long-running turn trades the copy for a duration ───
// This is where the retired `progress` card's information went (spec 12,
// carried items). Below the threshold the copy must not move: "< 1m" tells the
// operator strictly less than "is thinking..." does.

activityStarts.set("ada", new Date(Date.now() - 30 * 1000).toISOString());
transcript.renderPresence("a");
const shortTurnUnchanged = presenceSlot.children[0].textContent === "Ada is thinking...";
if (!shortTurnUnchanged) {
    throw new Error(`a 30s turn must still read "is thinking...", got "${presenceSlot.children[0].textContent}"`);
}

activityStarts.set("ada", new Date(Date.now() - 12 * 60 * 1000).toISOString());
transcript.renderPresence("a");
const longTurnShowsDuration = presenceSlot.children[0].textContent === "Ada is working · 12m";
if (!longTurnShowsDuration) {
    throw new Error(`a 12m turn must read the duration, got "${presenceSlot.children[0].textContent}"`);
}

// A start time the roster does not have, or cannot parse, is not an excuse to
// paint a broken row: the copy falls back to the honest one.
activityStarts.set("ada", "not-a-timestamp");
transcript.renderPresence("a");
const unparseableFallsBack = presenceSlot.children[0].textContent === "Ada is thinking...";
if (!unparseableFallsBack) {
    throw new Error(`an unparseable start must not paint a duration, got "${presenceSlot.children[0].textContent}"`);
}
activityStarts.delete("ada");
transcript.renderPresence("a");
if (presenceSlot.children[0].textContent !== "Ada is thinking...") {
    throw new Error("no start time at all must read as thinking");
}

// ─── Status states ───

function statusNodes() {
    return transcript.element.querySelectorAll(".transcript-status");
}
transcript.setStatus("loading");
if (statusNodes().length !== 1) throw new Error("loading must render exactly one status node");
transcript.setStatus("empty", { title: "No thread messages yet.", hint: "" });
if (statusNodes().length !== 1) throw new Error("a new status must replace the previous one");
transcript.setStatus("error", { message: "Failed to load.", onRetry() {} });
const errors = statusNodes();
if (errors.length !== 1 || errors[0].getAttribute("role") !== "alert") {
    throw new Error('the error state must be role="alert"');
}
transcript.setStatus("ready");
if (statusNodes().length !== 0) throw new Error("ready must clear the status node");

// ─── Cache ───

const cache = BossModTranscriptCache.createCache();
cache.remember("a", [message("c1", "cached one")]);
const recalled = cache.recall("a");
if (!recalled || recalled.length !== 1) throw new Error("recall must return the remembered list");
recalled.push(message("c2", "mutation"));
if (cache.recall("a").length !== 1) {
    throw new Error("recall must return a copy — a caller must not be able to mutate the cache");
}
if (cache.append("a", message("c1", "dup")) !== false) {
    throw new Error("the cache must dedupe by key");
}
if (cache.append("a", message("c2", "later")) !== true
    || cache.recall("a").length !== 2) {
    throw new Error("a live append must land in the cached transcript");
}
if (cache.append("a", message("", "keyless")) !== true
    || cache.recall("a").length !== 3) {
    throw new Error("a keyless message must always be cached too");
}
if (cache.recall("missing") !== null) throw new Error("an unknown id must recall null");
if (cache.forget("a") !== true || cache.recall("a") !== null) {
    throw new Error("forget must drop the cached transcript");
}

// ─── Live queue-visibility line: increment / decrement / clear ───

function liveLine(text) {
    return {
        key: "queue-visibility:ada",
        author: "system",
        authorName: "Ada",
        showAuthor: false,
        text,
        createdAt: "",
        kind: "note",
        live: true,
        cleared: !String(text || "").trim(),
    };
}

transcript.setMessages([]);
if (transcript.append(liveLine("Busy — 1 queued")) !== true || transcript.messageCount() !== 1) {
    throw new Error("a live Busy line must append");
}
if (transcript.append(liveLine("Busy — 2 queued")) !== true || transcript.messageCount() !== 1) {
    throw new Error("increment must replace the same live line, not stack");
}
const busyNode = listing.querySelector('[data-message-key="queue-visibility:ada"]');
if (!busyNode || !String(busyNode.textContent).includes("Busy — 2 queued")) {
    throw new Error(`increment must paint the new depth, got "${busyNode && busyNode.textContent}"`);
}
if (transcript.append(liveLine("Busy — 1 queued")) !== true || transcript.messageCount() !== 1) {
    throw new Error("decrement must replace the same live line");
}
if (transcript.append(liveLine("")) !== true || transcript.messageCount() !== 0) {
    throw new Error("clear must remove the live Busy line");
}
if (listing.querySelector('[data-message-key="queue-visibility:ada"]')) {
    throw new Error("a cleared Busy line must leave no node");
}

cache.remember("q", [liveLine("Busy — 1 queued")]);
if (cache.append("q", liveLine("Busy — 2 queued")) !== true || cache.recall("q").length !== 1) {
    throw new Error("cache increment must replace, not stack");
}
if (cache.recall("q")[0].text !== "Busy — 2 queued") {
    throw new Error("cache increment must keep the new depth");
}
if (cache.append("q", liveLine("")) !== true || cache.recall("q").length !== 0) {
    throw new Error("cache clear must drop the live line");
}

process.stdout.write(JSON.stringify({
    ok: true,
    dedupes: duplicate === false,
    keylessAlwaysAppends: keylessA === true && keylessB === true,
    sticksWhenNearBottom,
    keepsScrollWhenReading: true,
    announcesNewCount: true,
    presenceScoped: true,
    presenceShowsDuration: shortTurnUnchanged && longTurnShowsDuration && unparseableFallsBack,
    cacheCopies: true,
    liveQueueLine: true,
}));
