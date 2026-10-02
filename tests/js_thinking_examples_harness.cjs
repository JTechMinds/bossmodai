/**
 * Node harness: Settings → AI Connections → the connection form's "Examples
 * of common formats" panel. It renders collapsed with its disclaimer, lists
 * every family's five snippets, opens and closes, copies exactly one snippet
 * per button, and never feeds the level inputs — a submit after opening it
 * sends thinking_levels from the five inputs alone.
 * Invoked by tests/test_thinking_examples_ui.py. Not a browser bundle.
 *
 * argv: format.js, dom.js, switch.js, settings-thinking-examples.js,
 * settings-connections-form.js.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

const documentStub = installDom();
const icons = installIconsStub();

// The form finds its model field with `#connection-form [name="model"]`, a
// descendant selector the shared fake refuses. Only that exact shape is
// answered here, by walking to it; anything else still reaches the fake.
const baseQuery = documentStub.querySelector.bind(documentStub);
documentStub.querySelector = (selector) => {
    const scoped = /^#([\w-]+) (\[[^\]]+\])$/.exec(selector);
    if (!scoped) return baseQuery(selector);
    const scope = documentStub.getElementById(scoped[1]);
    return scope ? scope.querySelector(scoped[2]) : null;
};

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

// FormData over the fake form: every named control's current value, which is
// what the browser's FormData reads for text inputs and textareas.
global.FormData = class {
    constructor(form) {
        this.values = {};
        for (const control of form.querySelectorAll("[name]")) {
            const value = control.tagName === "TEXTAREA" && !control.value
                ? control.textContent
                : control.value;
            this.values[control.getAttribute("name")] = value;
        }
    }

    get(key) {
        return Object.prototype.hasOwnProperty.call(this.values, key) ? this.values[key] : null;
    }
};

const clipboardWrites = [];
let clipboardFails = false;
Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
        clipboard: {
            async writeText(text) {
                if (clipboardFails) throw new Error("denied");
                clipboardWrites.push(text);
            },
        },
    },
});

const saves = [];
global.apiFetch = async (url) => { throw new Error(`unexpected fetch ${url}`); };
global.apiFetchOk = async (url, init) => {
    saves.push({ url, method: init.method, body: JSON.parse(init.body) });
    return { ok: true };
};
global.BossModOperatorInvalidate = { notifyLocal() {} };
global.BossModBanners = { refreshModelAvailability: async () => {} };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModSwitch = BossModSwitch;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModThinkingExamples = BossModThinkingExamples;\n`);
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModConnectionForm = BossModConnectionForm;\n`);

const LEVEL_KEYS = ["off", "low", "medium", "high", "xhigh"];

function panelState(toggle, content) {
    return {
        expanded: toggle.getAttribute("aria-expanded"),
        hidden: content.classList.contains("hidden"),
    };
}

function levelInputs(form) {
    return Object.fromEntries(LEVEL_KEYS.map((key) => [
        key, form.querySelector(`#connection-thinking-${key}`).value,
    ]));
}

async function main() {
    let done = 0;
    const container = new FakeEl("div");
    document.body.append(container);
    BossModConnectionForm.renderForm(null, { container, onDone: async () => { done += 1; } });

    const form = container.querySelector("#connection-form");
    const toggle = container.querySelector("#connection-thinking-examples-toggle");
    const content = container.querySelector("#connection-thinking-examples-content");
    const status = container.querySelector("#connection-thinking-examples-status");
    const fieldset = toggle.closest("fieldset");

    const initial = {
        ...panelState(toggle, content),
        toggleType: toggle.getAttribute("type"),
        controls: toggle.getAttribute("aria-controls"),
        title: toggle.textContent.trim(),
        insideThinkingFieldset: Boolean(fieldset && fieldset.querySelector("legend").textContent.trim() === "Thinking levels"),
        firstLine: content.querySelectorAll("p")[0].textContent.trim(),
        statusRole: status.getAttribute("role"),
        statusLive: status.getAttribute("aria-live"),
        liveRegions: container.querySelectorAll("[role]").filter((el) => el.getAttribute("role") === "status").length,
        painted: icons.calls.map((call) => ({ isToggle: call.root === toggle, context: call.context })),
    };

    const families = content.children
        .filter((node) => node.nodeType === 1 && node.classList.contains("space-y-1"))
        .map((block) => {
            const paragraphs = block.querySelectorAll("p").map((p) => p.textContent.trim());
            const rows = block.querySelectorAll("code").map((code) => {
                const row = code.parent;
                const button = row.querySelector("button");
                return {
                    label: row.querySelector("span").textContent.trim(),
                    code: code.textContent,
                    buttonType: button.getAttribute("type"),
                    ariaLabel: button.getAttribute("aria-label"),
                };
            });
            return { title: paragraphs[0], usedBy: paragraphs[1], rows };
        });

    await toggle.dispatchClick();
    const opened = panelState(toggle, content);
    await toggle.dispatchClick();
    const closed = panelState(toggle, content);
    await toggle.dispatchClick();
    const reopened = panelState(toggle, content);

    const inputsBeforeCopy = levelInputs(form);
    const buttons = content.querySelectorAll("[data-copy-thinking-example]");
    // The chat-template family's Extra high row: the last button.
    const target = buttons[buttons.length - 1];
    await target.dispatchClick();
    const copied = { writes: [...clipboardWrites], status: status.textContent, ariaLabel: target.getAttribute("aria-label") };
    clipboardFails = true;
    await buttons[0].dispatchClick();
    const failed = { writes: [...clipboardWrites], status: status.textContent };
    clipboardFails = false;
    const inputsAfterCopy = levelInputs(form);

    form.querySelector("[name=\"name\"]").value = "Local";
    form.querySelector("[name=\"api_base_url\"]").value = "http://127.0.0.1:9/v1";
    form.querySelector("#connection-thinking-off").value = '{"thinking": {"type": "disabled"}}';
    form.querySelector("#connection-thinking-high").value = '{"reasoning_effort": "high"}';
    const submit = form.querySelectorAll("button").find((btn) => btn.getAttribute("type") === "submit");
    await submit.dispatchClick();

    process.stdout.write(JSON.stringify({
        ok: true,
        examples: BossModThinkingExamples.THINKING_FORMAT_EXAMPLES,
        frozen: Object.isFrozen(BossModThinkingExamples.THINKING_FORMAT_EXAMPLES)
            && BossModThinkingExamples.THINKING_FORMAT_EXAMPLES.every(
                (family) => Object.isFrozen(family) && Object.isFrozen(family.snippets)),
        initial,
        families,
        opened,
        closed,
        reopened,
        copied,
        failed,
        inputsBeforeCopy,
        inputsAfterCopy,
        saves,
        done,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
