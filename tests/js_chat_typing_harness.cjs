/**
 * Node harness: chat typing indicator is scoped to the selected agent.
 * Invoked by tests/test_ui_chat_typing.py. Not a browser bundle.
 */
const fs = require("fs");

class FakeEl {
    constructor(tag = "div") {
        this.tagName = String(tag).toUpperCase();
        this.id = "";
        this.className = "";
        this.children = [];
        this.parent = null;
        this.dataset = {};
        this._text = "";
        this.scrollTop = 0;
        this.scrollHeight = 0;
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

    appendChild(child) {
        child.parent = this;
        this.children.push(child);
        this.scrollHeight = Math.max(this.scrollHeight, this.children.length * 20);
        return child;
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const out = [];
        const visit = (node) => {
            if (selector.startsWith("#") && node.id === selector.slice(1)) out.push(node);
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

const documentStub = {
    createElement(tag) {
        return new FakeEl(tag);
    },
    getElementById(id) {
        return id === "chat-typing-indicator" ? messages.querySelector("#chat-typing-indicator") : null;
    },
};

global.document = documentStub;
global.window = { document: documentStub };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModUtils = BossModUtils;\n`);

if (typeof BossModUtils !== "object" || typeof BossModUtils.createChatTypingController !== "function") {
    throw new Error("createChatTypingController missing");
}

const messages = new FakeEl("div");
messages.id = "chat-messages";

let selectedId = "agent-a";
let subview = "chat";

const typing = BossModUtils.createChatTypingController({
    getMessagesEl() {
        return messages;
    },
    isActiveChat(agentId) {
        return selectedId === agentId && subview === "chat";
    },
});

function indicator() {
    return messages.querySelector("#chat-typing-indicator");
}

if (typing.show("agent-a") !== true || !indicator()) {
    throw new Error("show for the selected agent must paint Thinking...");
}
if (indicator().dataset.agentId !== "agent-a") {
    throw new Error("indicator must be tagged with the owning agent");
}
if (typing.ownerId() !== "agent-a") {
    throw new Error("owner should be agent-a after show");
}

if (typing.hide("agent-b") !== false || !indicator() || typing.ownerId() !== "agent-a") {
    throw new Error("foreign hide must leave the selected agent's typing alone");
}

selectedId = "agent-b";
if (typing.sync() !== false || indicator()) {
    throw new Error("switching agents must hide the previous agent's indicator");
}
if (typing.ownerId() !== "agent-a") {
    throw new Error("switch should keep pending typing ownership until that agent acks");
}

selectedId = "agent-a";
if (typing.sync() !== true || !indicator()) {
    throw new Error("returning to the pending agent must restore typing");
}

if (typing.hide("agent-a") !== true || indicator() || typing.ownerId() !== null) {
    throw new Error("matching hide must clear ownership and DOM");
}

selectedId = "agent-b";
if (typing.show("agent-a") !== false || indicator() || typing.ownerId() !== "agent-a") {
    throw new Error("show for a non-selected agent must record ownership without painting");
}

if (typing.hide() !== true || typing.ownerId() !== null) {
    throw new Error("unscoped hide must clear any pending owner");
}

selectedId = "1";
if (typing.show("1") !== true || !indicator()) {
    throw new Error("show must paint when the selected id is a string number");
}
if (typing.hide(1) !== true || indicator() || typing.ownerId() !== null) {
    throw new Error("hide must treat string and numeric agent ids as the same owner");
}

process.stdout.write(JSON.stringify({
    ok: true,
    paintsSelected: true,
    ignoresForeignHide: true,
    hidesOnSwitch: true,
    restoresOnReturn: true,
    ignoresForeignShow: true,
}));
