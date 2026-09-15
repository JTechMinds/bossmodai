/**
 * Node harness: origin one-liner chrome.
 * Done opens a real file. Created/Accepted open the bound Board task.
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
const navigated = [];
const ctx = {
    api: async () => ({ ok: true, async json() { return {}; } }),
    openDeliverable: (path, agentId) => { opened.push({ path, agentId }); },
    navigate: (place, params) => { navigated.push({ place, params }); },
};

function note(text, deskPath, extra) {
    return Object.assign({
        kind: "note",
        text,
        deskPath: deskPath || "",
        authorAgentId: "agent-1",
    }, extra || {});
}

function chip(el) {
    return (el.children || []).find((child) => child.tagName === "BUTTON") || null;
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

const openBtn = chip(openable);
if (openBtn) openBtn.click();

const created = global.BossModEventCards.renderEventCard(
    note("Created: Share review findings", "", { taskId: "task-1" }),
    ctx,
);
const accepted = global.BossModEventCards.renderEventCard(
    note("Accepted: Share review findings", "", { taskId: "task-1" }),
    ctx,
);
const createdNoTask = global.BossModEventCards.renderEventCard(
    note("Created: Share review findings", ""),
    ctx,
);
const createdFakeDoc = global.BossModEventCards.renderEventCard(
    note("Created: Share review findings", "/invented.md", { taskId: "task-1" }),
    ctx,
);
const createdNoNavigate = global.BossModEventCards.renderEventCard(
    note("Created: Share review findings", "", { taskId: "task-1" }),
    { api: ctx.api, openDeliverable: ctx.openDeliverable },
);

const createdBtn = chip(created);
if (createdBtn) createdBtn.click();
const acceptedBtn = chip(accepted);
if (acceptedBtn) acceptedBtn.click();
const fakeDocBtn = chip(createdFakeDoc);
if (fakeDocBtn) fakeDocBtn.click();

const payload = {
    ok: true,
    openableHasTint: String(openable.className || "").includes("note-ok"),
    openableTone: openable.getAttribute("data-tone"),
    openablePath: openable.getAttribute("data-desk-path"),
    openableChip: openBtn ? String(openBtn.textContent || "") : "",
    opened,
    proseHasTint: String(prose.className || "").includes("note-ok"),
    proseHasChip: Boolean(chip(prose)),
    prosePath: prose.getAttribute("data-desk-path"),
    noOpenerHasChip: Boolean(chip(noOpener)),
    noOpenerKeepsPath: noOpener.getAttribute("data-desk-path") === "/projects/review.md",
    createdHasTint: String(created.className || "").includes("note-ok"),
    createdChip: createdBtn ? String(createdBtn.textContent || "") : "",
    createdTaskId: created.getAttribute("data-task-id"),
    createdPath: created.getAttribute("data-desk-path"),
    acceptedHasTint: String(accepted.className || "").includes("note-ok"),
    acceptedChip: acceptedBtn ? String(acceptedBtn.textContent || "") : "",
    acceptedTaskId: accepted.getAttribute("data-task-id"),
    createdNoTaskHasChip: Boolean(chip(createdNoTask)),
    createdFakeDocHasTint: String(createdFakeDoc.className || "").includes("note-ok"),
    createdFakeDocOpensFile: opened.some((item) => item.path === "/invented.md"),
    createdNoNavigateHasChip: Boolean(chip(createdNoNavigate)),
    navigated,
};

process.stdout.write(`${JSON.stringify(payload)}\n`);
