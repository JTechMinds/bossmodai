/**
 * Node harness: h() attribute/child handling and delegate() teardown.
 * Invoked by tests/test_ui_dom.py. Not a browser bundle.
 */
const fs = require("fs");

function makeEl(tag) {
    return {
        // Real elements report nodeType 1; dom.js uses it to tell a node from
        // a primitive, so the mock must report it too.
        nodeType: 1,
        tagName: String(tag).toUpperCase(),
        attributes: {},
        children: [],
        listeners: {},
        textContent: "",
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
        append(...kids) { kids.forEach((k) => this.children.push(k)); },
        addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
        removeEventListener(name, fn) {
            const list = this.listeners[name] || [];
            const i = list.indexOf(fn);
            if (i !== -1) list.splice(i, 1);
        },
        replaceChildren() { this.children = []; },
    };
}

global.document = {
    createElement: makeEl,
    createTextNode(text) { return { nodeType: 3, textContent: String(text) }; },
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);

const { h, clear, delegate } = BossModDom;

// Attributes are set; null and false are skipped entirely.
const el = h("div", { class: "row", "aria-current": null, hidden: false, "data-id": "x" });
if (el.getAttribute("class") !== "row") throw new Error("class not set");
if ("aria-current" in el.attributes) throw new Error("null attribute must be skipped");
if ("hidden" in el.attributes) throw new Error("false attribute must be skipped");

// true renders as an empty string attribute.
const flagged = h("div", { hidden: true });
if (flagged.getAttribute("hidden") !== "") throw new Error("true attribute must be empty string");

// on* keys become listeners, not attributes.
let clicked = 0;
const btn = h("button", { onclick: () => { clicked += 1; } });
if ("onclick" in btn.attributes) throw new Error("onclick must not be an attribute");
btn.listeners.click[0]();
if (clicked !== 1) throw new Error("onclick handler not bound");

// Children flatten; null and false are dropped; strings become text nodes.
const parent = h("div", null, "a", null, false, ["b", "c"], h("span", null));
const kinds = parent.children.map((c) => (c.nodeType === 3 ? c.textContent : c.tagName));
if (JSON.stringify(kinds) !== JSON.stringify(["a", "b", "c", "SPAN"])) {
    throw new Error(`unexpected children: ${JSON.stringify(kinds)}`);
}

// clear() empties children.
clear(parent);
if (parent.children.length !== 0) throw new Error("clear must empty children");

// delegate returns a disposer that actually removes the listener.
const root = makeEl("div");
const off = delegate(root, ".x", "click", () => {});
if ((root.listeners.click || []).length !== 1) throw new Error("delegate must add a listener");
off();
if ((root.listeners.click || []).length !== 0) throw new Error("disposer must remove the listener");

process.stdout.write(JSON.stringify({
    ok: true,
    skipsNullAttrs: true,
    flattensChildren: true,
    disposerRemovesListener: true,
}));
