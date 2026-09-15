/**
 * Node harness: Done-link chrome on the origin one-liner.
 * Invoked by tests/test_ui_conversation.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const paths = process.argv.slice(2);
if (paths.length !== 2) throw new Error(`expected 2 module paths, got ${paths.length}`);
eval(`${fs.readFileSync(paths[0], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(paths[1], "utf8")}\n;global.BossModEventCards = BossModEventCards;\n`);

const opened = [];
const ctx = {
    api: async () => ({ ok: true, async json() { return {}; } }),
    openDeliverable: (path, agentId) => { opened.push({ path, agentId }); },
};

function note(text, deskPath, extra) {
    return Object.assign({
        kind: "note",
        text,
        deskPath: deskPath || "",
        authorAgentId: "agent-1",
    }, extra || {});
}

const openable = global.BossModEventCards.renderEventCard(
    note("Done — /projects/review.md", "/projects/review.md"),
    ctx,
);
const prose = global.BossModEventCards.renderEventCard(
    note("Blocked — checkable claim missing", ""),
    ctx,
);
const noOpener = global.BossModEventCards.renderEventCard(
    note("Done — /projects/review.md", "/projects/review.md"),
    { api: ctx.api },
);

const openBtn = (openable.children || []).find((child) => child.tagName === "BUTTON");
if (openBtn) openBtn.click();

const payload = {
    ok: true,
    openableHasTint: String(openable.className || "").includes("note-ok"),
    openableTone: openable.getAttribute("data-tone"),
    openablePath: openable.getAttribute("data-desk-path"),
    openableChip: openBtn ? String(openBtn.textContent || "") : "",
    opened,
    proseHasTint: String(prose.className || "").includes("note-ok"),
    proseHasChip: (prose.children || []).some((child) => child.tagName === "BUTTON"),
    prosePath: prose.getAttribute("data-desk-path"),
    noOpenerHasChip: (noOpener.children || []).some((child) => child.tagName === "BUTTON"),
    noOpenerKeepsPath: noOpener.getAttribute("data-desk-path") === "/projects/review.md",
};

process.stdout.write(`${JSON.stringify(payload)}\n`);
