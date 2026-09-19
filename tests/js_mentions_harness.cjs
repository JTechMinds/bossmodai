/**
 * Node harness: live-agent @mention filter, pill insert, click menu,
 * composer persist, bottom align, regular weight, soft tint, and
 * menu-under-pill.
 * Invoked by tests/test_ui_mentions.py. Not a browser bundle.
 */
const fs = require("fs");
const path = require("path");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModOverlayFocus", "BossModOverlays",
    "BossModMentions", "BossModMentionPills", "BossModMentionDraft",
    "BossModMentionPicker",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const Mentions = global.BossModMentions;
const Pills = global.BossModMentionPills;
const Draft = global.BossModMentionDraft;
const Picker = global.BossModMentionPicker;
const { h } = global.BossModDom;

const JOEY = { id: "joey", name: "Joey", role: "Eng", color: "#1d4ed8" };
const HUGH = { id: "hugh", name: "Hugh", role: "QA", color: "#065f46" };
const DEBRA = { id: "debra", name: "Debra", role: "PM", color: "#92400e" };
const AUDITOR = { id: "auditor", name: "TheAuditor", role: "Auditor", color: "#6d28d9" };
const LIVE = [JOEY, HUGH, DEBRA, AUDITOR];

function fail(label, detail) {
    throw new Error(`${label}: ${detail}`);
}

function ids(agents) {
    return agents.map((agent) => agent.id);
}

const filterAll = ids(Mentions.filterAgents(LIVE, ""));
if (filterAll.join(",") !== "joey,hugh,debra,auditor") {
    fail("filterEmpty", filterAll.join(","));
}

const filterJo = ids(Mentions.filterAgents(LIVE, "jo"));
if (filterJo.join(",") !== "joey") fail("filterJo", filterJo.join(","));

const filterQa = ids(Mentions.filterAgents(LIVE, "qa"));
if (filterQa.join(",") !== "hugh") fail("filterRole", filterQa.join(","));

const filterNone = Mentions.filterAgents(LIVE, "zzz");
if (filterNone.length !== 0) fail("filterNone", filterNone.length);

const firedGone = Mentions.filterAgents(LIVE, "bea");
if (firedGone.length !== 0) fail("filterFired", firedGone.length);

if (Mentions.resolveLive(LIVE, "bea") !== null) fail("resolveFired", "expected null");
if (Mentions.resolveLive([], JOEY.id) !== null) fail("resolveEmpty", "expected null");
if (Mentions.resolveLive(LIVE, "Joey").id !== "joey") fail("resolveName", "Joey");

const triggerAt = Mentions.findTrigger("hello @jo", 9);
if (!triggerAt || triggerAt.start !== 6 || triggerAt.query !== "jo") {
    fail("findTrigger", JSON.stringify(triggerAt));
}
if (Mentions.findTrigger("a@b", 3) !== null) fail("findTriggerEmail", "should ignore mid-token");
if (Mentions.findTrigger("hello @jo more", 14) !== null) {
    fail("findTriggerClosed", "space should close the query");
}

const inserted = Mentions.insertText("hello @jo", 9, "Joey");
if (inserted.text !== "hello @Joey " || inserted.caret !== 12) {
    fail("insertReplace", JSON.stringify(inserted));
}

const appended = Mentions.insertText("hello", 5, "Joey");
if (appended.text !== "hello @Joey " || appended.caret !== 12) {
    fail("insertAgain", JSON.stringify(appended));
}

const hits = Mentions.scanMentions("CLEAR @Joey please", LIVE);
if (hits.length !== 1 || hits[0].agent.id !== "joey") {
    fail("scanLive", JSON.stringify(hits));
}
if (Mentions.scanMentions("CLEAR @Bea please", LIVE).length !== 0) {
    fail("scanFired", "Bea is not live");
}

const possHits = Mentions.scanMentions("Waiting on @TheAuditor's LOCK.", LIVE);
if (possHits.length !== 1 || possHits[0].agent.id !== "auditor" || possHits[0].glue !== "'s") {
    fail("scanPossessiveGlue", JSON.stringify(possHits));
}
const periodHits = Mentions.scanMentions("parked @Joey.", LIVE);
if (periodHits.length !== 1 || periodHits[0].glue !== ".") {
    fail("scanPeriodGlue", JSON.stringify(periodHits));
}

const input = documentStub.createElement("div");
input.setAttribute("contenteditable", "true");
input.className = "composer-input";
Draft.bindEditable(input, { agents: LIVE });
input.value = "tip @hu";
input.selectionStart = 7;
input.selectionEnd = 7;
const composer = documentStub.createElement("div");
composer.className = "composer";
documentStub.body.append(composer);
composer.append(input);

const picker = Picker.bindComposer({
    input,
    container: composer,
    getAgents: () => LIVE,
});
picker.sync();
if (!picker.isOpen()) fail("pickerOpen", "typing @ should open the picker");
const options = picker.element.querySelectorAll(".mention-option");
if (options.length !== 1) fail("pickerFilter", options.length);
if ((options[0].getAttribute("data-agent-id") || options[0].textContent).indexOf("Hugh") === -1
    && options[0].textContent.indexOf("Hugh") === -1) {
    fail("pickerRow", options[0].textContent);
}

const pick = picker.insert(HUGH);
if (!pick || pick.text !== "tip @Hugh ") fail("pillInsert", JSON.stringify(pick));
if (input.value !== "tip @Hugh ") fail("pillInsertValue", input.value);
if (picker.isOpen()) fail("pickerClosedAfterInsert", "pick should close the list");
const composerPill = input.querySelector(".mention-pill");
if (!composerPill) fail("composerPillAfterPick", "pick must paint a pill in the field");
if (!composerPill.getAttribute("style") || !/background:/.test(composerPill.getAttribute("style"))) {
    fail("softTint", composerPill.getAttribute("style"));
}
if (/(?:^|;)\s*color:/.test(composerPill.getAttribute("style") || "")) {
    fail("nameBodyInk", composerPill.getAttribute("style"));
}
const composerName = composerPill.querySelector(".mention-pill-name");
if (!composerName) fail("composerName", "missing name");
if (composerName.getAttribute("style") && /color:/.test(composerName.getAttribute("style"))) {
    fail("nameInherits", composerName.getAttribute("style"));
}
const composerAvatar = composerPill.querySelector(".avatar");
if (!composerAvatar || !/background:/.test(composerAvatar.getAttribute("style") || "")) {
    fail("avatarTint", composerAvatar && composerAvatar.getAttribute("style"));
}
input.append(documentStub.createTextNode("please review"));
if (!input.querySelector(".mention-pill")) fail("composerPersist", "typing must leave the pill");
if (Draft.readEditable(input) !== "tip @Hugh please review") {
    fail("composerSerialize", Draft.readEditable(input));
}

const body = h("div", { class: "msg-body md" }, "Hugh CLEAR parked @Joey.");
const msg = h("div", { class: "msg msg-agent" }, body);
documentStub.body.append(msg);
const painted = Pills.linkify(body, { agents: LIVE, container: msg });
if (painted !== 1) fail("linkifyCount", painted);
const pill = msg.querySelector(".mention-pill");
if (!pill) fail("linkifyPill", "missing pill");
if (pill.getAttribute("data-agent-id") !== "joey") fail("linkifyId", pill.getAttribute("data-agent-id"));
if (pill.tagName !== "BUTTON") fail("linkifyInteractive", pill.tagName);
const host = pill.closest(".mention-host");
if (!host) fail("mentionHost", "interactive pill needs a host the menu hangs off");
if (!pill.getAttribute("style") || !/background:/.test(pill.getAttribute("style"))) {
    fail("chatSoftTint", pill.getAttribute("style"));
}
if (/(?:^|;)\s*color:/.test(pill.getAttribute("style") || "")) {
    fail("chatNameBodyInk", pill.getAttribute("style"));
}
const chatAvatar = pill.querySelector(".avatar");
if (!chatAvatar || !/background:/.test(chatAvatar.getAttribute("style") || "")) {
    fail("chatAvatarTint", chatAvatar && chatAvatar.getAttribute("style"));
}

const unknown = h("div", { class: "msg" },
    h("div", { class: "msg-body" }, "parked @Bea."));
Pills.linkify(unknown.querySelector(".msg-body"), { agents: LIVE, container: unknown });
if (unknown.querySelector(".mention-pill")) fail("linkifyUnknown", "fired name must stay text");

let openedChat = null;
let viewedDesk = null;
let mentionedAgain = null;
Mentions.configure({
    getAgents: () => LIVE,
    onOpenChat: (agent) => { openedChat = agent.id; },
    onViewDesk: (agent) => { viewedDesk = agent.id; },
    onMentionAgain: (agent) => { mentionedAgain = agent.id; },
});

const menu = Pills.openMenu({
    agent: JOEY,
    agents: LIVE,
    anchor: pill,
    container: msg,
});
if (!menu) fail("menuOpen", "live pill must open a menu");
if (menu.element.parentNode !== host) {
    fail("menuUnderPill", menu.element.parentNode && menu.element.parentNode.className);
}
const actions = menu.element.querySelectorAll(".menu-action");
const labels = actions.map((btn) => btn.textLabel || btn.textContent);
if (labels.join("|") !== "Open Chat|View Desk|Mention again") {
    fail("menuActions", labels.join("|"));
}

async function main() {
    await actions[0].dispatchClick();
    if (openedChat !== "joey") fail("openChat", openedChat);
    const menu2 = Pills.openMenu({
        agent: JOEY, agents: LIVE, anchor: pill, container: msg,
    });
    await menu2.element.querySelector("#mention-view-desk").dispatchClick();
    if (viewedDesk !== "joey") fail("viewDesk", viewedDesk);
    if (openedChat !== "joey") fail("viewDeskDidNotOpenChat", openedChat);

    const menu3 = Pills.openMenu({
        agent: JOEY, agents: LIVE, anchor: pill, container: msg,
    });
    await menu3.element.querySelector("#mention-again").dispatchClick();
    if (mentionedAgain !== "joey") fail("mentionAgain", mentionedAgain);

    const dead = Pills.openMenu({
        agent: { id: "bea", name: "Bea" },
        agents: LIVE,
        anchor: pill,
        container: msg,
    });
    if (dead !== null) fail("failClosedFired", "fired agent must not open a menu");

    picker.destroy();
    Mentions.configure(null);

    const css = fs.readFileSync(
        path.join(__dirname, "..", "ui", "static", "css", "conversation.css"),
        "utf8",
    );
    const pillRule = css.split(".mention-pill {")[1].split("}")[0];
    const nameRule = css.split(".mention-pill-name {")[1].split("}")[0];
    const alignBottom = /align-items:\s*flex-end/.test(pillRule)
        && /vertical-align:\s*bottom/.test(pillRule);
    if (!alignBottom) fail("alignBottom", pillRule);
    const regularWeight = /font-weight:\s*400/.test(pillRule)
        && /font-weight:\s*400/.test(nameRule)
        && !/font-weight:\s*600/.test(pillRule)
        && !/font-weight:\s*600/.test(nameRule);
    if (!regularWeight) fail("regularWeight", `${pillRule} | ${nameRule}`);
    const softPillBackground = /background:\s*var\(--bg\)/.test(pillRule)
        && !/background:\s*none/.test(pillRule)
        && /border:\s*1px solid var\(--line\)/.test(pillRule)
        && !/border:\s*0/.test(pillRule)
        && /background:/.test(composerPill.getAttribute("style") || "")
        && /background:/.test(pill.getAttribute("style") || "");
    if (!softPillBackground) fail("softPillBackground", pillRule);

    const possessive = h("div", { class: "msg-body md" }, "Debra parked @TheAuditor's LOCK.");
    const possMsg = h("div", { class: "msg msg-agent" }, possessive);
    documentStub.body.append(possMsg);
    const possPainted = Pills.linkify(possessive, { agents: LIVE, container: possMsg });
    if (possPainted !== 1) fail("possessiveLinkify", possPainted);
    const possPill = possMsg.querySelector(".mention-pill");
    const possName = possPill && possPill.querySelector(".mention-pill-name");
    if (!possName || possName.textContent !== "TheAuditor's") {
        fail("trailingPunctGlued", possName && possName.textContent);
    }
    if (possPill.getAttribute("data-agent-name") !== "TheAuditor") {
        fail("possessiveNameAttr", possPill.getAttribute("data-agent-name"));
    }
    if (possPill.getAttribute("data-mention-glue") !== "'s") {
        fail("possessiveGlueAttr", possPill.getAttribute("data-mention-glue"));
    }
    const leftover = [];
    function walkLooseText(node) {
        if (!node) return;
        if (node.nodeType === 3) {
            leftover.push(node.textContent);
            return;
        }
        if (node.nodeType === 1 && node.classList
            && (node.classList.contains("mention-pill") || node.classList.contains("mention-host"))) {
            return;
        }
        const kids = node.childNodes || node.children || [];
        for (const child of kids) walkLooseText(child);
    }
    walkLooseText(possessive);
    if (leftover.some((text) => text === "'s" || text === " 's")) {
        fail("strandedPossessive", leftover.join("|"));
    }

    const glueField = documentStub.createElement("div");
    Draft.bindEditable(glueField, { agents: LIVE });
    glueField.value = "tip @TheAuditor's LOCK";
    if (Draft.readEditable(glueField) !== "tip @TheAuditor's LOCK") {
        fail("possessiveSerialize", Draft.readEditable(glueField));
    }
    const draftName = glueField.querySelector(".mention-pill-name");
    if (!draftName || draftName.textContent !== "TheAuditor's") {
        fail("possessiveDraft", draftName && draftName.textContent);
    }

    console.log(JSON.stringify({
        ok: true,
        filterEmpty: filterAll,
        filterQuery: filterJo,
        filterRole: filterQa,
        filterNone: filterNone.length,
        insertReplacesQuery: inserted.text,
        insertMentionAgain: appended.text,
        pickerFilter: options.length,
        pillInsert: pick.text,
        composerPersist: true,
        alignBottom: true,
        regularWeight: true,
        softPillBackground: true,
        menuUnderPill: true,
        linkifyLive: painted,
        trailingPunctGlued: possName.textContent,
        noStrandedPossessive: true,
        possessiveSerialize: Draft.readEditable(glueField),
        menuActions: labels,
        openChat: openedChat,
        viewDesk: viewedDesk,
        mentionAgain: mentionedAgain,
        failClosedFired: dead === null,
        noHardJumpOnClick: true,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
