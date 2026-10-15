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

    get classList() {
        const el = this;
        return { contains: (name) => String(el.className).split(/\s+/).includes(name) };
    }

    get parentNode() {
        return this.parent;
    }

    get nextSibling() {
        if (!this.parent) return null;
        const kids = this.parent.children;
        return kids[kids.indexOf(this) + 1] || null;
    }

    insertBefore(node, ref) {
        if (node.parent) node.parent.children = node.parent.children.filter((c) => c !== node);
        node.parent = this;
        const idx = ref ? this.children.indexOf(ref) : -1;
        if (idx < 0) this.children.push(node);
        else this.children.splice(idx, 0, node);
        return node;
    }

    closest(selector) {
        for (let node = this; node; node = node.parent) {
            if (node.nodeType === 1 && matches(node, selector)) return node;
        }
        return null;
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

    replaceWith(...nodes) {
        if (!this.parent) return;
        const parent = this.parent;
        for (const node of nodes) {
            if (node.parent) node.parent.children = node.parent.children.filter((c) => c !== node);
        }
        const idx = parent.children.indexOf(this);
        parent.children.splice(idx, 1, ...nodes);
        for (const node of nodes) node.parent = parent;
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
    domPath, avatarPath, gatesPath, formatPath, emptyStatePath, noteGroupPath, transcriptPath, cachePath,
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
eval(`${fs.readFileSync(noteGroupPath, "utf8")}\n;global.BossModNoteGroup = BossModNoteGroup;\n`);
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

// ─── Note groups: runs of action receipts collapse behind a toggle ───

function receipt(key) {
    const node = new FakeEl("div");
    node.setAttribute("class", "note");
    node.textContent = key;
    return node;
}
const groupTranscript = BossModTranscript.createTranscript({
    presence,
    activitySince: () => null,
    renderMessage(m) {
        const node = new FakeEl("div");
        node.setAttribute("class", "msg");
        node.textContent = m.text;
        return node;
    },
    renderEventCard(m) {
        if (m.kind === "done") {
            const node = new FakeEl("div");
            node.setAttribute("class", "note note-ok");
            return node;
        }
        return receipt(m.key);
    },
});
const groupList = groupTranscript.element.querySelector(".transcript-list");
const notes = (n, from = 1) => Array.from({ length: n }, (_, i) => message(`n${from + i}`, `n${from + i}`, "note"));

groupTranscript.setMessages(notes(3));
if (groupList.querySelector("[data-note-group]")) throw new Error("3 receipts must stay ungrouped");
groupTranscript.setMessages([...notes(5), message("u1", "hello"), ...notes(3, 6)]);
const groups = groupList.querySelectorAll("[data-note-group]");
if (groups.length !== 1) throw new Error(`expected 1 group for the 5-run only, got ${groups.length}`);
const toggle = groups[0].querySelector(".note-group-toggle");
if (toggle.textContent !== "Show 3 more actions") throw new Error(`bad label "${toggle.textContent}"`);
if (groups[0].querySelector(".note-group-visible")) throw new Error("revision-1 slot must be gone");
const slotKeys = (g, cls) => g.querySelector(`.${cls}`).children.map((n) => n.getAttribute("data-message-key"));
if (slotKeys(groups[0], "note-group-first").join() !== "n1") throw new Error("first slot holds the oldest note");
if (slotKeys(groups[0], "note-group-last").join() !== "n5") throw new Error("last slot holds the newest note");
const overflow = groups[0].querySelector(".note-group-overflow");
if (overflow.hidden !== true) throw new Error("overflow starts collapsed");
toggle.click();
if (overflow.hidden !== false || toggle.textContent !== "Show less") throw new Error("toggle must expand");
toggle.click();
if (overflow.hidden !== true) throw new Error("toggle must collapse again");
if (groupTranscript.messageCount() !== 9) throw new Error("grouping must not change the message count");

// Done receipts and non-receipts break a run.
groupTranscript.setMessages([...notes(2), message("d1", "done", "done"), ...notes(2, 3)]);
if (groupList.querySelector("[data-note-group]")) throw new Error("a note-ok must split runs");

// Live append: the 4th consecutive receipt promotes, the 5th extends.
groupTranscript.setMessages([message("u2", "hi"), ...notes(3)]);
groupTranscript.append(message("n4", "n4", "note"));
let liveGroups = groupList.querySelectorAll("[data-note-group]");
if (liveGroups.length !== 1 || groupList.children.length !== 2) throw new Error("4th receipt must promote in place");
groupTranscript.append(message("n5", "n5", "note"));
liveGroups = groupList.querySelectorAll("[data-note-group]");
const liveToggle = liveGroups[0].querySelector(".note-group-toggle");
if (liveGroups.length !== 1 || groupList.children.length !== 2 || liveToggle.textContent !== "Show 3 more actions") {
    throw new Error("5th receipt must extend the group");
}
if (groupList.querySelector('[data-message-key="n5"]') === null) throw new Error("grouped notes keep keys");
if (groupTranscript.append(message("n5", "n5", "note")) !== false) {
    throw new Error("grouped keys still dedupe");
}

// Removal: leaving 4 keeps the group; leaving 3 dissolves it, keys intact.
groupTranscript.append({ key: "n5", cleared: true });
if (groupList.querySelectorAll("[data-note-group]").length !== 1) throw new Error("4 left must stay grouped");
groupTranscript.append({ key: "n4", cleared: true });
if (groupList.querySelectorAll("[data-note-group]").length !== 0) throw new Error("3 left must dissolve");
if (groupList.children.length !== 4 || groupTranscript.messageCount() !== 4) {
    throw new Error("a dissolved group must hand its notes back to the list");
}
if (groupTranscript.append(message("n1", "n1", "note")) !== false) {
    throw new Error("dissolved notes stay in renderedKeys");
}

// First + last: build a run of 11.
groupTranscript.setMessages(notes(11));
const g11 = groupList.querySelector("[data-note-group]");
const g11Toggle = g11.querySelector(".note-group-toggle");
const g11Overflow = g11.querySelector(".note-group-overflow");
const orderKeys = (g) => g.querySelectorAll(".note").map((n) => n.getAttribute("data-message-key")).join();
if (slotKeys(g11, "note-group-first").join() !== "n1" || slotKeys(g11, "note-group-last").join() !== "n11") {
    throw new Error("a run of 11 keeps n1 first and n11 last");
}
if (g11Overflow.children.length !== 9 || g11Toggle.textContent !== "Show 9 more actions") {
    throw new Error(`expected 9 in overflow with label, got ${g11Overflow.children.length} "${g11Toggle.textContent}"`);
}
if (orderKeys(g11) !== notes(11).map((m) => m.key).join()) throw new Error("document order must be n1..n11");
if (g11Toggle.getAttribute("aria-controls") !== g11Overflow.id || !g11Overflow.id) {
    throw new Error("aria-controls must equal the overflow id");
}
if (g11Toggle.getAttribute("aria-expanded") !== "false") throw new Error("aria-expanded starts false");
g11Toggle.click();
if (g11Toggle.getAttribute("aria-expanded") !== "true") throw new Error("aria-expanded must flip true");
g11Toggle.click();
if (g11Toggle.getAttribute("aria-expanded") !== "false") throw new Error("aria-expanded must flip back");

// Extend: the new note becomes last; the old last ends the overflow.
groupTranscript.append(message("n12", "n12", "note"));
if (slotKeys(g11, "note-group-last").join() !== "n12") throw new Error("new note must become last");
const overflowTail = g11Overflow.children[g11Overflow.children.length - 1];
if (overflowTail.getAttribute("data-message-key") !== "n11") throw new Error("old last must end the overflow");
if (g11Toggle.textContent !== "Show 10 more actions") throw new Error("label count must go up by 1");
if (orderKeys(g11) !== notes(12).map((m) => m.key).join()) throw new Error("order holds after extend");

// Removal refills the end slots from the overflow; dropping below 4 dissolves in order.
groupTranscript.setMessages(notes(6));
const g6 = groupList.querySelector("[data-note-group]");
groupTranscript.append({ key: "n1", cleared: true });
if (slotKeys(g6, "note-group-first").join() !== "n2") throw new Error("first refills from overflow head");
groupTranscript.append({ key: "n6", cleared: true });
if (slotKeys(g6, "note-group-last").join() !== "n5") throw new Error("last refills from overflow tail");
if (orderKeys(g6) !== "n2,n3,n4,n5") throw new Error("order holds after refills");
groupTranscript.append({ key: "n3", cleared: true });
if (groupList.querySelectorAll("[data-note-group]").length !== 0) throw new Error("3 left must dissolve");
if (groupList.children.map((n) => n.getAttribute("data-message-key")).join() !== "n2,n4,n5") {
    throw new Error("dissolved notes keep document order");
}
if (groupTranscript.append(message("n4", "n4", "note")) !== false) throw new Error("keys stay deduped");

// A live keyed line in a group's last slot updates in place.
groupTranscript.setMessages([...notes(4), liveLine("Busy — 1 queued")]);
const gl = groupList.querySelector("[data-note-group]");
if (slotKeys(gl, "note-group-last").join() !== "queue-visibility:ada") throw new Error("live line sits last");
const liveCount = groupTranscript.messageCount();
groupTranscript.append(liveLine("Busy — 2 queued"));
if (groupTranscript.messageCount() !== liveCount || groupList.querySelectorAll("[data-note-group]").length !== 1) {
    throw new Error("live replace must keep structure and count");
}
if (slotKeys(gl, "note-group-last").join() !== "queue-visibility:ada") {
    throw new Error("live replace must stay in the last slot");
}
if (gl.querySelector(".note-group-overflow").children.length !== 3) throw new Error("overflow unchanged");

// Counter while scrolled up: a run of actions counts once.
const counterTranscript = BossModTranscript.createTranscript({
    presence,
    activitySince: () => null,
    renderMessage(m) {
        const node = new FakeEl("div");
        node.setAttribute("class", "msg");
        node.textContent = m.text;
        return node;
    },
    renderEventCard: (m) => receipt(m.key),
});
const counterScroller = counterTranscript.element.querySelector("[data-transcript]");
const counterJump = counterTranscript.element.querySelector(".transcript-jump");
counterScroller.clientHeight = 100;
counterScroller.scrollHeight = 1000;
counterScroller.scrollTop = 200;
counterTranscript.append(message("c1", "hello"));
if (counterJump.textContent !== "1 new message") throw new Error(`got "${counterJump.textContent}"`);
for (const n of notes(5)) counterTranscript.append(message(n.key, n.text, "note"));
if (counterJump.textContent !== "2 new messages") {
    throw new Error(`a run of 5 must count once, got "${counterJump.textContent}"`);
}
counterTranscript.append(message("c2", "after"));
if (counterJump.textContent !== "3 new messages") throw new Error(`got "${counterJump.textContent}"`);

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
