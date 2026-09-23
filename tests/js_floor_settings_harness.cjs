/**
 * Node harness: the floor settings (People, Threads, Projects), the Add
 * picker, and the move confirm layer.
 *
 * Checks what renders and what a click sends, which the source text cannot
 * show: that the four sections render with their counts from the store and
 * the server, that Lobby offers no Delete, that a row's `⋯` → Move to… menu
 * hangs off the row's own host and never <body>, that Add people opens a
 * picker grouped by floor whose Next waits for a choice, that the confirm
 * layer shows every group of a stubbed plan, that unchecking a companion
 * re-asks the server with its id excluded and keeps its row, and that a
 * stale plan is re-read and said before a second try moves.
 *
 * Invoked by tests/test_ui_floor_settings.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const NAMES = [
    "BossModDom", "BossModStore", "BossModFormat", "BossModAvatar", "BossModSearchField",
    "BossModOverlayFocus", "BossModOverlays",
    "BossModFloorScope", "BossModFloorApi", "BossModFloorDelete", "BossModFloorPicker",
    "BossModFloorMoveConfirm", "BossModFloorPeople", "BossModFloorThreads", "BossModFloorProjects",
    "BossModFloorSettings",
];
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
paths.forEach((path, index) => {
    eval(`${fs.readFileSync(path, "utf8")}\n;global.${NAMES[index]} = ${NAMES[index]};\n`);
});

const settled = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settled(); };

function response(status, body) {
    const text = JSON.stringify(body);
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(JSON.parse(text)),
        text: () => Promise.resolve(text),
    };
}

const THREADS = [
    { id: "t1", name: "Books", status: "active", floor_id: "lobby", members: [{ id: "a1", name: "Ada" }, { id: "a2", name: "Bob" }] },
    { id: "t2", name: "Ledger", status: "active", floor_id: "lobby", members: [{ id: "a1", name: "Ada" }] },
    { id: "t3", name: "Audit", status: "active", floor_id: "fin", members: [{ id: "a3", name: "Cy" }] },
];
const PLAN = {
    target_floor_id: "lobby",
    agents: [{ id: "a3", name: "Cy", from_floor_id: "fin", reason: "picked" }],
    threads: [],
    companions: [{ id: "t4", name: "Standup", from_floor_id: "fin", member_names: ["Cy"] }],
    split_threads: [{ id: "t3", name: "Audit", from_floor_id: "fin", leaving_names: ["Cy"], staying_names: ["Dee"] }],
    stranded_tasks: [{ id: "k1", title: "Audit memo", thread_name: "Audit" }],
    fingerprint: "fp-1",
};

const calls = [];
let moveAnswers = [];
function apiFetch(url, init) {
    const method = (init && init.method) || "GET";
    const body = init && init.body ? JSON.parse(init.body) : null;
    calls.push({ url, method, body });
    if (url === "/api/channels?status=active") return Promise.resolve(response(200, THREADS));
    if (url === "/api/floors/lobby/projects") {
        return Promise.resolve(response(200, [{ name: "site", modified_at: "2026-09-01T10:00:00+00:00" }]));
    }
    if (url === "/api/floors/fin/projects") return Promise.resolve(response(200, [{ name: "books", modified_at: null }]));
    if (url === "/api/floors/lobby/move-plan" && method === "POST") {
        const excluded = body.exclude_companion_ids || [];
        const plan = excluded.includes("t4")
            ? {
                ...PLAN,
                companions: [],
                split_threads: [...PLAN.split_threads,
                    { id: "t4", name: "Standup", from_floor_id: "fin", leaving_names: ["Cy"], staying_names: [] }],
                fingerprint: "fp-2",
            }
            : PLAN;
        return Promise.resolve(response(200, plan));
    }
    if (url === "/api/floors/lobby/move" && method === "POST") {
        const answer = moveAnswers.shift();
        return Promise.resolve(answer);
    }
    return Promise.reject(new Error(`unexpected request ${method} ${url}`));
}

const dialogs = () => documentStub.body.querySelectorAll(".modal-panel");
const visibleDialog = () => dialogs().find((panel) => !panel.hidden);
const actionNamed = (panel, label) => panel.querySelectorAll(".modal-action")
    .find((button) => button.textLabel === label);
const count = (url, method = "GET") => calls.filter((call) => call.url === url && call.method === method).length;
const change = (input) => (input.listeners.change || []).forEach((fn) => fn({ target: input }));

(async () => {
    const verdict = {};
    const store = BossModStore.createStore({
        currentFloorId: "lobby",
        floors: [{ id: "lobby", name: "Lobby", has_folder: true }, { id: "fin", name: "Finance", has_folder: true }],
        roster: [
            { id: "a1", name: "Ada", role: "Eng", floor_id: "lobby" },
            { id: "a2", name: "Bob", role: "Ops", floor_id: "lobby" },
            { id: "a3", name: "Cy", role: "Books", floor_id: "fin" },
        ],
    });
    const floorApi = BossModFloorApi.createFloorApi({ apiFetch });
    const reloadFloors = async () => true;

    // ─── Four sections with their counts; Lobby has no Delete ───
    const settings = BossModFloorSettings.open({ store, floorApi, floorId: "lobby", reloadFloors });
    await drain();
    const panel = dialogs()[0];
    const titles = panel.querySelectorAll(".floor-section-title").map((node) => node.textContent);
    verdict.settingsRenderFourSectionsWithCounts = panel.getAttribute("aria-label") === "Lobby"
        && panel.getAttribute("data-size") === "panel"
        && panel.querySelector("#floor-settings-name").value === "Lobby"
        && titles.join("|") === "People (2)|Threads (2)|Projects (1)";
    verdict.lobbyHasNoDelete = panel.querySelectorAll(".modal-action")
        .map((button) => button.textLabel).join("|") === "Close";

    // ─── ⋯ → Move to… hangs off the row's host ───
    const firstMore = panel.querySelector(".floor-item").querySelector(".floor-row-more");
    await firstMore.dispatchClick();
    const moveMenu = panel.querySelector(".menu");
    verdict.moveToMenuHangsOffTheRow = Boolean(moveMenu)
        && moveMenu.parentNode !== documentStub.body
        && moveMenu.parentNode.classList.contains("floor-item-more")
        && moveMenu.getAttribute("data-menu") === "floor-move"
        && moveMenu.querySelectorAll(".menu-action").map((button) => button.textContent).join("|") === "Finance"
        && firstMore.getAttribute("aria-expanded") === "true";
    await firstMore.dispatchClick();

    // ─── Add people → a picker grouped by floor; Next waits for a choice ───
    const addPeople = panel.querySelectorAll(".floor-section-add")
        .find((button) => button.textContent === "Add people");
    await addPeople.dispatchClick();
    await drain();
    const picker = visibleDialog();
    const next = picker.querySelector("#floor-picker-next");
    const groups = picker.querySelectorAll(".floor-pick-group");
    verdict.pickerGroupsOtherFloorsAndWaits = picker.getAttribute("aria-label") === "Add people to Lobby"
        && groups.length === 1
        && groups[0].querySelector(".floor-pick-group-title").textContent === "Finance"
        && groups[0].querySelectorAll(".floor-pick-option").length === 1
        && next.disabled === true;
    const box = picker.querySelector("input[type=\"checkbox\"]");
    box.checked = true;
    change(box);
    verdict.choosingEnablesNext = picker.querySelector("#floor-picker-next").disabled === false;

    // ─── The confirm shows every group of the plan ───
    await picker.querySelector("#floor-picker-next").dispatchClick();
    await drain();
    const confirm = visibleDialog();
    const firstPlan = calls.filter((call) => call.url === "/api/floors/lobby/move-plan");
    const groupKeys = () => confirm.querySelectorAll(".floor-move-group").map((node) => node.getAttribute("data-group"));
    verdict.confirmShowsEveryGroup = confirm.getAttribute("aria-label") === "Move to Lobby"
        && firstPlan.length === 1
        && JSON.stringify(firstPlan[0].body) === JSON.stringify({
            agent_ids: ["a3"], channel_ids: [], exclude_companion_ids: [],
        })
        && groupKeys().join("|") === "moving|companions|split|stranded"
        && /Cy/.test(confirm.textContent)
        && /Audit memo/.test(confirm.textContent)
        && /Cy leaves; Dee stays/.test(confirm.textContent)
        && confirm.querySelector("#floor-move-confirm").textLabel === "Move 1 person, 1 thread";

    // ─── Unchecking a companion re-asks with it excluded, and keeps its row ───
    const companion = confirm.querySelector("input[type=\"checkbox\"]");
    const checkedAtFirst = companion.checked === true;
    companion.checked = false;
    change(companion);
    await drain();
    const replanned = calls.filter((call) => call.url === "/api/floors/lobby/move-plan");
    const companionAfter = confirm.querySelector("input[type=\"checkbox\"]");
    verdict.uncheckingACompanionReplansWithItExcluded = checkedAtFirst
        && replanned.length === 2
        && JSON.stringify(replanned[1].body.exclude_companion_ids) === JSON.stringify(["t4"])
        && Boolean(companionAfter) && companionAfter.checked === false
        && /no one stays/.test(confirm.textContent)
        && confirm.querySelector("#floor-move-confirm").textLabel === "Move 1 person, 0 threads";

    // ─── A stale plan is re-read and said; the second try moves ───
    moveAnswers = [
        response(409, { code: "plan_changed", message: "Something changed since this move was planned." }),
        response(200, { moved_agents: ["a3"], moved_threads: [], left_threads: ["t3", "t4"] }),
    ];
    const warnBefore = console.warn;
    console.warn = () => {};
    await confirm.querySelector("#floor-move-confirm").dispatchClick();
    await drain();
    console.warn = warnBefore;
    const moves = calls.filter((call) => call.url === "/api/floors/lobby/move");
    verdict.aStalePlanIsReReadAndSaid = moves.length === 1
        && moves[0].body.fingerprint === "fp-2"
        && calls.filter((call) => call.url === "/api/floors/lobby/move-plan").length === 3
        && /Something changed — review again\./.test(confirm.textContent)
        && Boolean(visibleDialog()) && visibleDialog() === confirm;

    const threadReads = count("/api/channels?status=active");
    await confirm.querySelector("#floor-move-confirm").dispatchClick();
    await drain();
    verdict.aMoveClosesBackToTheSettingsAndRefreshes = dialogs().length === 1
        && visibleDialog() === panel
        && count("/api/floors/lobby/move", "POST") === 2
        && count("/api/channels?status=active") === threadReads + 1;

    // ─── Another floor's settings offer Delete ───
    settings.close();
    const fin = BossModFloorSettings.open({ store, floorApi, floorId: "fin", reloadFloors });
    await drain();
    const finPanel = dialogs()[0];
    verdict.otherFloorsOfferDelete = finPanel.querySelectorAll(".modal-action")
        .map((button) => button.textLabel).join("|") === "Delete floor…|Close"
        && finPanel.querySelectorAll(".floor-section-title").map((node) => node.textContent)
            .join("|") === "People (1)|Threads (1)|Projects (1)";
    fin.close();
    verdict.closingDrainsTheStore = store.subscriberCount() === 0 && dialogs().length === 0;

    const failed = Object.entries(verdict).filter(([, ok]) => ok !== true).map(([name]) => name);
    if (failed.length) throw new Error(`failed: ${failed.join(", ")}`);
    process.stdout.write(JSON.stringify({ ok: true, ...verdict }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
