/**
 * Node harness: the header floor switcher and its Edit / Delete floor modal.
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
 * floor is gone.
 *
 * Invoked by tests/test_ui_floor_switcher.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const NAMES = [
    "BossModDom", "BossModStore", "BossModBus", "BossModOverlayFocus", "BossModOverlays",
    "BossModFloorScope", "BossModFloorApi", "BossModFloorEdit", "BossModFloorSwitcher",
];
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

let floors = [{ id: "lobby", name: "Lobby" }, { id: "fin", name: "Finance" }];
let failFloors = false;
const calls = [];
function apiFetch(url, init) {
    const method = (init && init.method) || "GET";
    calls.push({ url, method, body: init && init.body ? JSON.parse(init.body) : null });
    if (url === "/api/floors" && method === "GET") {
        return Promise.resolve(failFloors ? response(500, { detail: "boom" }) : response(200, floors));
    }
    if (url === "/api/floors" && method === "POST") {
        const created = { id: "ops", name: JSON.parse(init.body).name };
        floors = floors.concat([created]);
        return Promise.resolve(response(201, created));
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
    const switcher = BossModFloorSwitcher.mount({ store, apiFetch, bus });
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
    const more = panel.querySelectorAll(".floor-row-more");
    verdict.everyRowHasANamedEditButton = more.length === 2
        && more[1].getAttribute("aria-label") === "Edit floor Finance";

    // ─── Picking a row moves the operator and closes the panel ───
    await choices[1].dispatchClick();
    verdict.pickingARowSetsTheCurrentFloor = store.getState().currentFloorId === "fin"
        && !switcher.element.querySelector(".menu")
        && trigger.getAttribute("aria-label") === "Floor: Finance";

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

    // ─── Edit floor, then the Delete layer ───
    store.setState({ currentFloorId: "fin" });
    await trigger.dispatchClick();
    panel = switcher.element.querySelector(".menu");
    const finMore = panel.querySelectorAll(".floor-row-more")
        .find((button) => button.getAttribute("aria-label") === "Edit floor Finance");
    await finMore.dispatchClick();
    const dialogs = () => documentStub.body.querySelectorAll(".modal-panel");
    const edit = dialogs()[0];
    const editInput = edit.querySelector(".field-input");
    const actionLabels = edit.querySelectorAll(".modal-action").map((button) => button.textLabel);
    verdict.editFloorPrefillsTheNameAndOffersDelete = Boolean(edit)
        && edit.getAttribute("aria-label") === "Edit floor"
        && editInput.value === "Finance"
        && actionLabels.join("|") === "Delete floor…|Cancel|Save";

    const deleteAction = edit.querySelectorAll(".modal-action")
        .find((button) => button.textLabel === "Delete floor…");
    await deleteAction.dispatchClick();
    const layer = dialogs()[1];
    const confirm = layer.querySelector("#floor-delete-confirm");
    const radios = layer.querySelectorAll("input");
    verdict.deleteLayerWaitsForTheAgentsChoice = Boolean(layer)
        && layer.getAttribute("aria-label") === "Delete Finance"
        && edit.hidden === true
        && radios.length === 2
        && radios.every((radio) => radio.getAttribute("type") === "radio" && !radio.checked)
        && confirm.disabled === true
        && /1 agent works on Finance\. Its 1 thread will be archived\./.test(layer.textContent);
    (radios[0].listeners.change || []).forEach((fn) => fn({ target: radios[0] }));
    verdict.choosingEnablesDelete = layer.querySelector("#floor-delete-confirm").disabled === false;
    // ✕ on the top layer closes every layer.
    await layer.querySelector(".modal-close").dispatchClick();

    // ─── Live: another window renamed the floor the operator is on ───
    // The panel is open when it lands; its rows are stale, so it closes.
    await trigger.dispatchClick();
    const openBeforeRename = Boolean(switcher.element.querySelector(".menu"));
    bus.publish("floors_updated", [
        { id: "lobby", name: "Lobby" }, { id: "fin", name: "Accounts" }, { id: "ops", name: "Ops" },
    ]);
    await drain();
    verdict.aRenameElsewhereRepaintsTheTrigger = openBeforeRename
        && store.getState().currentFloorId === "fin"
        && trigger.getAttribute("aria-label") === "Floor: Accounts"
        && switcher.element.querySelector(".floor-switcher-name").textContent === "Accounts"
        && !switcher.element.querySelector(".menu")
        && trigger.getAttribute("aria-expanded") === "false";

    // ─── Live: another window deleted it ───
    bus.publish("floors_updated", [{ id: "lobby", name: "Lobby" }, { id: "ops", name: "Ops" }]);
    await drain();
    verdict.aDeleteElsewhereMovesTheOperatorToLobby = store.getState().currentFloorId === "lobby"
        && store.getState().floors.every((floor) => floor.id !== "fin")
        && trigger.getAttribute("aria-label") === "Floor: Lobby";

    // ─── A malformed broadcast is shown and logged, never adopted ───
    const floorsBefore = store.getState().floors;
    const errorBefore = console.error;
    const malformedLogged = [];
    console.error = (...args) => { malformedLogged.push(args.join(" ")); };
    bus.publish("floors_updated", []);
    await drain();
    console.error = errorBefore;
    verdict.aMalformedBroadcastIsNotAdopted = store.getState().floors === floorsBefore
        && trigger.getAttribute("data-error") === "true"
        && malformedLogged.some((line) => line.includes("[floor-switcher] could not load floors"));

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
    const broken = BossModFloorSwitcher.mount({ store, apiFetch, bus });
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
