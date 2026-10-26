/**
 * Node harness: seat a live agent into an existing thread.
 * Invoked by tests/test_thread_seat_ui.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModYoloMode", "BossModMenu", "BossModMenuButton",
    "BossModTitleRename", "BossModChromeMenu", "BossModChromeSwitch", "BossModConversationChrome", "BossModFloorScope",
    "BossModThreadArchive", "BossModThreadSeat", "BossModThreadRequests", "BossModApprovalControls", "BossModThreadMembers", "BossModThreadSource",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModGates, BossModThreadArchive, BossModThreadSeat, BossModThreadSource } = global;

const calls = [];
const thread = {
    id: "t1",
    name: "Debra, Jim",
    status: "active",
    floor_id: "f1",
    members: [{ id: "debra", name: "Debra" }, { id: "jim", name: "Jim" }],
};
const messages = [
    { id: "m1", content: "Lock the requirements.", author_type: "human", author_name: "You" },
    { id: "m2", content: "Locked. Waiting on CLEAR.", author_type: "agent", author_name: "Debra", author_agent_id: "debra" },
];

// What GET /open-tasks answers: a task list, or null for a failed read.
let openTasks = [];

const api = async (url, init) => {
    const method = String((init && init.method) || "GET").toUpperCase();
    calls.push({ method, url: String(url), body: (init && init.body) || null });
    const members = String(url).match(/^\/api\/channels\/([^/]+)\/members$/);
    if (members && method === "POST") {
        const body = JSON.parse(init.body);
        if (thread.status !== "active") {
            return { ok: false, status: 409, async json() { return { detail: "Thread is archived — reopen it before adding someone" }; } };
        }
        if (thread.members.some((row) => row.id === body.agent_id)) {
            return { ok: false, status: 409, async json() { return { detail: "Hugh is already a member of this thread" }; } };
        }
        const known = store.getState().roster.find((row) => row.id === body.agent_id);
        if (!known) {
            return { ok: false, status: 404, async json() { return { detail: "Agent not found" }; } };
        }
        thread.members = thread.members.concat([{ id: known.id, name: known.name, role: known.role }]);
        return { ok: true, async json() { return { ...thread, members: thread.members.slice() }; } };
    }
    const member = String(url).match(/^\/api\/channels\/([^/]+)\/members\/([^/]+)$/);
    if (member && method === "DELETE") {
        if (thread.members.length === 1) {
            return { ok: false, status: 409, async text() { return JSON.stringify({ detail: "A thread needs at least one agent" }); } };
        }
        thread.members = thread.members.filter((row) => row.id !== member[2]);
        return { ok: true, async json() { return { ...thread, members: thread.members.slice() }; } };
    }
    if (String(url) === "/api/channels/t1/open-tasks" && method === "GET") {
        if (openTasks === null) return { ok: false, status: 500, async text() { return "Open tasks are unavailable"; } };
        return { ok: true, async json() { return { count: openTasks.length, tasks: openTasks }; } };
    }
    const one = String(url).match(/^\/api\/channels\/([^/?]+)$/);
    if (one && method === "GET") {
        return {
            ok: true,
            async json() {
                return { channel: { ...thread, members: thread.members.slice() }, messages: messages.slice() };
            },
        };
    }
    throw new Error(`unhandled ${method} ${url}`);
};

const store = BossModStore.createStore({
    roster: [
        { id: "debra", name: "Debra", role: "Requirements Analyst", floorId: "f1" },
        { id: "jim", name: "Jim", role: "Eng", floorId: "f1" },
        { id: "hugh", name: "Hugh", role: "Code Auditor", floorId: "f1" },
        { id: "zed", name: "Zed", role: "Elsewhere", floor_id: "f2" },
    ],
    threads: [thread],
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const presence = BossModGates.createChannelPresenceController();

async function main() {
    const candidates = BossModThreadSeat.liveCandidates(
        store.getState().roster,
        thread.members.map((row) => row.id),
        "f1",
    );
    if (candidates.length !== 1 || candidates[0].id !== "hugh") {
        throw new Error("only the live non-member on the thread's floor should be offered");
    }
    // Zed is a live non-member too, but on another floor: never offered.
    const wide = BossModThreadSeat.liveCandidates(store.getState().roster, [], "f2");
    if (wide.length !== 1 || wide[0].id !== "zed") {
        throw new Error("the floor filter must read both floorId and floor_id");
    }
    let noFloor = "";
    try { BossModThreadSeat.liveCandidates(store.getState().roster, [], ""); } catch (err) { noFloor = err.message; }
    if (!noFloor.includes("floor id is required")) throw new Error("a missing floor id must throw");
    const strictSeat = BossModThreadSeat.createThreadSeat({ api, store, prompt: async (rows) => rows[0].id });
    let noFloorPick = "";
    try { await strictSeat.pickAndSeat("t1", thread.members, ""); } catch (err) { noFloorPick = err.message; }
    if (!noFloorPick.includes("floor id is required")) throw new Error("pickAndSeat must fail closed without a floor");

    const empty = BossModThreadSeat.liveCandidates(store.getState().roster, ["debra", "jim", "hugh"], "f1");
    let emptyFailed = false;
    const emptySeat = BossModThreadSeat.createThreadSeat({ api, store });
    try {
        await emptySeat.prompt(empty);
    } catch (err) {
        emptyFailed = String(err.message).includes("already in this thread");
    }
    if (!emptyFailed) throw new Error("an empty picker must fail closed");

    const seater = BossModThreadSeat.createThreadSeat({
        api,
        store,
        prompt: async (rows) => rows[0].id,
    });
    const summary = await seater.pickAndSeat("t1", thread.members.filter((row) => row.id !== "hugh"), "f1");
    if (!summary || summary.members.length !== 3) {
        throw new Error("seating Hugh must keep the room and add him");
    }
    const posted = calls.find((item) => item.method === "POST" && item.url === "/api/channels/t1/members");
    if (!posted || !String(posted.body).includes("hugh")) {
        throw new Error("the picker must POST the chosen live agent");
    }

    const duplicate = await seater.request("t1", "hugh").then(() => null, (err) => err.message);
    if (!String(duplicate).includes("already a member")) {
        throw new Error("a duplicate seat must surface the server error");
    }

    const source = BossModThreadSource.createThreadSource("t1", {
        api,
        bus,
        presence,
        archive: BossModThreadArchive.createThreadArchive({ api }),
        seat: seater,
        forgetCache: () => {},
    });
    const loaded = await source.load();
    if (loaded.length !== 2 || loaded[0].text !== "Lock the requirements.") {
        throw new Error("seating must not wipe the transcript the source paints");
    }
    // The @ scope is read from the channel on every call: Hugh, seated
    // above, is already in it.
    const scope = source.mentionScope();
    if (scope.everyone !== true || scope.memberIds.join(",") !== "debra,jim,hugh") {
        throw new Error(`thread mention scope: ${JSON.stringify(scope)}`);
    }
    const liveChrome = source.chrome();
    if (!liveChrome.actions.some((item) => item.id === "channel-seat-btn")) {
        throw new Error("a live thread must offer Add to thread");
    }

    // The header's participant count is a button only when the descriptor
    // supplies onSubtitleSelect, and it opens the same dialog Add people… does.
    const errors = [];
    const chrome = BossModConversationChrome.createChrome({ onError: (message) => errors.push(message) });
    documentStub.body.append(chrome.element);
    chrome.apply(liveChrome);
    const link = chrome.element.querySelector(".conversation-subtitle-link");
    if (!link || link.textContent !== "3 participants") throw new Error("a thread's count must be a button");
    chrome.apply({ title: "Ann", subtitle: "Online", actions: [] });
    if (chrome.element.querySelector(".conversation-subtitle-link")) {
        throw new Error("a descriptor without onSubtitleSelect must keep a plain subtitle");
    }
    chrome.apply(liveChrome);

    // The dialog and the chrome fire their handlers without awaiting them.
    const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
    const panels = () => documentStub.querySelectorAll(".modal-panel");
    await chrome.element.querySelector(".conversation-subtitle-link").dispatchClick();
    await settle();
    if (panels().length !== 1) throw new Error("the count must open the members dialog");
    await liveChrome.actions.find((item) => item.id === "channel-seat-btn").onSelect();
    await settle();
    if (panels().length !== 1) throw new Error("Add people… must reuse the open dialog");
    const removeButtons = () => documentStub.querySelectorAll(".thread-member-remove");
    if (removeButtons().length !== 3) throw new Error("the dialog must list all three members");

    // Remove Jim: the DELETE goes out and the list repaints from the answer.
    await removeButtons()[1].dispatchClick();
    await settle();
    const deleted = calls.find((item) => item.method === "DELETE" && item.url === "/api/channels/t1/members/jim");
    if (!deleted || removeButtons().length !== 2) throw new Error("removing must DELETE and repaint");

    const deletes = (id) => calls.filter((item) => item.method === "DELETE" && item.url === `/api/channels/t1/members/${id}`);
    // The dialog frame has its own .btn-sm; the confirmation's two are the last two.
    const confirmButtons = () => {
        if (!documentStub.querySelector(".thread-member-confirm")) return [];
        return documentStub.querySelectorAll(".btn-sm").slice(-2);
    };

    // A failed task read is shown and removes nobody (no silent skip).
    openTasks = null;
    await removeButtons()[1].dispatchClick();
    await settle();
    if (deletes("hugh").length) throw new Error("a failed open-tasks read must not remove");
    if (!documentStub.querySelector(".context-error").textContent.includes("unavailable")) {
        throw new Error("a failed open-tasks read must be shown in the dialog");
    }

    // Only Debra's task is open: Hugh has none assigned, so he goes at once.
    openTasks = [{ assigned_to: "debra" }];
    await removeButtons()[1].dispatchClick();
    await settle();
    if (deletes("hugh").length !== 1) throw new Error("no assigned tasks must remove at once");
    // The picker offers the first floor-mate left out: Jim.
    const addMember = async () => { await documentStub.querySelector(".menu-door").dispatchClick(); await settle(); };
    await addMember();

    // Open tasks assigned to Jim: confirm in place, DELETE only on Remove anyway.
    openTasks = [{ assigned_to: "jim" }, { assigned_to: "jim" }, { assigned_to: "debra" }];
    await removeButtons()[1].dispatchClick();
    await settle();
    const confirmText = documentStub.querySelector(".thread-member-confirm").textContent;
    if (!confirmText.includes("Jim still has 2 open tasks") || !confirmText.includes("stay assigned to Jim")) {
        throw new Error(`confirmation copy: ${confirmText}`);
    }
    if (deletes("jim").length > 1) throw new Error("no DELETE before the operator confirms");
    if (documentStub.querySelectorAll(".modal-panel").length !== 1) throw new Error("the confirmation must not open a second modal");
    const [confirmBtn, keepBtn] = confirmButtons();
    if (confirmBtn.textContent !== "Remove anyway" || keepBtn.textContent !== "Keep") throw new Error(`confirmation buttons: ${confirmButtons().map((b) => b.textContent)}`);
    const jimDeletes = deletes("jim").length;
    await keepBtn.dispatchClick();
    await settle();
    if (deletes("jim").length !== jimDeletes || confirmButtons().length || removeButtons().length !== 2) {
        throw new Error("Keep must cancel without removing");
    }
    await removeButtons()[1].dispatchClick();
    await settle();
    await confirmButtons()[0].dispatchClick();
    await settle();
    if (deletes("jim").length !== jimDeletes + 1 || confirmButtons().length || removeButtons().length !== 1) {
        throw new Error("Remove anyway must DELETE and repaint");
    }
    openTasks = [];
    await addMember();

    // Down to one member: Remove is off and the reason is on screen.
    await removeButtons()[1].dispatchClick();
    await settle();
    const buttons = removeButtons();
    if (buttons.length !== 1 || !buttons[0].disabled) throw new Error("the last member cannot be removed");
    if (!documentStub.querySelector(".thread-members").textContent.includes("at least one agent")) {
        throw new Error("the last-member reason must be visible");
    }

    // Add from the dialog: Jim (first non-member on f1) is back; Zed never offered.
    await documentStub.querySelector(".menu-door").dispatchClick();
    await settle();
    if (thread.members.map((row) => row.id).join(",") !== "debra,jim") {
        throw new Error(`adding from the dialog seated ${thread.members.map((row) => row.id)}`);
    }
    if (errors.length) throw new Error(`unexpected header errors: ${errors}`);
    await documentStub.querySelector(".modal-close").dispatchClick();
    if (panels().length !== 0) throw new Error("closing must remove the dialog");
    chrome.destroy();

    thread.status = "archived";
    const sealed = await source.load();
    if (sealed.length !== 2) throw new Error("an archived load must keep history");
    if (source.chrome().actions.some((item) => item.id === "channel-seat-btn")) {
        throw new Error("a sealed thread must not offer Add to thread");
    }
    if (source.chrome().onSubtitleSelect) throw new Error("a sealed thread's count must not open anything");

    process.stdout.write(JSON.stringify({
        ok: true,
        offeredOnlyHugh: true,
        emptyPickerFailClosed: true,
        seatedWithoutWipe: true,
        duplicateFailClosed: true,
        liveChromeOffersSeat: true,
        archivedHidesSeat: true,
        floorScopedPicker: true,
        subtitleOpensMembers: true,
        removeRepaints: true,
        lastMemberLocked: true,
        taskWarning: true,
        mentionScope: scope,
    }));
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
