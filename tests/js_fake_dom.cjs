/**
 * A small DOM stand-in shared by the Phase 2 conversation harnesses.
 *
 * The conversation surface is six modules that hand each other real element
 * objects, so each harness needs the same slice of the DOM: attributes and
 * dataset, classList, append/replaceChildren/remove, closest, querySelector,
 * listeners, and the three scroll numbers. Six private copies of that would
 * drift, and a drifted fake is a test that proves nothing.
 *
 * Not a browser bundle and not loaded by index.html — required by the .cjs
 * harnesses under tests/.
 */

const KEBAB = /-([a-z])/g;
const CAMEL = /[A-Z]/g;

function toAttrName(key) {
    return `data-${String(key).replace(CAMEL, (c) => `-${c.toLowerCase()}`)}`;
}

function toDatasetKey(attr) {
    return attr.slice(5).replace(KEBAB, (_, c) => c.toUpperCase());
}

/**
 * A text or comment node, with the slice of CharacterData a walker needs.
 *
 * These used to be bare object literals in two places, and both were missing
 * `remove()` — which every real text and comment node has, because they
 * inherit it from ChildNode along with elements. core/markdown.js drops a
 * comment out of a parsed tree and moves text nodes when it unwraps a tag, so
 * a fake without it turns a working sanitiser into a TypeError.
 *
 * @param {number} nodeType  3 for text, 8 for a comment.
 * @param {string} text
 * @returns {object}
 */
function characterData(nodeType, text) {
    return {
        nodeType,
        textContent: String(text),
        parent: null,
        get parentNode() {
            return this.parent;
        },
        remove() {
            if (!this.parent) return;
            this.parent.children = this.parent.children.filter((child) => child !== this);
            this.parent = null;
        },
    };
}

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
        this.checked = false;
        this.value = "";
        this.style = {};
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.clientHeight = 0;
        this._text = "";

        const el = this;
        this.dataset = new Proxy({}, {
            get(_t, key) {
                if (typeof key !== "string") return undefined;
                const value = el.attrs[toAttrName(key)];
                return value === undefined ? undefined : value;
            },
            set(_t, key, value) {
                el.attrs[toAttrName(key)] = String(value);
                return true;
            },
            has(_t, key) {
                return toAttrName(key) in el.attrs;
            },
            deleteProperty(_t, key) {
                delete el.attrs[toAttrName(key)];
                return true;
            },
            ownKeys() {
                return Object.keys(el.attrs)
                    .filter((name) => name.startsWith("data-"))
                    .map(toDatasetKey);
            },
            getOwnPropertyDescriptor() {
                return { enumerable: true, configurable: true };
            },
        });

        this.classList = {
            add(...names) {
                const set = el._classSet();
                names.forEach((name) => set.add(name));
                el.attrs.class = Array.from(set).join(" ");
            },
            remove(...names) {
                const set = el._classSet();
                names.forEach((name) => set.delete(name));
                el.attrs.class = Array.from(set).join(" ");
            },
            toggle(name, force) {
                const set = el._classSet();
                const on = force === undefined ? !set.has(name) : Boolean(force);
                if (on) set.add(name);
                else set.delete(name);
                el.attrs.class = Array.from(set).join(" ");
                return on;
            },
            contains(name) {
                return el._classSet().has(name);
            },
        };
    }

    _classSet() {
        return new Set(String(this.attrs.class || "").split(/\s+/).filter(Boolean));
    }

    /**
     * Real DOM semantics: `disabled` REFLECTS to the attribute, which is what
     * makes `button:not([disabled])` mean anything. Without this the overlay
     * focus trap would happily hand focus to a disabled control in every
     * harness that opens an overlay, and no test could see it.
     */
    get disabled() {
        return this.hasAttribute("disabled");
    }

    set disabled(value) {
        if (value) this.attrs.disabled = "";
        else delete this.attrs.disabled;
    }

    get id() {
        return this.attrs.id || "";
    }

    set id(value) {
        this.attrs.id = String(value);
    }

    get className() {
        return this.attrs.class || "";
    }

    set className(value) {
        this.attrs.class = String(value);
    }

    /**
     * The attributes, in document order, as a real element reports them.
     *
     * `Array.from(el.attributes)` over a NamedNodeMap yields Attr nodes with
     * `name` and `value` — the shape core/icons.js reads to carry a
     * placeholder's classes and id onto the SVG that replaces it.
     */
    get attributes() {
        return Object.entries(this.attrs).map(([name, value]) => ({ name, value }));
    }

    /** Real DOM name for the link this fake stores as `parent`. */
    get parentNode() {
        return this.parent;
    }

    /**
     * Every child node, text nodes included.
     *
     * NOT the same list as `children`, which this fake happens to store
     * everything in: a real element's `children` is elements ONLY, so a walker
     * written against `children` would silently skip every text node in the
     * browser while passing here. core/markdown.js sanitises a parsed tree and
     * has to see the text, so it reads `childNodes` and this is what makes the
     * fake honest about the difference.
     */
    get childNodes() {
        return this.children;
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    }

    setAttribute(name, value) {
        this.attrs[name] = String(value);
    }

    removeAttribute(name) {
        delete this.attrs[name];
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
        for (const child of this.children) child.parent = null;
        this.children = [];
        this._text = String(value);
    }

    get innerText() {
        return this.textContent;
    }

    set innerText(value) {
        this.textContent = value;
    }

    append(...nodes) {
        for (const raw of nodes) {
            // ParentNode.append() takes strings as well as nodes, and modules
            // written against the real DOM use that. Wrapping here rather than
            // making every caller wrap keeps the fake honest about the contract.
            const node = (raw && raw.nodeType)
                ? raw
                : characterData(3, raw);
            // Real DOM semantics: appending an attached node MOVES it.
            if (node.parent) {
                node.parent.children = node.parent.children.filter((child) => child !== node);
            }
            node.parent = this;
            this.children.push(node);
            this._text = "";
        }
    }

    appendChild(node) {
        this.append(node);
        return node;
    }

    prepend(...nodes) {
        const existing = this.children;
        this.children = [];
        this.append(...nodes);
        for (const child of existing) {
            child.parent = this;
            this.children.push(child);
        }
        this._text = "";
    }

    insertBefore(node, reference) {
        if (node.parent) {
            node.parent.children = node.parent.children.filter((child) => child !== node);
        }
        node.parent = this;
        const at = this.children.indexOf(reference);
        if (at === -1) this.children.push(node);
        else this.children.splice(at, 0, node);
        this._text = "";
        return node;
    }

    replaceChildren(...nodes) {
        for (const child of this.children) child.parent = null;
        this.children = [];
        this._text = "";
        this.append(...nodes);
    }

    /**
     * Swap one child for another, in place.
     *
     * Position matters: an icon painted into the middle of a button has to
     * come back in the middle of it, so this splices rather than appends.
     *
     * @param {object} node  The replacement.
     * @param {object} old  The child to replace.
     * @returns {object} `old`, as the real DOM returns it.
     * @throws {Error} When `old` is not a child of this element — the real DOM
     *   throws NotFoundError, and a fake that appended instead would hide the
     *   bug that got it here.
     */
    replaceChild(node, old) {
        const at = this.children.indexOf(old);
        if (at === -1) throw new Error("[fake-dom] replaceChild: node is not a child");
        if (node.parent) {
            node.parent.children = node.parent.children.filter((child) => child !== node);
        }
        node.parent = this;
        this.children[at] = node;
        old.parent = null;
        return old;
    }

    remove() {
        if (!this.parent) return;
        this.parent.children = this.parent.children.filter((child) => child !== this);
        this.parent = null;
    }

    contains(node) {
        if (node === this) return true;
        return this.children.some((child) => child.nodeType === 1 && child.contains(node));
    }

    matches(selector) {
        return matches(this, selector);
    }

    closest(selector) {
        let node = this;
        while (node) {
            if (node.nodeType === 1 && matches(node, selector)) return node;
            node = node.parent;
        }
        return null;
    }

    addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
    }

    removeEventListener(type, fn) {
        this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== fn);
    }

    /**
     * The form this button submits, or null.
     *
     * Real DOM semantics, and the half the fake was missing: a submit button
     * submits the form it sits in, OR the form its `form` attribute names —
     * which need not contain it. That attribute is the whole mechanism behind
     * a dialog pinning a form's primary action outside a scrolling body, and
     * without it here a harness could only assert the markup, never the click.
     *
     * @returns {FakeEl|null}
     */
    _formToSubmit() {
        if (this.tagName !== "BUTTON") return null;
        // HTML's default button type inside a form is "submit".
        if ((this.getAttribute("type") || "submit") !== "submit") return null;
        if (this.disabled) return null;
        const named = this.getAttribute("form");
        if (named) {
            return this.ownerDocument ? this.ownerDocument.getElementById(named) : null;
        }
        return this.closest("form");
    }

    /** Fire every click handler, including the onclick property. @returns {Promise<void>} */
    async dispatchClick() {
        const event = { preventDefault() {}, stopPropagation() {}, target: this, key: "", shiftKey: false };
        const handlers = [...(this.listeners.click || [])];
        if (typeof this.onclick === "function") handlers.push(this.onclick);
        for (const fn of handlers) await fn(event);
        const form = this._formToSubmit();
        if (!form) return;
        const submitEvent = { preventDefault() {}, stopPropagation() {}, target: form };
        for (const fn of [...(form.listeners.submit || [])]) await fn(submitEvent);
    }

    click() {
        void this.dispatchClick();
    }

    focus() {
        if (this.ownerDocument) this.ownerDocument._activeElement = this;
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const wanted = String(selector).split(",").map((part) => part.trim()).filter(Boolean);
        const out = [];
        const visit = (node) => {
            if (node.nodeType !== 1) return;
            if (wanted.some((part) => matches(node, part))) out.push(node);
            for (const child of node.children) visit(child);
        };
        for (const child of this.children) visit(child);
        return out;
    }
}

/** An id or class name: no combinator, no second `.`, `#` or `[` inside it. */
const SIMPLE_NAME = /^[A-Za-z_-][A-Za-z0-9_-]*$/;

/**
 * Match one element against a single simple selector.
 *
 * Supports `#id`, `.class`, `[attr]`, `[attr="value"]`, and a tag name — the
 * only forms the conversation modules use. Anything else throws rather than
 * quietly matching nothing, because a selector the fake cannot understand
 * would turn a real failure into a green test.
 *
 * That promise used to have a hole in it. A descendant selector (`.a .b`) or a
 * compound one (`.a.b`) starts with a `.`, so the class branch took it, sliced
 * the leading dot, and asked whether the element's class list contained the
 * literal string `a .b` — which nothing ever does. The answer was a silent
 * `false`, `querySelector` returned `null`, and a harness looking for a node it
 * could not express read that null as "the node is absent". Every form the fake
 * cannot express now throws, which is what the docstring above always claimed.
 *
 * One negation is understood on top of that — `button:not([disabled])` and
 * `[tabindex]:not([tabindex="-1"])` are two thirds of core/overlays.js's
 * focusable list, and a fake that threw on them could not run a harness that
 * opens an overlay at all.
 *
 * So is a tag followed by ONE attribute clause — `select[name="desk"]`,
 * `input[name="name"]`, `textarea[name="description"]`. That is still one
 * simple selector with no combinator in it, and it is how the agent form finds
 * its own fields; throwing on it would have made the form untestable rather
 * than catching anything. A compound the fake genuinely cannot express — two
 * classes, a descendant — still throws.
 *
 * @param {FakeEl} el
 * @param {string} selector
 * @returns {boolean}
 * @throws {Error} When the selector is not one simple selector.
 */
function matches(el, selector) {
    const negated = /^([^\s>+~]*):not\(([^()]+)\)$/.exec(selector);
    if (negated) {
        const [, base, inner] = negated;
        if (!inner.trim()) throw new Error(`[fake-dom] unsupported selector: ${selector}`);
        if (base && !matches(el, base)) return false;
        return !matches(el, inner.trim());
    }
    if (/[\s>+~]/.test(selector)) {
        throw new Error(
            `[fake-dom] unsupported selector: ${selector} — this fake matches one `
            + "simple selector, not a combinator. Find the node by walking to it.",
        );
    }
    if (selector.startsWith("#")) {
        const id = selector.slice(1);
        if (!SIMPLE_NAME.test(id)) {
            throw new Error(`[fake-dom] unsupported selector: ${selector}`);
        }
        return el.id === id;
    }
    if (selector.startsWith(".")) {
        const name = selector.slice(1);
        if (!SIMPLE_NAME.test(name)) {
            throw new Error(`[fake-dom] unsupported selector: ${selector}`);
        }
        return String(el.className).split(/\s+/).includes(name);
    }
    const tagAttr = /^([A-Za-z][A-Za-z0-9]*)(\[[^\]]+\])$/.exec(selector);
    if (tagAttr) {
        return matches(el, tagAttr[1]) && matches(el, tagAttr[2]);
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
    if (/^[A-Za-z][A-Za-z0-9]*$/.test(selector)) {
        return el.tagName === selector.toUpperCase();
    }
    throw new Error(`[fake-dom] unsupported selector: ${selector}`);
}

/**
 * Install a document/window pair on `global` and return the document.
 *
 * @returns {object} The document stub. `_activeElement` records the last
 *   element focused, which is how the harnesses assert focus movement.
 */
function installDom() {
    const body = new FakeEl("body");
    const registry = new Map();

    const documentStub = {
        _activeElement: null,
        body,
        // The base every relative URL resolves against, as a real document
        // reports it. core/markdown.js resolves an agent-supplied href before
        // it will trust the scheme, and `new URL(href, undefined)` THROWS on a
        // relative one — so without this the fake would exercise the reject
        // path for links the browser resolves perfectly well.
        baseURI: "https://bossmod.localhost/",
        createElement(tag) {
            const el = new FakeEl(tag);
            el.ownerDocument = documentStub;
            return el;
        },
        /**
         * The SVG-namespace constructor. An icon built with createElement in
         * the HTML namespace parses but never renders, which is why lucide's
         * own builder uses this one and why the fake has to answer it.
         */
        createElementNS(namespace, tag) {
            const el = new FakeEl(tag);
            el.namespaceURI = String(namespace);
            el.ownerDocument = documentStub;
            return el;
        },
        createTextNode(text) {
            return characterData(3, text);
        },
        createComment(text) {
            return characterData(8, text);
        },
        getElementById(id) {
            const found = body.querySelector(`#${id}`);
            return found || registry.get(id) || null;
        },
        querySelector(selector) {
            return body.querySelector(selector);
        },
        querySelectorAll(selector) {
            return body.querySelectorAll(selector);
        },
        addEventListener() {},
        removeEventListener() {},
        get activeElement() {
            return documentStub._activeElement;
        },
        /** Make an element reachable by id without attaching it to <body>. */
        _register(el) {
            if (el.id) registry.set(el.id, el);
            return el;
        },
    };
    body.ownerDocument = documentStub;

    // Site data the modules may read. Present by default so the real code
    // path runs; delete window.localStorage in a harness to exercise the
    // blocked-storage branch instead.
    const stored = new Map();
    const mediaQueries = new Map();
    global.document = documentStub;
    global.window = {
        document: documentStub,
        lucide: null,
        // Real browser API, not a module: the toast asks whether the operator
        // has asked for reduced motion, and the responsive layer asks which
        // breakpoint is live. Reassign window.matchMedia in a harness to
        // exercise a query that matches, or call `_emit` on what this returns
        // to simulate a viewport crossing a breakpoint.
        matchMedia: (query) => {
            // Memoised by query, as a browser's is: two calls for the same
            // media string must return the same object, or a harness cannot
            // reach the listener the module under test registered.
            const key = String(query);
            if (mediaQueries.has(key)) return mediaQueries.get(key);
            const listeners = [];
            const media = {
                media: String(query),
                matches: false,
                addEventListener: (_type, fn) => { listeners.push(fn); },
                removeEventListener: (_type, fn) => {
                    const at = listeners.indexOf(fn);
                    if (at !== -1) listeners.splice(at, 1);
                },
                _listenerCount: () => listeners.length,
                _emit: (matches) => {
                    media.matches = matches;
                    listeners.slice().forEach((fn) => fn({ matches }));
                },
            };
            mediaQueries.set(key, media);
            return media;
        },
        localStorage: {
            getItem: (key) => (stored.has(key) ? stored.get(key) : null),
            setItem: (key, value) => { stored.set(key, String(value)); },
            removeItem: (key) => { stored.delete(key); },
        },
    };
    return documentStub;
}

module.exports = { FakeEl, matches, installDom };
