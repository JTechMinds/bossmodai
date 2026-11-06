/**
 * Node harness: the two narrow-layout controls (spec 10).
 * Invoked by tests/test_ui_responsive.py. Not a browser bundle.
 *
 * The layout is CSS and is asserted from the stylesheet. What CSS cannot show
 * is that the operator can get the columns back, that getting them back does
 * not rebuild them, and that widening the window puts them where the grid
 * expects. Those are this file's four properties.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = ["BossModDom", "BossModStore", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu",
    "BossModPlaces", "BossModResponsive"];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModResponsive, BossModPlaces } = global;

// The frame, as index.html builds it: roster, place, context, in that order.
const header = documentStub.createElement("header");
const layout = documentStub.createElement("div");
const roster = documentStub.createElement("aside");
const place = documentStub.createElement("div");
const context = documentStub.createElement("aside");
roster.className = "app-roster";
place.className = "app-place";
context.className = "app-context";
layout.append(roster, place, context);
documentStub.body.append(header, layout);

// Something already inside each column, so a REBUILD is distinguishable from a
// move: a rebuilt column would not still be holding these.
const rosterMark = documentStub.createElement("p");
rosterMark.className = "roster-mark";
roster.append(rosterMark);
const contextMark = documentStub.createElement("p");
contextMark.className = "context-mark";
context.append(contextMark);

const store = BossModStore.createStore({ place: "chat", conversationId: null, conversationKind: null });
const mounted = BossModResponsive.mount({
    headerEl: header,
    layoutEl: layout,
    rosterEl: roster,
    placeEl: place,
    contextEl: context,
    store,
});
// The drawer's opener is handed back so the shell can give it to a place;
// the disposer travels beside it rather than being the whole return.
if (typeof mounted.destroy !== "function" || typeof mounted.openRoster !== "function") {
    throw new Error(`mount() must return { destroy, openRoster }, got ${Object.keys(mounted)}`);
}
const { destroy } = mounted;

const menu = header.querySelector(".responsive-menu-btn");
// The context column holds the office summary and nothing else, so its
// opener is named for that: a "Desk" button that opened no desk would lie.
const desk = header.querySelector(".responsive-context-btn");
if (!menu) throw new Error("no drawer button was inserted into the header");
if (!desk) throw new Error("no context button was inserted into the header");
const contextOpenerSaysOffice = desk.getAttribute("aria-label") === "Open the office summary"
    && desk.querySelector(".responsive-btn-label").textContent === "Office"
    && header.querySelectorAll(".responsive-desk-btn").length === 0;
if (!contextOpenerSaysOffice) throw new Error("the context opener must say Office, not Desk");

// ─── 1. Both openers are real, named buttons ───

const openersAreNamedButtons =
    menu.tagName === "BUTTON" && desk.tagName === "BUTTON"
    && String(menu.getAttribute("aria-label")).trim().length > 0
    && String(desk.getAttribute("aria-label")).trim().length > 0
    && menu.getAttribute("type") === "button"
    && desk.getAttribute("type") === "button";
if (!openersAreNamedButtons) {
    throw new Error("both openers must be buttons with an accessible name");
}
// ...and neither is bound to a gesture (SC 2.1.1).
for (const [name, node] of [["menu", menu], ["desk", desk]]) {
    for (const gesture of ["touchstart", "pointerdown", "mousedown", "touchend"]) {
        if ((node.listeners[gesture] || []).length) {
            throw new Error(`the ${name} button binds ${gesture}`);
        }
    }
}

// ─── 2. Opening MOVES the live column into the modal ───

await_(menu.dispatchClick());
const panel = documentStub.body.querySelector(".responsive-panel");
if (!panel) throw new Error("the drawer did not open a modal");
if (!panel.contains(roster)) throw new Error("the drawer must hold the live roster");
if (layout.children.includes(roster)) throw new Error("the roster is in two places at once");
// The same element, with what it was holding: a rebuild would have lost this.
const presentsTheLiveColumn = panel.querySelectorAll(".roster-mark").length === 1;
if (!presentsTheLiveColumn) throw new Error("the drawer rebuilt the roster instead of moving it");
// The trap is core/overlays.js's, not a third implementation.
if (panel.getAttribute("role") !== "dialog") throw new Error("the panel must be a dialog");
if (panel.getAttribute("aria-modal") !== "true") throw new Error("the panel must be modal");
if (panel.getAttribute("data-size") !== "panel") throw new Error("a presented column is a panel modal");
if (!panel.querySelector(".modal-close")) throw new Error("the panel must be dismissible");

// ─── 3. Only one panel at a time, and closing restores the grid order ───

await_(desk.dispatchClick());
const panels = documentStub.body.querySelectorAll(".responsive-panel");
const onePanelAtATime = panels.length === 1 && panels[0].contains(context)
    && panels[0].getAttribute("aria-label") === "Office";
if (!onePanelAtATime) throw new Error(`expected one panel holding the context, got ${panels.length}`);
// Opening the second put the first back where the grid expects it: roster,
// place — appending blindly would have left the roster to the RIGHT of centre.
const order = layout.children.map((child) => child.className);
if (order.join("|") !== "app-roster|app-place") {
    throw new Error(`the roster was restored out of order: ${order.join("|")}`);
}

// Esc / the close button puts the context back as the LAST child.
panels[0].querySelector(".modal-close").click();
const restored = layout.children.map((child) => child.className);
const restoresColumnOrder = restored.join("|") === "app-roster|app-place|app-context";
if (!restoresColumnOrder) throw new Error(`bad restore order: ${restored.join("|")}`);
if (documentStub.body.querySelectorAll(".responsive-panel").length !== 0) {
    throw new Error("closing must leave no panel behind");
}
if (context.querySelectorAll(".context-mark").length !== 1) {
    throw new Error("the context column lost its contents across the round trip");
}

// ─── 4. Widening the window puts the column back on its own ───

await_(desk.dispatchClick());
if (!documentStub.body.querySelector(".responsive-panel")) throw new Error("expected a panel");
// The MEDIUM query stops matching: there is room for the column again, and
// leaving the panel up would show it twice.
const medium = global.window.matchMedia(BossModResponsive.MEDIUM);
medium._emit(false);
const widenClosesThePanel =
    documentStub.body.querySelectorAll(".responsive-panel").length === 0
    && layout.children.map((c) => c.className).join("|") === "app-roster|app-place|app-context";
if (!widenClosesThePanel) throw new Error("crossing the breakpoint must return the column to the grid");

// ─── 5. openRoster() is the ☰'s own door ───

mounted.openRoster();
const rosterPanel = documentStub.body.querySelector(".responsive-panel");
const openRosterPresentsTheRoster = Boolean(rosterPanel) && rosterPanel.contains(roster)
    && rosterPanel.getAttribute("aria-label") === "People and threads"
    && rosterPanel.querySelectorAll(".roster-mark").length === 1;
if (!openRosterPresentsTheRoster) throw new Error("openRoster() must present the live roster");

// ─── 6. Picking a conversation closes the drawer; the context panel stays ───

store.setState({ conversationKind: "agent", conversationId: "a1" });
const conversationClosesTheRoster =
    documentStub.body.querySelectorAll(".responsive-panel").length === 0
    && layout.children.map((c) => c.className).join("|") === "app-roster|app-place|app-context";
if (!conversationClosesTheRoster) {
    throw new Error("choosing a conversation must close the drawer and restore the roster");
}
await_(desk.dispatchClick());
store.setState({ conversationKind: "thread", conversationId: "t1" });
const contextPanels = documentStub.body.querySelectorAll(".responsive-panel");
const conversationLeavesTheContext = contextPanels.length === 1 && contextPanels[0].contains(context);
if (!conversationLeavesTheContext) {
    throw new Error("a conversation change must not close the office summary");
}
contextPanels[0].querySelector(".modal-close").click();

// ─── 7. The Office button is only offered where a context column exists ───

if (!desk.classList.contains("is-available")) throw new Error("Chat has a context column");
const noContextPlace = BossModPlaces.PLACE_IDS.find(
    (id) => BossModPlaces.get(id).hasContext !== true);
if (!noContextPlace) throw new Error("every place claims a context column");
store.setState({ place: noContextPlace });
const hiddenWhereThereIsNoContext = !desk.classList.contains("is-available");
if (!hiddenWhereThereIsNoContext) {
    throw new Error(`the Office button is offered on ${noContextPlace}, which has no context column`);
}

destroy();
if (header.querySelectorAll(".responsive-menu-btn").length !== 0) {
    throw new Error("destroy must remove the buttons it added");
}

process.stdout.write(JSON.stringify({
    ok: true,
    openersAreNamedButtons,
    contextOpenerSaysOffice,
    presentsTheLiveColumn,
    onePanelAtATime,
    restoresColumnOrder,
    widenClosesThePanel,
    hiddenWhereThereIsNoContext,
    openRosterPresentsTheRoster,
    conversationClosesTheRoster,
    conversationLeavesTheContext,
}));

/** The fake DOM's click handlers are async; this harness is straight-line. */
function await_(promise) {
    if (promise && typeof promise.then === "function") {
        let settled = false;
        promise.then(() => { settled = true; });
        // Every handler here is synchronous under the hood, so the microtask
        // queue is already drained by the time the next statement runs; this
        // only makes a rejection loud instead of silent.
        promise.catch((err) => { throw err; });
        void settled;
    }
}
