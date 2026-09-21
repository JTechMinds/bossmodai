/**
 * Node harness: the Tasks place, mounted against the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * The data layer's rules are checked first on small fixtures of their own.
 * Then the real place is mounted, because most of what matters here — that
 * every status lands somewhere, that Done counts only `complete`, that the
 * Done window and the Archive split finished work between them, that a
 * refresh does not eat the search box or the selection — is a property of how
 * the modules are wired together. A stub for any of them would prove nothing.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

installDom();
installIconsStub();
global.lucide = null;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModFactList", "BossModAvatar", "BossModSwitch", "BossModStore",
    "BossModBus", "BossModFormat", "BossModSpecialty", "BossModGates",
    "BossModOverlayFocus", "BossModOverlays", "BossModPlaces", "BossModAgentRoutes",
    "BossModTasksColumns", "BossModTasksData", "BossModTasksGrid", "BossModTaskCard",
    "BossModTaskDeliverables", "BossModTaskEvents", "BossModTaskDetailSections",
    "BossModTaskDetail",
    "BossModAssignOutcomes", "BossModAssignForm", "BossModTasksCancel",
    "BossModTasksMenu", "BossModTasksArchive", "BossModTasksToolbar", "BossModTasksPlace",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

// Read off `global`, never destructured into a module-scope const: such a
// const sits in its temporal dead zone while the evals above run, and the
// eval'd sources reference these names at load time.

// Local calendar anchors, read off the real clock. The Done window is a
// rolling cutoff and the day labels are local calendar days, so "today" has
// to be a moment that is both inside the window and on today's date: a
// minute ago, unless that fell before local midnight.
const NOW = new Date();
const [Y, M, D] = [NOW.getFullYear(), NOW.getMonth(), NOW.getDate()];
const startOfToday = new Date(Y, M, D).getTime();
const todayIso = new Date(Math.max(startOfToday + 1000, NOW.getTime() - 60000)).toISOString();
const yesterdayIso = new Date(Y, M, D - 1, 12).toISOString();
const fiveDaysIso = new Date(Y, M, D - 5, 12).toISOString();
const thirtyDaysIso = new Date(Y, M, D - 30, 12).toISOString();
const FINISHED = new Set(["complete", "abandoned", "declined", "cancelled"]);

// One task per TaskStatus member. Eleven of them: if the column map ever loses
// one, it stops rendering here and everyStatusLands goes false. Every finished
// one ended today, so it is inside any Done window.
const STATUSES = [
    "pending", "accepted", "active", "waiting", "blocked", "complete",
    "stalled", "abandoned", "delegated", "declined", "cancelled",
];
const TASKS = STATUSES.map((status, index) => ({
    id: `t-${status}`,
    title: `Task ${status}`,
    status,
    assigned_to: "a1",
    assigned_to_name: "Jim",
    parent_task_id: null,
    last_activity: `2026-09-0${(index % 9) + 1}T10:00:00Z`,
    closed_at: FINISHED.has(status) ? todayIso : null,
}));

// Finished work spread across the calendar, and a second backlog task that
// moved earlier than the first, so the sort has something to reverse.
const person = { assigned_to: "a1", assigned_to_name: "Jim", parent_task_id: null };
TASKS.push(
    { ...person, id: "t-complete-yesterday", title: "Task complete yesterday", status: "complete",
        last_activity: yesterdayIso, closed_at: yesterdayIso },
    { ...person, id: "t-complete-5d", title: "Task complete five days ago", status: "complete",
        last_activity: fiveDaysIso, closed_at: fiveDaysIso },
    { ...person, id: "t-complete-old", title: "Task complete a month ago", status: "complete",
        last_activity: thirtyDaysIso, closed_at: thirtyDaysIso },
    { ...person, id: "t-pending-older", title: "Task pending older", status: "pending",
        last_activity: "2026-08-20T10:00:00Z", closed_at: null },
);
// A task that came from a thread goes back to it; one from a DM goes to its
// assignee. t-stalled keeps the base fixture's assignee and no channel.
Object.assign(TASKS.find((task) => task.id === "t-blocked"),
    { source_channel: "channel", notification_channel_id: "th-1" });

// What GET /api/needs would return for the same fixture: api/routes/needs.py
// emits one blocked-kind need per task in BLOCKED_STATUSES, keyed by task id.
const BLOCKED_KIND_NEEDS = TASKS
    .filter((task) => task.status === "blocked" || task.status === "stalled")
    .map((task) => ({ id: task.id, kind: "blocked", agentId: task.assigned_to }));

let taskFetches = 0;

function api(url) {
    if (String(url).includes("/events")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }
    if (url.startsWith("/api/tasks")) {
        taskFetches += 1;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(TASKS) });
    }
    return Promise.resolve({
        ok: true, json: () => Promise.resolve([]), text: () => Promise.resolve(""),
    });
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fire(el, type, event) {
    (el.listeners[type] || []).forEach((fn) => fn(event || { target: el }));
}

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

/**
 * The data layer's rules, checked on small fixtures of their own before the
 * place is mounted: what the columns and counts SHOULD be is decided here,
 * and the mounted place is then checked against the same functions.
 *
 * @returns {object} One boolean per rule.
 */
function pureChecks() {
    const DATA = global.BossModTasksData;
    const now = NOW.getTime();
    const finished = (id, status, closedAt) => ({
        id, title: id, status, closed_at: closedAt, last_activity: todayIso,
    });
    const ids = (list) => list.map((task) => task.id).sort().join(",");

    const byAge = [
        finished("today", "complete", todayIso),
        finished("yesterday", "complete", yesterdayIso),
        finished("five", "complete", fiveDaysIso),
        finished("thirty", "complete", thirtyDaysIso),
    ];
    const week = DATA.groupIntoColumns(byAge, { windowDays: 7, now });
    const three = DATA.groupIntoColumns(byAge, { windowDays: 3, now });
    const windowSplitsDone = ids(week.columns.done) === "five,today,yesterday"
        && ids(week.older) === "thirty"
        && ids(three.columns.done) === "today,yesterday"
        && ids(three.older) === "five,thirty";
    if (!windowSplitsDone) {
        fail(`window split wrong: 7d done [${ids(week.columns.done)}] older [${ids(week.older)}], `
            + `3d done [${ids(three.columns.done)}] older [${ids(three.older)}]`);
    }

    const undatedGroup = DATA.groupIntoColumns(
        [finished("undated", "complete", null)], { windowDays: 7, now });
    const undatedIsReported = ids(undatedGroup.undated) === "undated"
        && undatedGroup.columns.done.length === 0 && undatedGroup.older.length === 0;
    if (!undatedIsReported) fail("a finished task with no closed_at was not reported as undated");

    const bogus = DATA.groupIntoColumns(
        [{ id: "bogus", status: "bogus", last_activity: todayIso }], { windowDays: 7, now });
    const unknownStatusIsUnplaced = ids(bogus.unplaced) === "bogus";
    if (!unknownStatusIsUnplaced) fail("an unknown status was not reported as unplaced");

    const mixed = DATA.counts(DATA.groupIntoColumns([
        finished("done", "complete", todayIso),
        finished("cancelled", "cancelled", todayIso),
    ], { windowDays: 7, now }));
    const countsExcludeClosed = mixed.done === 1 && mixed.closed === 1;
    if (!countsExcludeClosed) fail(`counts folded closed into done: ${JSON.stringify(mixed)}`);

    const thread = DATA.chatTargetFor(
        { source_channel: "channel", notification_channel_id: "th-1", assigned_to: "a1" });
    const direct = DATA.chatTargetFor({ source_channel: "chat", assigned_to: "a1" });
    const chatTargetRules = JSON.stringify(thread) === JSON.stringify({ id: "th-1", kind: "thread" })
        && JSON.stringify(direct) === JSON.stringify({ id: "a1", kind: "agent" })
        && DATA.chatTargetFor({}) === null;
    if (!chatTargetRules) fail(`chat targets wrong: ${JSON.stringify([thread, direct])}`);

    // The finished task closed earlier but was touched later; its finish time
    // is what orders it, or a late heartbeat would reshuffle the Done column.
    const sorted = DATA.sortTasks([
        { id: "closed-early", status: "complete", closed_at: fiveDaysIso, last_activity: todayIso },
        { id: "closed-late", status: "complete", closed_at: yesterdayIso, last_activity: fiveDaysIso },
    ], "desc");
    const sortFollowsFinishTime = sorted.map((task) => task.id).join(",") === "closed-late,closed-early";
    if (!sortFollowsFinishTime) fail("sortTasks ordered finished work by last_activity");

    let unknownWindowThrows = false;
    try { DATA.windowFor(5); } catch (err) { unknownWindowThrows = true; }
    if (!unknownWindowThrows) fail("windowFor(5) did not throw");

    return {
        windowSplitsDone,
        undatedIsReported,
        unknownStatusIsUnplaced,
        countsExcludeClosed,
        chatTargetRules,
        sortFollowsFinishTime,
        unknownWindowThrows,
    };
}

/** Click the way the browser does: every listener, awaited, then settle. */
async function click(el, what) {
    if (!el) fail(`nothing to click: ${what}`);
    await el.dispatchClick();
    await drain();
}

/** A column's heading count, as a number. */
function countOf(container, columnId) {
    const column = container.querySelector(`[data-column="${columnId}"]`);
    if (!column) fail(`no ${columnId} column rendered`);
    return Number(column.querySelector(".tasks-column-count").textContent);
}

/** The panels on screen, and the one on top (the only one not hidden). */
function panels() {
    const all = document.body.querySelectorAll(".modal-panel");
    return { all, top: all.filter((panel) => !panel.hidden).pop() || null };
}

function storeFor(placeParams) {
    return global.BossModStore.createStore({
        place: "tasks",
        placeParams,
        conversationId: null,
        conversationKind: null,
        roster: [{ id: "a1", name: "Jim", color: "#3b82f6" }],
        threads: [],
        needs: BLOCKED_KIND_NEEDS,
        runtimePaused: false,
    });
}

async function main() {
    const pure = pureChecks();
    const store = storeFor({});
    const bus = global.BossModBus.createBus(global.BossModBus.KNOWN_TOPICS);
    const container = document.createElement("div");
    document.body.append(container);
    const navigations = [];
    const navigate = (placeId, params) => { navigations.push({ placeId, params }); };

    const place = global.BossModPlaces.get("tasks");
    place.mount(container, { store, bus, api, needs: {}, navigate });
    await drain();

    // ── everyStatusLands ────────────────────────────────────────────────
    // Show subtasks is irrelevant here (no children), so every fixture task
    // but the one a month old is on screen exactly once; that one is Archive's.
    const cards = container.querySelectorAll(".task-card");
    const rendered = new Set(cards.map((card) => card.getAttribute("data-status")));
    const everyStatusLands = rendered.size === STATUSES.length
        && STATUSES.every((status) => rendered.has(status))
        && cards.length === TASKS.length - 1;
    if (!everyStatusLands) {
        fail(`not every status rendered: ${cards.length} cards, statuses ${[...rendered].sort()}`);
    }

    // ── doneExcludesClosed ──────────────────────────────────────────────
    const doneColumn = container.querySelector('[data-column="done"]');
    const doneCards = doneColumn.querySelectorAll(".task-card");
    const doneExcludesClosed = countOf(container, "done") === 3 && doneCards.length === 6;
    if (!doneExcludesClosed) {
        fail(`Done counted ${countOf(container, "done")} over ${doneCards.length} cards`);
    }

    // ── closedCardsAreMarked ────────────────────────────────────────────
    const closedCards = doneCards.filter((card) => card.getAttribute("data-closed") === "true");
    const closedIds = closedCards.map((card) => card.getAttribute("data-task-id")).sort();
    const closedCardsAreMarked = closedIds.join(",") === "t-abandoned,t-cancelled,t-declined"
        && closedCards.every((card) => card.textContent.includes("closed without completing"))
        && doneCards.filter((card) => card.getAttribute("data-status") === "complete")
            .every((card) => !card.hasAttribute("data-closed"));
    if (!closedCardsAreMarked) fail(`closed cards not marked: [${closedIds}]`);

    // ── doneGroupsByDay ─────────────────────────────────────────────────
    const labels = doneColumn.querySelectorAll(".tasks-day-label").map((node) => node.textContent);
    const expectedLabels = ["Today", "Yesterday", global.BossModFormat.formatDayLabel(fiveDaysIso)];
    const doneGroupsByDay = labels.join("|") === expectedLabels.join("|");
    if (!doneGroupsByDay) fail(`Done day labels were [${labels}], wanted [${expectedLabels}]`);

    // ── summaryCountsTheWindow ──────────────────────────────────────────
    const summaryEl = container.querySelector(".place-summary");
    const summaryCountsTheWindow = summaryEl.textContent === "8 active · 3 done this week";
    if (!summaryCountsTheWindow) fail(`summary read "${summaryEl.textContent}"`);

    // ── needsColumnMatchesQueue ─────────────────────────────────────────
    // The column reads task status; the queue's blocked kind comes from the
    // same statuses server-side. If they can disagree, one is lying.
    const needsColumn = container.querySelector('[data-column="needs"]');
    const shownIds = needsColumn.querySelectorAll(".task-card")
        .map((card) => card.getAttribute("data-task-id")).sort();
    const queuedIds = store.getState().needs
        .filter((need) => need.kind === "blocked")
        .map((need) => need.id).sort();
    const needsColumnMatchesQueue = countOf(container, "needs") === queuedIds.length
        && shownIds.join(",") === queuedIds.join(",");
    if (!needsColumnMatchesQueue) {
        fail(`Needs you shows [${shownIds}] but the queue holds [${queuedIds}]`);
    }

    // ── cancelHiddenUntilSelected, before ───────────────────────────────
    const cancelButton = container.querySelector(".tasks-danger");
    if (!cancelButton) fail("no bulk cancel button rendered");
    const cancelHiddenAtRest = cancelButton.hidden === true;
    if (!cancelHiddenAtRest) fail("Cancel selected is visible with nothing selected");

    // ── olderOpensArchive ───────────────────────────────────────────────
    const older = doneColumn.querySelector(".tasks-older");
    if (!older || older.textContent !== "1 older") {
        fail(`the older link read "${older && older.textContent}"`);
    }
    await click(older, ".tasks-older");
    const archivePanel = panels().all.find((panel) => panel.getAttribute("aria-label") === "Archive");
    const olderOpensArchive = Boolean(archivePanel)
        && Boolean(archivePanel.querySelector('[data-task-id="t-complete-old"]'));
    if (!olderOpensArchive) fail("the older link did not open Archive with the month-old task");

    // ── archiveOpensTaskAsLayer ─────────────────────────────────────────
    const archivedCard = archivePanel.querySelector('[data-task-id="t-complete-old"]');
    await click(archivedCard.querySelector(".task-card-open"), "the archived card");
    const layered = panels();
    const back = layered.top && layered.top.querySelector(".modal-back");
    const opensAsLayer = Boolean(layered.top)
        && layered.top.getAttribute("aria-label") === "Task complete a month ago"
        && archivePanel.hidden === true
        && Boolean(back) && back.hidden === false;
    await click(layered.top && layered.top.querySelector(".modal-close"), "the top ✕");
    const archiveOpensTaskAsLayer = opensAsLayer && panels().all.length === 0;
    if (!archiveOpensTaskAsLayer) {
        fail(`an archived task did not open as a layer: top "${layered.top
            && layered.top.getAttribute("aria-label")}", ${panels().all.length} panels left`);
    }

    // ── windowMovesDone ─────────────────────────────────────────────────
    await click(container.querySelector("#tasks-options"), "#tasks-options");
    await click(container.querySelector("#tasks-window-3"), "#tasks-window-3");
    const olderAfter = container.querySelector(".tasks-older");
    const windowMovesDone = countOf(container, "done") === 2
        && Boolean(olderAfter) && olderAfter.textContent === "2 older"
        && container.querySelector(".place-summary").textContent.endsWith("2 done in the last 3 days")
        && container.querySelector("#tasks-window-3").getAttribute("aria-pressed") === "true";
    if (!windowMovesDone) {
        fail(`the 3d window left Done at ${countOf(container, "done")}, `
            + `summary "${container.querySelector(".place-summary").textContent}"`);
    }

    // ── sortReverses ────────────────────────────────────────────────────
    const backlogIds = () => container.querySelector('[data-column="backlog"]')
        .querySelectorAll(".task-card").map((card) => card.getAttribute("data-task-id")).join(",");
    const newestFirst = backlogIds();
    const sortButton = container.querySelector("#tasks-sort");
    if (!sortButton || sortButton.textContent !== "Sort: newest first") {
        fail(`the sort action read "${sortButton && sortButton.textContent}"`);
    }
    await click(sortButton, "#tasks-sort");
    const sortReverses = newestFirst === "t-pending,t-pending-older"
        && backlogIds() === "t-pending-older,t-pending"
        && container.querySelector("#tasks-sort").textContent === "Sort: oldest first";
    if (!sortReverses) fail(`backlog went from [${newestFirst}] to [${backlogIds()}]`);
    // Put the menu away through its own toggle, as the operator would.
    await click(container.querySelector("#tasks-options"), "#tasks-options");
    if (container.querySelector("#tasks-sort")) fail("the ⋯ did not close its panel");

    // ── searchSurvivesRefresh ───────────────────────────────────────────
    const search = container.querySelector(".tasks-search");
    if (!search) fail("no search input rendered");
    search.value = "blocked";
    const searchBefore = search;
    const fetchesBefore = taskFetches;

    // A task event arrives while the operator is typing.
    bus.publish("activity", { event: "status_changed", title: "a task moved" });
    await sleep(600);
    await drain();
    if (taskFetches <= fetchesBefore) fail("a task event did not refresh the list");

    const searchAfter = container.querySelector(".tasks-search");
    const searchSurvivesRefresh = searchAfter === searchBefore && searchAfter.value === "blocked";
    if (!searchSurvivesRefresh) fail("the refresh replaced the search input or its value");

    // The filter it holds is applied, so the refresh repainted through it.
    const filtered = container.querySelectorAll(".task-card")
        .map((card) => card.getAttribute("data-task-id"));
    if (filtered.join(",") !== "t-blocked") {
        fail(`the search filter was not applied on refresh: [${filtered}]`);
    }

    // ── selectionSurvivesRefresh, and cancelHiddenUntilSelected ─────────
    // Clear the filter through the real debounced path, so the repaint that
    // brings the other cards back is the one the operator would trigger.
    search.value = "";
    fire(search, "input", { target: search });
    await sleep(250);
    await drain();

    const box = container.querySelector('[data-select-task="t-active"]');
    if (!box) fail("no select checkbox on an open task");
    box.checked = true;
    fire(box, "change", { target: box });

    const cancelHiddenUntilSelected = cancelHiddenAtRest
        && cancelButton.hidden === false
        && cancelButton.textContent === "Cancel selected (1)";
    if (!cancelHiddenUntilSelected) {
        fail(`selection did not reach the toolbar: hidden=${cancelButton.hidden} `
            + `"${cancelButton.textContent}"`);
    }

    bus.publish("activity", { event: "task_created", title: "another task" });
    await sleep(600);
    await drain();

    const reselected = container.querySelector('[data-task-id="t-active"]');
    const selectionSurvivesRefresh = cancelButton.textContent === "Cancel selected (1)"
        && reselected.getAttribute("class").includes("is-selected");
    if (!selectionSurvivesRefresh) {
        fail(`selection lost on refresh: "${cancelButton.textContent}"`);
    }

    // ── needsCardOpensChat, threadTaskOpensThread ───────────────────────
    const chatButton = (taskId) => {
        const card = container.querySelector(`[data-task-id="${taskId}"]`);
        const actions = card && card.querySelector(".task-card-actions");
        return actions && actions.querySelector("button");
    };
    await click(chatButton("t-stalled"), "t-stalled's Open chat");
    const afterDm = store.getState();
    const needsCardOpensChat = afterDm.conversationId === "a1"
        && afterDm.conversationKind === "agent"
        && navigations.some((entry) => entry.placeId === "chat");
    if (!needsCardOpensChat) {
        fail(`Open chat left ${afterDm.conversationKind}:${afterDm.conversationId}, `
            + `navigations ${JSON.stringify(navigations)}`);
    }
    await click(chatButton("t-blocked"), "t-blocked's Open chat");
    const afterThread = store.getState();
    const threadTaskOpensThread = afterThread.conversationId === "th-1"
        && afterThread.conversationKind === "thread";
    if (!threadTaskOpensThread) {
        fail(`a thread task opened ${afterThread.conversationKind}:${afterThread.conversationId}`);
    }

    // ── refetchesOnResync ───────────────────────────────────────────────
    // Every broadcast during an outage is lost with no replay buffer, so a
    // reconnect has to re-read rather than assume continuity (spec 1.4).
    const beforeResync = taskFetches;
    bus.publish("resync", { downtimeMs: 4000 });
    await drain();
    const refetchesOnResync = taskFetches > beforeResync;
    if (!refetchesOnResync) fail("resync did not refetch the list");

    // Draining the place must take the resync subscription with it.
    place.unmount();
    const afterUnmount = taskFetches;
    bus.publish("resync", { downtimeMs: 4000 });
    await drain();
    if (taskFetches !== afterUnmount) fail("the resync subscription outlived the place");

    // ── opensLinkedTask ─────────────────────────────────────────────────
    // Blocked needs and Created/Accepted notes navigate here with { taskId }.
    const linkedStore = storeFor({ taskId: "t-accepted" });
    const linkedContainer = document.createElement("div");
    document.body.append(linkedContainer);
    place.mount(linkedContainer, { store: linkedStore, bus, api, needs: {}, navigate });
    await drain();
    const sheet = document.body.querySelector(".modal-panel");
    const opensLinkedTask = Boolean(sheet)
        && String(sheet.getAttribute("aria-label") || "") === "Task accepted";
    if (!opensLinkedTask) {
        fail(`placeParams.taskId did not open the task: aria-label="${sheet && sheet.getAttribute("aria-label")}"`);
    }
    place.unmount();

    process.stdout.write(JSON.stringify({
        ok: true,
        ...pure,
        everyStatusLands,
        doneExcludesClosed,
        closedCardsAreMarked,
        doneGroupsByDay,
        summaryCountsTheWindow,
        cancelHiddenUntilSelected,
        olderOpensArchive,
        archiveOpensTaskAsLayer,
        windowMovesDone,
        sortReverses,
        needsCardOpensChat,
        threadTaskOpensThread,
        searchSurvivesRefresh,
        selectionSurvivesRefresh,
        needsColumnMatchesQueue,
        refetchesOnResync,
        opensLinkedTask,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
