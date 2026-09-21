/**
 * Node harness: http(s) click → desktop opener invoke, and nothing else.
 *
 * Invoked by tests/test_desktop_external_open.py. Not a browser bundle.
 *
 * The properties a grep cannot see: a transcript link and a composer link
 * both reach the same `open_external_url` invoke, the webview location is
 * never assigned, and javascript: / file: / mailto: / relative paths do not
 * go through this opener.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();

const [modulePath] = process.argv.slice(2);
if (!modulePath) {
    throw new Error("expected the external-open module path");
}
eval(`${fs.readFileSync(modulePath, "utf8")}\n;global.BossModExternalOpen = BossModExternalOpen;\n`);
const { BossModExternalOpen } = global;

if (BossModExternalOpen.COMMAND !== "open_external_url") {
    throw new Error("desktop command drifted");
}

function clickEvent(target, extras) {
    const event = {
        target,
        button: 0,
        defaultPrevented: false,
        stopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; },
        ...extras,
    };
    return event;
}

function installTauri(invokes) {
    global.__TAURI__ = {
        core: {
            invoke(cmd, args) {
                invokes.push({ cmd, args });
                return Promise.resolve();
            },
        },
    };
}

function el(tag, attrs, ...children) {
    const node = documentStub.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    for (const child of children) {
        node.append(typeof child === "string" ? documentStub.createTextNode(child) : child);
    }
    return node;
}

const opens = [];
window.open = (url, target, features) => {
    opens.push({ url, target, features });
    return { closed: false };
};

let locationAssigned = false;
Object.defineProperty(window, "location", {
    configurable: true,
    value: {
        set href(_value) { locationAssigned = true; },
        assign() { locationAssigned = true; },
        replace() { locationAssigned = true; },
    },
});

const GITHUB = "https://github.com/example/compare/main...branch";
const HTTP = "http://example.com/docs";

// ─── Transcript click invokes the desktop opener ───
const transcriptInvokes = [];
installTauri(transcriptInvokes);
const transcriptLink = el("a", {
    href: GITHUB, target: "_blank", rel: "noopener noreferrer",
}, "Open:");
const transcript = el("div", { class: "msg-body md" },
    el("p", null, transcriptLink));
const transcriptEvent = clickEvent(transcriptLink);
const transcriptHandled = BossModExternalOpen.handleClick(transcriptEvent);
if (!transcriptHandled) throw new Error("transcript https click must be handled");
if (!transcriptEvent.defaultPrevented) throw new Error("transcript click must preventDefault");
if (!transcriptEvent.stopped) throw new Error("transcript click must stopPropagation");
if (transcriptInvokes.length !== 1 || transcriptInvokes[0].cmd !== "open_external_url") {
    throw new Error(`transcript must invoke once, got ${JSON.stringify(transcriptInvokes)}`);
}
if (transcriptInvokes[0].args.url !== GITHUB) {
    throw new Error(`transcript invoke URL drifted: ${JSON.stringify(transcriptInvokes[0].args)}`);
}
if (JSON.stringify(transcriptInvokes[0].args) !== JSON.stringify({ url: GITHUB })) {
    throw new Error(`invoke payload must be url-only, got ${JSON.stringify(transcriptInvokes[0].args)}`);
}

// ─── Composer click uses the same command ───
const composerInvokes = [];
installTauri(composerInvokes);
const composerLink = el("a", { href: HTTP }, HTTP);
const composer = el("div", { class: "composer-input" }, composerLink);
const nested = el("span", null, "http://example.com/docs");
composerLink.append(nested);
const composerEvent = clickEvent(nested);
const composerHandled = BossModExternalOpen.handleClick(composerEvent);
if (!composerHandled) throw new Error("composer http click must be handled");
if (!composerEvent.defaultPrevented) throw new Error("composer click must preventDefault");
if (composerInvokes.length !== 1 || composerInvokes[0].cmd !== "open_external_url") {
    throw new Error(`composer must invoke the same command, got ${JSON.stringify(composerInvokes)}`);
}
if (composerInvokes[0].args.url !== `${HTTP}/` && composerInvokes[0].args.url !== HTTP) {
    throw new Error(`composer invoke URL drifted: ${JSON.stringify(composerInvokes[0].args)}`);
}

// ─── javascript: / file: / mailto: / relative paths stay off this path ───
const refusedInvokes = [];
installTauri(refusedInvokes);
const refusals = [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,hi",
    "mailto:ops@example.com",
    "/files",
    "#settings",
];
for (const href of refusals) {
    const link = el("a", { href }, "x");
    const event = clickEvent(link);
    const handled = BossModExternalOpen.handleClick(event);
    if (handled) throw new Error(`${href} must not be handled by the http opener`);
    if (event.defaultPrevented) throw new Error(`${href} must be left to its existing opener`);
}
if (refusedInvokes.length !== 0) {
    throw new Error(`refused schemes reached invoke: ${JSON.stringify(refusedInvokes)}`);
}

if (BossModExternalOpen.httpUrlFrom("javascript:https://example.com") !== null) {
    throw new Error("javascript: must not parse as http");
}
if (BossModExternalOpen.httpUrlFrom("file://https://example.com") !== null) {
    throw new Error("file: must not parse as http");
}

// ─── Middle-click is not this gesture ───
const middleInvokes = [];
installTauri(middleInvokes);
const middleLink = el("a", { href: GITHUB }, "x");
const middle = clickEvent(middleLink, { button: 1 });
if (BossModExternalOpen.handleClick(middle)) {
    throw new Error("middle-click must not take the http opener");
}
if (middleInvokes.length !== 0) throw new Error("middle-click reached invoke");

// ─── Browser / no Tauri: window.open, never location ───
delete global.__TAURI__;
const beforeOpens = opens.length;
const browserLink = el("a", { href: GITHUB }, "x");
const browserEvent = clickEvent(browserLink);
if (!BossModExternalOpen.handleClick(browserEvent)) {
    throw new Error("browser session must still open http(s) off-webview");
}
if (opens.length !== beforeOpens + 1) {
    throw new Error("browser fallback must call window.open");
}
const lastOpen = opens[opens.length - 1];
if (lastOpen.url !== GITHUB || lastOpen.target !== "_blank") {
    throw new Error(`window.open args drifted: ${JSON.stringify(lastOpen)}`);
}
if (!String(lastOpen.features || "").includes("noopener")) {
    throw new Error("browser fallback must not hand over a window reference");
}
if (locationAssigned) throw new Error("webview location must never be assigned");

const destroy = BossModExternalOpen.install();
if (typeof destroy !== "function") throw new Error("install must return an unbind");
destroy();

process.stdout.write(JSON.stringify({
    ok: true,
    transcriptInvokesDesktop: true,
    composerInvokesDesktop: true,
    sameCommand: true,
    preventDefault: true,
    javascriptRefused: true,
    fileRefused: true,
    mailtoLeftAlone: true,
    relativeLeftAlone: true,
    browserUsesWindowOpen: true,
    webviewLocationUntouched: true,
}));
