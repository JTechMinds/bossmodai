/**
 * Node harness: the Board's four behavioural promises.
 *
 * Invoked by tests/test_ui_board.py. Not a browser bundle.
 *
 * It mounts the real place against the real modules, because three of the four
 * properties — that every status lands somewhere, that a refresh does not eat
 * the search box, and that a refresh does not eat the selection — are
 * properties of how those modules are wired together. A stub for any of them
 * would prove nothing.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();
global.lucide = null;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModFormat", "BossModSpecialty", "BossModGates",
    "BossModOverlays", "BossModPlaces",
    "BossModBoardColumns", "BossModBoardData", "BossModBoardGrid", "BossModTaskCard",
    "BossModTaskDeliverables", "BossModTaskEvents", "BossModTaskDetail",
    "BossModAssignOutcomes", "BossModAssignForm", "BossModBoardCancel",
    "BossModBoardToolbar", "BossModBoardPlace",
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

// One task per TaskStatus member. Eleven of them: if the column map ever loses
// one, it stops rendering here and everyStatusLands goes false.
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
}));

// What GET /api/needs would return for the same fixture: api/routes/needs.py
// emits one blocked-kind need per task in BLOCKED_STATUSES, keyed by task id.
const BLOCKED_KIND_NEEDS = TASKS
    .filter((task) => task.status === "blocked" || task.status === "stalled")
    .map((task) => ({ id: task.id, kind: "blocked", agentId: task.assigned_to }));

let taskFetches = 0;

function api(url) {
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

async function main() {
    const store = global.BossModStore.createStore({
        place: "board",
        placeParams: {},
        conversationId: null,
        conversationKind: null,
        roster: [],
        threads: [],
        needs: BLOCKED_KIND_NEEDS,
        runtimePaused: false,
    });
    const bus = global.BossModBus.createBus(global.BossModBus.KNOWN_TOPICS);
    const container = document.createElement("div");
    document.body.append(container);

    const place = global.BossModPlaces.get("board");
    place.mount(container, { store, bus, api, needs: {}, navigate: () => {} });
    await drain();

    // ── everyStatusLands ────────────────────────────────────────────────
    // Show subtasks is irrelevant here (no children), so every fixture task
    // must be on screen exactly once, across the columns and the disclosure.
    const cards = container.querySelectorAll(".task-card");
    const rendered = cards.map((card) => card.getAttribute("data-status")).sort();
    const everyStatusLands = rendered.length === STATUSES.length
        && rendered.join(",") === STATUSES.slice().sort().join(",");
    if (!everyStatusLands) {
        fail(`not every status rendered: got ${rendered.join(",")}`);
    }

    // ── doneExcludesClosed ──────────────────────────────────────────────
    const doneColumn = container.querySelector('[data-column="done"]');
    if (!doneColumn) fail("no Done column rendered");
    const doneCount = Number(doneColumn.querySelector(".board-column-count").textContent);
    const disclosure = doneColumn.querySelector(".board-closed");
    if (!disclosure) fail("the Done column rendered no disclosure");
    if (disclosure.tagName !== "DETAILS") fail("the closed rows are not in a details element");
    const summary = disclosure.querySelector(".board-closed-summary").textContent;
    const closedCount = global.BossModBoardColumns.CLOSED_WITHOUT_COMPLETING.length;
    const doneExcludesClosed = doneCount === 1
        && summary === `${global.BossModBoardColumns.CLOSED_LABEL} (${closedCount})`
        && disclosure.querySelectorAll(".task-card").length === closedCount;
    if (!doneExcludesClosed) {
        fail(`Done counted ${doneCount} with summary "${summary}"`);
    }

    // ── needsColumnMatchesQueue ─────────────────────────────────────────
    // The column reads task status; the queue's blocked kind comes from the
    // same statuses server-side. If they can disagree, one is lying.
    const needsColumn = container.querySelector('[data-column="needs"]');
    const needsCount = Number(needsColumn.querySelector(".board-column-count").textContent);
    const shownIds = needsColumn.querySelectorAll(".task-card")
        .map((card) => card.getAttribute("data-task-id")).sort();
    const queuedIds = store.getState().needs
        .filter((need) => need.kind === "blocked")
        .map((need) => need.id).sort();
    const needsColumnMatchesQueue = needsCount === queuedIds.length
        && shownIds.join(",") === queuedIds.join(",");
    if (!needsColumnMatchesQueue) {
        fail(`Needs you shows [${shownIds}] but the queue holds [${queuedIds}]`);
    }

    // ── searchSurvivesRefresh ───────────────────────────────────────────
    const search = container.querySelector(".board-search");
    if (!search) fail("no search input rendered");
    search.value = "blocked";
    const searchBefore = search;
    const fetchesBefore = taskFetches;

    // A task event arrives while the operator is typing.
    bus.publish("activity", { event: "status_changed", title: "a task moved" });
    await sleep(600);
    await drain();
    if (taskFetches <= fetchesBefore) fail("a task event did not refresh the board");

    const searchAfter = container.querySelector(".board-search");
    const searchSurvivesRefresh = searchAfter === searchBefore && searchAfter.value === "blocked";
    if (!searchSurvivesRefresh) fail("the refresh replaced the search input or its value");

    // The filter it holds is applied, so the refresh repainted through it.
    const filtered = container.querySelectorAll(".task-card")
        .map((card) => card.getAttribute("data-task-id"));
    if (filtered.join(",") !== "t-blocked") {
        fail(`the search filter was not applied on refresh: [${filtered}]`);
    }

    // ── selectionSurvivesRefresh ────────────────────────────────────────
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

    const cancelButton = container.querySelectorAll(".board-danger")[0];
    if (cancelButton.textContent !== "Cancel selected (1)") {
        fail(`selection did not reach the toolbar: "${cancelButton.textContent}"`);
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

    // ── refetchesOnResync ───────────────────────────────────────────────
    // Every broadcast during an outage is lost with no replay buffer, so a
    // reconnect has to re-read rather than assume continuity (spec 1.4).
    const beforeResync = taskFetches;
    bus.publish("resync", { downtimeMs: 4000 });
    await drain();
    const refetchesOnResync = taskFetches > beforeResync;
    if (!refetchesOnResync) fail("resync did not refetch the board");

    // Draining the place must take the resync subscription with it.
    place.unmount();
    const afterUnmount = taskFetches;
    bus.publish("resync", { downtimeMs: 4000 });
    await drain();
    if (taskFetches !== afterUnmount) fail("the resync subscription outlived the place");

    process.stdout.write(JSON.stringify({
        ok: true,
        everyStatusLands,
        doneExcludesClosed,
        searchSurvivesRefresh,
        selectionSurvivesRefresh,
        needsColumnMatchesQueue,
        refetchesOnResync,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
