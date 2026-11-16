/**
 * Node harness: the header floor switcher, the floor settings its `⋯` opens,
 * and the Delete floor layer over them.
 *
 * Checks what renders and what a click does, which the source text cannot
 * show: that the panel hangs off the switcher's own positioned host (the
 * header-collapse RCA — it used to be appended to <body>), that every floor is
 * a row with its agent count and the current one is pressed, that picking a
 * row moves the operator, that `+ New floor` turns into a field in place and
 * Esc there goes back to the door without closing the panel, that a failed
 * floor load is shown on the trigger, that the Delete layer holds its
 * confirm until the operator has said what happens to the agents, and that a
 * `floors_updated` broadcast (a change made in another window) repaints the
 * trigger, closes a stale panel, and moves the operator to Lobby when their
 * floor is gone. A floor suspended elsewhere leaves the rows for the
 * "Suspended floors" door, which shows only while one is suspended and opens
 * the view that resumes them (shell/floor-suspended.js): busy while a resume
 * runs, its error on its own row with no reload, a landed resume reloading the
 * list so its row leaves, a failed reload said on the row, and rows that leave
 * live with the broadcast.
 *
 * Invoked by tests/test_ui_floor_switcher.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const NAMES = [
    "BossModDom", "BossModStore", "BossModBus", "BossModFormat", "BossModAvatar", "BossModSearchField",
    "BossModInlineRename",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuButton", "BossModAgentApi",
    "BossModFloorScope", "BossModFloorApi", "BossModFloorDelete", "BossModFloorSuspend", "BossModFloorPicker",
    "BossModFloorMoveConfirm", "BossModFloorPeople", "BossModFloorThreads", "BossModFloorProjects",
    "BossModFloorSettings", "BossModFloorSuspended", "BossModFloorSwitcher",
];
if (process.argv.length - 2 !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${process.argv.length - 2}`);
}
process.argv.slice(2).forEach((path, index) => {
    eval(`${fs.readFileSync(path, "utf8")}\n;global.${NAMES[index]} = ${NAMES[index]};\n`);
});

const settled = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 6; i += 1) await settled(); };

function response(status, body) {
    const text = JSON.stringify(body);
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(JSON.parse(text)),
        text: () => Promise.resolve(text),
    };
}

let floors = [
    { id: "lobby", name: "Lobby", has_folder: true, suspended_at: null },
    { id: "fin", name: "Finance", has_folder: true, suspended_at: null },
];
let failFloors = false;
const calls = [];
// POST /api/floors/{id}/resume answers, per floor id, in order: a response or
// a promise of one (to hold a resume in flight).
const resumeAnswers = {};
function apiFetch(url, init) {
    const method = (init && init.method) || "GET";
    calls.push({ url, method, body: init && init.body ? JSON.parse(init.body) : null });
    if (url === "/api/floors" && method === "GET") {
        return Promise.resolve(failFloors ? response(500, { detail: "boom" }) : response(200, floors));
    }
    // What the floor settings read when they open.
    if (url === "/api/channels?status=active" && method === "GET") {
        return Promise.resolve(response(200, [
            { id: "t1", name: "Books", status: "active", floor_id: "fin", members: [{ id: "a3", name: "Cy" }] },
        ]));
    }
    if (/^\/api\/floors\/[^/]+\/projects$/.test(url) && method === "GET") {
        return Promise.resolve(response(200, []));
    }
    if (url === "/api/floors" && method === "POST") {
        const created = { id: "ops", name: JSON.parse(init.body).name, suspended_at: null };
        floors = floors.concat([created]);
        return Promise.resolve(response(201, created));
    }
    const resume = /^\/api\/floors\/([^/]+)\/resume$/.exec(url);
    if (resume && method === "POST" && (resumeAnswers[resume[1]] || []).length) {
        // The server's list follows a resume that lands, so a reload reads it.
        return Promise.resolve(resumeAnswers[resume[1]].shift()).then((answer) => {
            if (answer.ok) {
                floors = floors.map((floor) => (floor.id === resume[1] ? { ...floor, suspended_at: null } : floor));
            }
            return answer;
        });
    }
    return Promise.reject(new Error(`unexpected request ${method} ${url}`));
}

function keydown(el, key) {
    const event = {
        key,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
        target: el,
    };
    (el.listeners.keydown || []).forEach((fn) => fn(event));
    return event;
}

async function submit(form) {
    const event = { preventDefault() {}, stopPropagation() {}, target: form };
    for (const fn of [...(form.listeners.submit || [])]) await fn(event);
}

(async () => {
    const verdict = {};
    const store = BossModStore.createStore({
        currentFloorId: "lobby",
        floors: [{ id: "lobby", name: "Lobby" }],
        roster: [
            { id: "a1", name: "Ada", floor_id: "lobby" },
            { id: "a2", name: "Bob", floor_id: "lobby" },
            { id: "a3", name: "Cy", floor_id: "fin" },
        ],
        threads: [{ id: "t1", name: "Books", status: "active", floor_id: "fin" }],
    });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const header = documentStub.createElement("header");
    documentStub.body.append(header);
    let mountWithoutBusThrows = false;
    try { BossModFloorSwitcher.mount({ store, apiFetch }); } catch (err) { mountWithoutBusThrows = true; }
    verdict.mountRequiresABus = mountWithoutBusThrows;
    // The Settings takeover: moving to another floor leaves it. Always "open"
    // here, so every close() a floor move makes is counted.
    const takeover = { closes: 0, isOpen: () => true, close() { this.closes += 1; } };
    let mountWithoutTakeoverThrows = false;
    try {
        BossModFloorSwitcher.mount({ store, apiFetch, bus });
    } catch (err) {
        mountWithoutTakeoverThrows = /deps\.takeover is required/.test(String(err && err.message));
    }
    verdict.mountRequiresATakeover = mountWithoutTakeoverThrows;
    const switcher = BossModFloorSwitcher.mount({ store, apiFetch, bus, takeover });
    header.append(switcher.element);
    await drain();

    const trigger = switcher.element.querySelector(".floor-switcher-trigger");
    verdict.triggerNamesTheCurrentFloor = trigger.getAttribute("aria-label") === "Floor: Lobby"
        && trigger.getAttribute("aria-haspopup") === "dialog"
        && trigger.getAttribute("aria-expanded") === "false";
    verdict.floorsLoadIntoTheStore = store.getState().floors.length === 2;

    // ─── The panel hangs off the positioned host, never <body> ───
    await trigger.dispatchClick();
    let panel = switcher.element.querySelector(".menu");
    verdict.panelHangsOffTheSwitcherHost = Boolean(panel)
        && panel.parentNode === switcher.element
        && documentStub.body.children.every((child) => child !== panel)
        && panel.getAttribute("data-menu") === "floor"
        && trigger.getAttribute("aria-expanded") === "true";

    const choices = panel.querySelectorAll(".floor-choice");
    verdict.everyFloorIsARowWithItsCount = choices.length === 2
        && choices[0].getAttribute("aria-label") === "Lobby, 2 agents"
        && choices[1].getAttribute("aria-label") === "Finance, 1 agent"
        && choices[0].getAttribute("aria-pressed") === "true"
        && choices[1].getAttribute("aria-pressed") === "false"
        && documentStub.activeElement === choices[0];
    verdict.noSuspendedDoorWhileNoneIs = !panel.querySelector(".floor-suspended-door")
        && store.getState().suspendedFloors.length === 0;
    // Each row's `⋯` is the shared inline trigger.
    const more = panel.querySelectorAll(".menu-trigger");
    verdict.everyRowHasANamedSettingsButton = more.length === 2
        && more.every((button) => button.getAttribute("data-size") === "inline")
        && more[1].getAttribute("aria-label") === "Floor settings Finance";

    // ─── Picking a row moves the operator and closes the panel ───
    await choices[1].dispatchClick();
    verdict.pickingARowSetsTheCurrentFloor = store.getState().currentFloorId === "fin"
        && !switcher.element.querySelector(".menu")
        && trigger.getAttribute("aria-label") === "Floor: Finance";
    verdict.pickingAFloorLeavesTheTakeover = takeover.closes === 1;

    // ─── Picking the floor you are on changes nothing, Settings included ───
    await trigger.dispatchClick();
    const pressed = switcher.element.querySelector(".menu").querySelector('[aria-pressed="true"]');
    await pressed.dispatchClick();
    verdict.pickingTheCurrentFloorKeepsTheTakeover = takeover.closes === 1
        && store.getState().currentFloorId === "fin"
        && !switcher.element.querySelector(".menu");

    // ─── + New floor: a field in place; Esc goes back to the door only ───
    await trigger.dispatchClick();
    panel = switcher.element.querySelector(".menu");
    await panel.querySelector(".menu-door").dispatchClick();
    let input = panel.querySelector(".field-input");
    const esc = keydown(input, "Escape");
    verdict.escInTheFieldReturnsToTheDoor = esc.propagationStopped
        && Boolean(switcher.element.querySelector(".menu"))
        && Boolean(panel.querySelector(".menu-door"))
        && !panel.querySelector(".field-input");
    await panel.querySelector(".menu-door").dispatchClick();
    input = panel.querySelector(".field-input");
    input.value = "Ops";
    await submit(panel.querySelector(".floor-new-form"));
    await drain();
    const posted = calls.filter((call) => call.method === "POST");
    verdict.newFloorCreatesAndMovesThere = posted.length === 1
        && posted[0].body.name === "Ops"
        && store.getState().currentFloorId === "ops"
        && store.getState().floors.some((floor) => floor.id === "ops")
        && !switcher.element.querySelector(".menu");
    verdict.newFloorLeavesTheTakeover = takeover.closes === 2;

    // ─── Floor settings, then the Delete layer ───
    store.setState({ currentFloorId: "fin" });
    await trigger.dispatchClick();
    panel = switcher.element.querySelector(".menu");
    const finMore = panel.querySelectorAll(".menu-trigger")
        .find((button) => button.getAttribute("aria-label") === "Floor settings Finance");
    await finMore.dispatchClick();
    await drain();
    const dialogs = () => documentStub.body.querySelectorAll(".modal-panel");
    const edit = dialogs()[0];
    const editInput = edit.querySelector("#floor-settings-name");
    const deleteTool = edit.querySelector(".floor-delete-tool");
    verdict.floorSettingsPrefillTheNameAndOfferDelete = Boolean(edit)
        && edit.getAttribute("aria-label") === "Finance"
        && edit.getAttribute("data-size") === "panel"
        && editInput.value === "Finance"
        && edit.querySelectorAll(".modal-action").length === 0
        && Boolean(deleteTool) && deleteTool.getAttribute("aria-label") === "Delete floor";

    await deleteTool.dispatchClick();
    const layer = dialogs()[1];
    const confirm = layer.querySelector("#floor-delete-confirm");
    const radios = layer.querySelectorAll("input");
    verdict.deleteLayerWaitsForTheAgentsChoice = Boolean(layer)
        && layer.getAttribute("aria-label") === "Delete Finance?"
        && edit.hidden === true
        && radios.length === 2
        && radios.every((radio) => radio.getAttribute("type") === "radio" && !radio.checked)
        && confirm.disabled === true
        && /1 agent works on Finance\. Its 1 thread will be archived\./.test(layer.textContent);
    // Finance has a company folder, so the layer says where its files go.
    verdict.deleteLayerSaysTheFilesAreArchived = /Its files move to Company › Archived floors\./
        .test(layer.textContent);
    // "Delete them" warns what goes and to back it up first (agent-api.js's floor copy).
    const deleteHint = layer.querySelectorAll(".floor-delete-option")[1].querySelector(".field-hint").textContent;
    verdict.deleteThemWarnsToBackUp = deleteHint.includes("back up anything you need")
        && deleteHint.includes("cancels their open tasks");
    // Vacation now ends their work too.
    const homeHint = layer.querySelectorAll(".floor-delete-option")[0].querySelector(".field-hint").textContent;
    verdict.sendThemHomeSaysTheirWorkEnds = homeHint === "They go on vacation: their work stops and their "
        + "open tasks are cancelled. Bring them back from People ⋯ → On vacation.";
    (radios[0].listeners.change || []).forEach((fn) => fn({ target: radios[0] }));
    verdict.choosingEnablesDelete = layer.querySelector("#floor-delete-confirm").disabled === false;
    // ✕ on the top layer closes every layer.
    await layer.querySelector(".modal-close").dispatchClick();

    // ─── Live: another window renamed the floor the operator is on ───
    // The panel is open when it lands; its rows are stale, so it closes.
    await trigger.dispatchClick();
    const openBeforeRename = Boolean(switcher.element.querySelector(".menu"));
    bus.publish("floors_updated", [
        { id: "lobby", name: "Lobby", suspended_at: null },
        { id: "fin", name: "Accounts", suspended_at: null },
        { id: "ops", name: "Ops", suspended_at: null },
    ]);
    await drain();
    verdict.aRenameElsewhereRepaintsTheTrigger = openBeforeRename
        && store.getState().currentFloorId === "fin"
        && trigger.getAttribute("aria-label") === "Floor: Accounts"
        && switcher.element.querySelector(".floor-switcher-name").textContent === "Accounts"
        && !switcher.element.querySelector(".menu")
        && trigger.getAttribute("aria-expanded") === "false";

    // ─── Live: another window deleted it ───
    bus.publish("floors_updated", [
        { id: "lobby", name: "Lobby", suspended_at: null }, { id: "ops", name: "Ops", suspended_at: null },
    ]);
    await drain();
    verdict.aDeleteElsewhereMovesTheOperatorToLobby = store.getState().currentFloorId === "lobby"
        && store.getState().floors.every((floor) => floor.id !== "fin")
        && trigger.getAttribute("aria-label") === "Floor: Lobby";
    // Falling back to Lobby is not the operator choosing to go somewhere.
    verdict.aDeleteElsewhereLeavesTheTakeoverUp = takeover.closes === 2;

    // ─── Live: another window suspended the floor the operator is on ───
    store.setState({ currentFloorId: "ops" });
    const OPS_AT = "2026-11-16T09:30:00+00:00";
    const MKT_AT = "2026-11-15T17:05:00+00:00";
    // What the server now holds: a reload reads this list.
    floors = [
        { id: "lobby", name: "Lobby", suspended_at: null },
        { id: "ops", name: "Ops", suspended_at: OPS_AT },
        { id: "mkt", name: "Marketing", suspended_at: MKT_AT },
    ];
    bus.publish("floors_updated", floors);
    await drain();
    const ids = (rows) => rows.map((floor) => floor.id).join("|");
    verdict.aSuspendElsewhereSplitsTheListAndMovesTheOperatorToLobby = store.getState().currentFloorId === "lobby"
        && ids(store.getState().floors) === "lobby"
        && ids(store.getState().suspendedFloors) === "ops|mkt"
        && trigger.getAttribute("aria-label") === "Floor: Lobby";
    verdict.aSuspendFallbackLeavesTheTakeoverUp = takeover.closes === 2;

    // ─── The panel: active floors are rows; the door, last, has the count ───
    await trigger.dispatchClick();
    panel = switcher.element.querySelector(".menu");
    const door = panel.querySelector(".floor-suspended-door");
    verdict.suspendedFloorsAreADoorNotRows = panel.querySelectorAll(".floor-choice").length === 1
        && Boolean(door)
        && door.classList.contains("menu-door")
        && door.getAttribute("aria-label") === "Suspended floors, 2"
        && door.querySelector(".menu-action-count").textContent === "2"
        && door.querySelector(".menu-action-count").getAttribute("aria-hidden") === "true"
        && door.querySelector("[data-lucide=\"circle-pause\"]") !== null
        && /Suspended floors/.test(door.textContent)
        && panel.children[panel.children.length - 1] === door
        && panel.children[panel.children.length - 2].classList.contains("floor-new");

    // ─── The door opens the Suspended floors view ───
    // It cannot open without the loader a resume reloads through.
    let viewWithoutReloadThrows = false;
    try {
        BossModFloorSuspended.open({ store, floorApi: BossModFloorApi.createFloorApi({ apiFetch }) });
    } catch (err) {
        viewWithoutReloadThrows = /deps\.reloadFloors is required/.test(String(err && err.message));
    }
    verdict.suspendedViewRequiresReloadFloors = viewWithoutReloadThrows
        && documentStub.body.querySelectorAll(".modal-panel").length === 0;
    const storeSubscribers = store.subscriberCount();
    await door.dispatchClick();
    const view = documentStub.body.querySelectorAll(".modal-panel").find((node) => !node.hidden);
    const items = () => view.querySelectorAll(".floor-item");
    const resumeOf = (item) => item.querySelector("button");
    const alertOf = (item) => item.querySelector("[role=\"alert\"]");
    verdict.theDoorClosesThePanelAndOpensTheView = !switcher.element.querySelector(".menu")
        && Boolean(view) && view.getAttribute("aria-label") === "Suspended floors";
    const [opsRow, mktRow] = items();
    verdict.theViewListsEachFloorInTheSettingsRows = Boolean(view.querySelector(".floor-item-list"))
        && items().length === 2
        && items().every((item) => item.parentNode.classList.contains("floor-item-list")
            && Boolean(item.querySelector(".floor-item-who")))
        && items().map((item) => item.querySelector(".floor-item-name").textContent).join("|") === "Ops|Marketing"
        && opsRow.querySelector(".floor-item-meta").textContent
            === `Suspended ${BossModFormat.formatDateTime(OPS_AT)}`
        && BossModFormat.formatDateTime(OPS_AT) !== ""
        && items().map((item) => resumeOf(item).getAttribute("aria-label")).join("|")
            === "Resume Ops|Resume Marketing"
        && items().every((item) => resumeOf(item).textContent === "Resume" && !resumeOf(item).disabled)
        && alertOf(opsRow).textContent === "";

    // ─── Resume: busy while it runs, and a refusal is said on its own row ───
    const floorReads = () => calls.filter((call) => call.url === "/api/floors" && call.method === "GET").length;
    const readsBeforeResume = floorReads();
    let refuse;
    resumeAnswers.ops = [new Promise((resolve) => { refuse = resolve; })];
    await resumeOf(opsRow).dispatchClick();
    verdict.resumeIsBusyWhileItRuns = resumeOf(opsRow).disabled === true
        && resumeOf(mktRow).disabled === false
        && calls.some((call) => call.url === "/api/floors/ops/resume" && call.method === "POST");
    const errorBefore409 = console.error;
    const resumeLogged = [];
    console.error = (...args) => { resumeLogged.push(args.join(" ")); };
    refuse(response(409, { detail: "Ops is not suspended." }));
    await drain();
    console.error = errorBefore409;
    verdict.aFailedResumeSaysWhyOnItsRow = alertOf(opsRow).textContent === "Ops is not suspended."
        && resumeOf(opsRow).disabled === false
        && alertOf(mktRow).textContent === ""
        && items().length === 2
        && resumeLogged.some((line) => line.includes("[floor-suspended] could not resume the floor"))
        // A refused resume changed nothing, so nothing is reloaded.
        && floorReads() === readsBeforeResume;

    // ─── A resume that lands reloads the list, and its row leaves with no broadcast ───
    resumeAnswers.mkt = [response(200, { id: "mkt", name: "Marketing", suspended_at: null })];
    await resumeOf(mktRow).dispatchClick();
    await drain();
    // The kept row is the same node, so its error (and a focus on it) survives.
    verdict.aResumeReloadsTheListAndItsRowLeaves = floorReads() === readsBeforeResume + 1
        && items().length === 1
        && items()[0] === opsRow
        && alertOf(opsRow).textContent === "Ops is not suspended."
        && ids(store.getState().floors) === "lobby|mkt"
        && ids(store.getState().suspendedFloors) === "ops";

    // ─── A resume that lands but whose reload fails says so, and stays done ───
    resumeAnswers.ops = [response(200, { id: "ops", name: "Ops", suspended_at: null })];
    failFloors = true;
    const errorBeforeReload = console.error;
    const reloadLogged = [];
    console.error = (...args) => { reloadLogged.push(args.join(" ")); };
    await resumeOf(opsRow).dispatchClick();
    await drain();
    console.error = errorBeforeReload;
    failFloors = false;
    verdict.aResumeWhoseReloadFailsSaysSoAndStaysDone = floorReads() === readsBeforeResume + 2
        && alertOf(opsRow).textContent === "Resumed, but the floor list could not reload."
        && resumeOf(opsRow).disabled === true
        && items().length === 1
        && trigger.getAttribute("data-error") === "true"
        && reloadLogged.some((line) => line.includes("[floor-switcher] could not load floors"));

    // ─── The broadcast catches up: the last row leaves, the empty state ───
    bus.publish("floors_updated", floors);
    await drain();
    verdict.theLastResumeShowsTheEmptyState = items().length === 0
        && !view.querySelector(".floor-item-list")
        && view.querySelector(".modal-body").textContent === "No floors are suspended."
        && !trigger.hasAttribute("data-error");
    await view.querySelector(".modal-close").dispatchClick();
    verdict.closingTheViewDrainsTheStore = store.subscriberCount() === storeSubscribers
        && documentStub.body.querySelectorAll(".modal-panel").length === 0;
    await trigger.dispatchClick();
    verdict.theDoorLeavesWithTheLastSuspendedFloor = !switcher.element.querySelector(".floor-suspended-door")
        && switcher.element.querySelectorAll(".floor-choice").length === 3;
    await trigger.dispatchClick();

    // ─── A malformed broadcast is shown and logged, never adopted ───
    const floorsBefore = store.getState().floors;
    const errorBefore = console.error;
    const malformedLogged = [];
    console.error = (...args) => { malformedLogged.push(args.join(" ")); };
    bus.publish("floors_updated", []);
    await drain();
    verdict.aMalformedBroadcastIsNotAdopted = store.getState().floors === floorsBefore
        && trigger.getAttribute("data-error") === "true"
        && malformedLogged.some((line) => line.includes("[floor-switcher] could not load floors"));
    // A row with no `suspended_at`, and a list with no active floor, are
    // broken payloads too: Lobby is always there and never suspended.
    const suspendedBefore = store.getState().suspendedFloors;
    const loggedBefore = malformedLogged.length;
    bus.publish("floors_updated", [{ id: "lobby", name: "Lobby" }]);
    bus.publish("floors_updated", [{ id: "lobby", name: "Lobby", suspended_at: "2026-11-16T09:30:00+00:00" }]);
    await drain();
    console.error = errorBefore;
    verdict.aListWithoutSuspendedAtOrAnActiveFloorIsNotAdopted = store.getState().floors === floorsBefore
        && store.getState().suspendedFloors === suspendedBefore
        && malformedLogged.length === loggedBefore + 2;

    // ─── A reconnect re-reads the list: broadcasts sent while down are gone ───
    const readsBefore = calls.filter((call) => call.url === "/api/floors" && call.method === "GET").length;
    bus.publish("resync", { downtimeMs: 4000 });
    await drain();
    verdict.resyncReReadsTheFloors = calls
        .filter((call) => call.url === "/api/floors" && call.method === "GET").length === readsBefore + 1
        && !trigger.hasAttribute("data-error");

    // ─── A failed floor load is shown on the trigger, not swallowed ───
    switcher.destroy();
    verdict.destroyDrainsTheBus = bus.subscriberCount() === 0;
    failFloors = true;
    const broken = BossModFloorSwitcher.mount({ store, apiFetch, bus, takeover });
    const originalError = console.error;
    const logged = [];
    console.error = (...args) => { logged.push(args.join(" ")); };
    await drain();
    console.error = originalError;
    const brokenTrigger = broken.element.querySelector(".floor-switcher-trigger");
    verdict.aFailedLoadIsShownAndLogged = brokenTrigger.getAttribute("title") === "Floors could not load."
        && brokenTrigger.getAttribute("data-error") === "true"
        && broken.element.querySelector("#floor-switcher-error").textContent === "Floors could not load."
        && logged.some((line) => line.includes("[floor-switcher] could not load floors"));
    broken.destroy();

    const failed = Object.entries(verdict).filter(([, ok]) => ok !== true).map(([name]) => name);
    if (failed.length) throw new Error(`failed: ${failed.join(", ")}`);
    process.stdout.write(JSON.stringify({ ok: true, ...verdict }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
