/**
 * Node harness: origin one-liner chrome.
 * Quiet chrome: glyph on the left, blue-link text. No Open pill.
 * Done opens a real file (path only). Created/Accepted open the bound Board task.
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

function walk(el, pred, found) {
    found = found || [];
    if (!el || el.nodeType !== 1) return found;
    if (pred(el)) found.push(el);
    for (const child of el.children || []) walk(child, pred, found);
    return found;
}

function glyph(el) {
    const icon = walk(el, (node) => Boolean(node.getAttribute && node.getAttribute("data-lucide")))[0];
    return icon ? icon.getAttribute("data-lucide") : "";
}

function glyphOnLeft(el) {
    const first = (el.children || []).find((child) => child && child.nodeType === 1);
    return Boolean(first && first.getAttribute && first.getAttribute("data-lucide"));
}

function link(el) {
    return walk(el, (node) => (
        node.tagName === "BUTTON" && String(node.className || "").split(/\s+/).includes("note-link")
    ))[0] || null;
}

function hasOpenPill(el) {
    return walk(el, (node) => (
        node.tagName === "BUTTON"
        && String(node.textContent || "").trim().toLowerCase() === "open"
    )).length > 0;
}

function textOf(el) {
    const p = walk(el, (node) => (
        node.tagName === "P" && String(node.className || "").split(/\s+/).includes("note-text")
    ))[0];
    return p ? String(p.textContent || "") : "";
}

const openable = global.BossModEventCards.renderEventCard(
    note("Jimothy Done — /projects/review.md", "/projects/review.md"),
    ctx,
);
const prose = global.BossModEventCards.renderEventCard(
    note("Jimothy Blocked — checkable claim missing", ""),
    ctx,
);
const noOpener = global.BossModEventCards.renderEventCard(
    note("Jimothy Done — /projects/review.md", "/projects/review.md"),
    { api: ctx.api },
);

const openLink = link(openable);
if (openLink) openLink.click();

const created = global.BossModEventCards.renderEventCard(
    note("Jimothy Created: Share review findings", "", { taskId: "task-1" }),
    ctx,
);
const accepted = global.BossModEventCards.renderEventCard(
    note("Jimothy Accepted: Share review findings", "", { taskId: "task-1" }),
    ctx,
);
const createdNoTask = global.BossModEventCards.renderEventCard(
    note("Jimothy Created: Share review findings", ""),
    ctx,
);
const createdFakeDoc = global.BossModEventCards.renderEventCard(
    note("Jimothy Created: Share review findings", "/invented.md", { taskId: "task-1" }),
    ctx,
);
const createdPathNoTask = global.BossModEventCards.renderEventCard(
    note("Jimothy Created: Share review findings", "/invented.md"),
    ctx,
);
const createdNoNavigate = global.BossModEventCards.renderEventCard(
    note("Jimothy Created: Share review findings", "", { taskId: "task-1" }),
    { api: ctx.api, openDeliverable: ctx.openDeliverable },
);
const writing = global.BossModEventCards.renderEventCard(
    note("Jimothy Writing /tmp/out.md", "/tmp/out.md"),
    ctx,
);

const createdLink = link(created);
if (createdLink) createdLink.click();
const acceptedLink = link(accepted);
if (acceptedLink) acceptedLink.click();
const fakeDocLink = link(createdFakeDoc);
if (fakeDocLink) fakeDocLink.click();
const createdPathNoTaskLink = link(createdPathNoTask);
if (createdPathNoTaskLink) createdPathNoTaskLink.click();
const writingLink = link(writing);
if (writingLink) writingLink.click();

const payload = {
    ok: true,
    openableHasTint: String(openable.className || "").includes("note-ok"),
    openableTone: openable.getAttribute("data-tone"),
    openablePath: openable.getAttribute("data-desk-path"),
    openableKind: openable.getAttribute("data-open-kind"),
    openableGlyph: glyph(openable),
    openableGlyphOnLeft: glyphOnLeft(openable),
    openableHasOpenPill: hasOpenPill(openable),
    openableLink: openLink ? String(openLink.textContent || "") : "",
    openableLinkClass: openLink ? String(openLink.className || "") : "",
    openableText: textOf(openable),
    opened,
    proseHasTint: String(prose.className || "").includes("note-ok"),
    proseHasLink: Boolean(link(prose)),
    proseHasGlyph: Boolean(glyph(prose)),
    proseHasOpenPill: hasOpenPill(prose),
    prosePath: prose.getAttribute("data-desk-path"),
    noOpenerHasLink: Boolean(link(noOpener)),
    noOpenerHasGlyph: Boolean(glyph(noOpener)),
    noOpenerKeepsPath: noOpener.getAttribute("data-desk-path") === "/projects/review.md",
    createdHasTint: String(created.className || "").includes("note-ok"),
    createdKind: created.getAttribute("data-open-kind"),
    createdGlyph: glyph(created),
    createdGlyphOnLeft: glyphOnLeft(created),
    createdHasOpenPill: hasOpenPill(created),
    createdLink: createdLink ? String(createdLink.textContent || "") : "",
    createdTaskId: created.getAttribute("data-task-id"),
    createdPath: created.getAttribute("data-desk-path"),
    acceptedHasTint: String(accepted.className || "").includes("note-ok"),
    acceptedKind: accepted.getAttribute("data-open-kind"),
    acceptedGlyph: glyph(accepted),
    acceptedHasOpenPill: hasOpenPill(accepted),
    acceptedLink: acceptedLink ? String(acceptedLink.textContent || "") : "",
    acceptedTaskId: accepted.getAttribute("data-task-id"),
    createdNoTaskHasLink: Boolean(link(createdNoTask)),
    createdNoTaskHasGlyph: Boolean(glyph(createdNoTask)),
    createdFakeDocHasTint: String(createdFakeDoc.className || "").includes("note-ok"),
    createdFakeDocKind: createdFakeDoc.getAttribute("data-open-kind"),
    createdFakeDocGlyph: glyph(createdFakeDoc),
    createdFakeDocOpensFile: opened.some((item) => item.path === "/invented.md"),
    createdPathNoTaskHasLink: Boolean(createdPathNoTaskLink),
    createdPathNoTaskHasGlyph: Boolean(glyph(createdPathNoTask)),
    createdPathNoTaskKind: createdPathNoTask.getAttribute("data-open-kind") || "",
    createdPathNoTaskOpensFile: opened.some((item) => item.path === "/invented.md"),
    createdNoNavigateHasLink: Boolean(link(createdNoNavigate)),
    writingHasLink: Boolean(writingLink),
    writingHasGlyph: Boolean(glyph(writing)),
    writingKind: writing.getAttribute("data-open-kind") || "",
    writingOpensFile: opened.some((item) => item.path === "/tmp/out.md"),
    navigated,
};

process.stdout.write(`${JSON.stringify(payload)}\n`);
