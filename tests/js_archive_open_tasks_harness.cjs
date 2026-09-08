/**
 * Node harness: archive prompt branches for threads with open tasks.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 *
 * Phase 2A split the dock-era ChannelsView into three owners, so the same
 * eleven properties are proven against three subjects: the copy and the two
 * prompt branches against conversation/sources/thread-archive.js, the archive /
 * reopen / seal behaviour against conversation/sources/thread-source.js, and
 * the archived-list filter against shell/roster.js, which owns the thread list
 * in the new shell. The emitted payload keys are byte-identical.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

const [
    agentStatusPath, domPath, avatarPath, storePath, busPath, gatesPath, consentPath,
    overlaysPath, archivePath, threadSourcePath,
    rosterPeoplePath, threadCreatePath, rosterThreadsPath, rosterPath,
] = process.argv.slice(2);
const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
load(agentStatusPath, "BossModAgentStatus");
load(domPath, "BossModDom");
load(avatarPath, "BossModAvatar");
load(storePath, "BossModStore");
load(busPath, "BossModBus");
load(gatesPath, "BossModGates");
load(consentPath, "BossModConsentCard");
load(overlaysPath, "BossModOverlays");
load(archivePath, "BossModThreadArchive");
load(threadSourcePath, "BossModThreadSource");
load(rosterPeoplePath, "BossModRosterPeople");
load(threadCreatePath, "BossModThreadCreate");
load(rosterThreadsPath, "BossModRosterThreads");
load(rosterPath, "BossModRoster");

const {
    BossModThreadArchive, BossModThreadSource, BossModGates, BossModRoster,
    BossModStore, BossModBus,
} = global;

const HONESTY =
    "Not permanently deleted — leaves the active list and seals the room (no new posts).";

// ─── Backing store and API ───

const calls = [];

function thread(id, name, openCount) {
    return {
        id,
        name,
        status: "active",
        openCount,
        members: [{ id: `m-${id}`, name, status: "idle" }],
        member_count: 1,
        latest_message: null,
        updated_at: "2026-01-01T00:00:00Z",
    };
}

const threads = [
    thread("open-a", "Ann", 2),
    thread("open-b", "Bea", 2),
    thread("none-c", "Cal", 0),
    thread("open-d", "Dee", 2),
];

function threadsFor(url) {
    const query = String(url).split("?")[1] || "";
    const status = new URLSearchParams(query).get("status") || "active";
    return threads
        .filter((item) => (item.status || "active") === status)
        .map((item) => ({ ...item, members: item.members.map((m) => ({ ...m })) }));
}

function openTasksFor(item) {
    const tasks = [];
    for (let i = 0; i < item.openCount; i += 1) {
        tasks.push({ id: `${item.id}-task-${i + 1}`, status: "pending", title: `Task ${i + 1}` });
    }
    return { count: tasks.length, tasks };
}

const api = async (url, opts = {}) => {
    const method = String(opts.method || "GET").toUpperCase();
    calls.push({ method, url: String(url), body: opts.body || null });
    const path = String(url).split("?")[0];

    if (path === "/api/world" && method === "GET") {
        return { ok: true, async json() { return []; } };
    }
    if (path === "/api/channels" && method === "GET") {
        return { ok: true, async json() { return threadsFor(url); } };
    }
    const open = path.match(/^\/api\/channels\/([^/]+)\/open-tasks$/);
    if (open && method === "GET") {
        const item = threads.find((row) => row.id === open[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        return { ok: true, async json() { return openTasksFor(item); } };
    }
    const archivePost = path.match(/^\/api\/channels\/([^/]+)\/archive$/);
    if (archivePost && method === "POST") {
        const item = threads.find((row) => row.id === archivePost[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        item.status = "archived";
        return { ok: true, async json() { return { ...item }; } };
    }
    const reopenPost = path.match(/^\/api\/channels\/([^/]+)\/reopen$/);
    if (reopenPost && method === "POST") {
        const item = threads.find((row) => row.id === reopenPost[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        item.status = "active";
        return { ok: true, async json() { return { ...item }; } };
    }
    const messagePost = path.match(/^\/api\/channels\/([^/]+)\/messages$/);
    if (messagePost && method === "POST") {
        const item = threads.find((row) => row.id === messagePost[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        if (item.status === "archived") return { ok: false, async text() { return "sealed"; } };
        return { ok: true, async json() { return { status: "ok" }; } };
    }
    const one = path.match(/^\/api\/channels\/([^/]+)$/);
    if (one) {
        const item = threads.find((row) => row.id === one[1]);
        if (!item) return { ok: false, async text() { return "missing"; } };
        if (method === "DELETE") {
            item.status = "archived";
            return { ok: true, async json() { return { ...item }; } };
        }
        if (method === "GET") {
            return {
                ok: true,
                async json() {
                    return {
                        channel: { ...item, members: item.members.map((m) => ({ ...m })) },
                        messages: [],
                    };
                },
            };
        }
    }
    throw new Error(`unhandled ${method} ${url}`);
};

const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const presence = BossModGates.createChannelPresenceController();
const forgotten = [];
const noop = () => {};
const signals = { message: noop, reset: noop, presence: noop, chrome: noop };

function sourceFor(threadId, confirmChoice) {
    return BossModThreadSource.createThreadSource(threadId, {
        api,
        bus,
        presence,
        archive: BossModThreadArchive.createThreadArchive({ api, confirm: () => confirmChoice }),
        forgetCache: (id) => forgotten.push(id),
    });
}

function modal() {
    return documentStub.body.querySelector(".modal");
}

function modalButton(id) {
    return documentStub.body.querySelector(`#${id}`);
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

async function main() {
    // ─── 1. The copy and the two branches ───

    if (BossModThreadArchive.HONESTY_COPY !== HONESTY) throw new Error("honesty constant mismatch");
    const emptySpec = BossModThreadArchive.spec(0);
    const openSpec = BossModThreadArchive.spec(2);
    if (emptySpec.title !== "Archive thread?" || openSpec.title !== "Archive thread?") {
        throw new Error("archive title mismatch");
    }
    if (emptySpec.honesty !== HONESTY || openSpec.honesty !== HONESTY) {
        throw new Error("modal honesty copy missing");
    }
    if (BossModThreadArchive.copy(0) !== `Hides it from the active list and seals the room — no new messages or access cards. Open tasks stay on the board. ${HONESTY}`) {
        throw new Error("N=0 archive copy mismatch");
    }
    if (BossModThreadArchive.copy(2) !== `This thread has 2 open tasks. Sealing stops new posts and access cards. ${HONESTY}`) {
        throw new Error("N>0 archive copy mismatch");
    }
    if (BossModThreadArchive.shouldPrompt(0) !== false) {
        throw new Error("N=0 must use the two-button confirm, not the open-task choices");
    }
    if (BossModThreadArchive.shouldPrompt(2) !== true) {
        throw new Error("N>0 must show the open-task choices");
    }
    if (emptySpec.buttons.map((b) => b.label).join("|") !== "Cancel|Archive") {
        throw new Error("N=0 buttons must be Cancel and Archive");
    }
    if (openSpec.buttons.map((b) => b.label).join("|") !== "Back|Archive only|Cancel tasks & archive") {
        throw new Error("N>0 buttons mismatch");
    }
    // Dismissing the dialog is never read as consent.
    if (emptySpec.dismissChoice !== "back" || openSpec.dismissChoice !== "back") {
        throw new Error("dismissing must mean back");
    }
    if (!BossModThreadArchive.isAbort(undefined) || !BossModThreadArchive.isAbort("cancel")) {
        throw new Error("no choice at all must abort");
    }

    // ─── 2. The real modal renders the right branch ───

    const flow = BossModThreadArchive.createThreadArchive({ api });

    const openPending = flow.prompt(2);
    const openDialog = modal();
    if (!openDialog) throw new Error("prompt(2) rendered no dialog");
    const openTasksThreeButtons = openDialog.querySelectorAll("button").length === 3
        && Boolean(modalButton("channel-archive-cancel-tasks"))
        && Boolean(modalButton("channel-archive-only"))
        && Boolean(modalButton("channel-archive-back"));
    if (!openTasksThreeButtons) throw new Error("N>0 must show a three-button modal");
    if (!openDialog.textContent.includes(HONESTY)) {
        throw new Error("honesty copy must appear in the open-task modal");
    }
    // The way out holds focus, so Enter and Esc agree (overlays contract).
    if (documentStub.activeElement !== modalButton("channel-archive-back")) {
        throw new Error("the abort choice must hold focus in the open-task modal");
    }
    await modalButton("channel-archive-back").dispatchClick();
    const backAborts = BossModThreadArchive.isAbort(await openPending);
    if (!backAborts) throw new Error("Back must abort archive");

    const zeroPending = flow.prompt(0);
    const zeroDialog = modal();
    if (!zeroDialog) throw new Error("prompt(0) rendered no dialog");
    const zeroOpenTwoButtons = zeroDialog.querySelectorAll("button").length === 2
        && Boolean(modalButton("channel-archive-confirm"))
        && Boolean(modalButton("channel-archive-back"))
        && !zeroDialog.textContent.includes("Cancel tasks")
        && !zeroDialog.textContent.includes("Archive only");
    if (!zeroOpenTwoButtons) throw new Error("N=0 must show a two-button modal");
    if (!zeroDialog.textContent.includes(HONESTY)) {
        throw new Error("honesty copy must appear in the zero-open modal");
    }
    await modalButton("channel-archive-confirm").dispatchClick();
    const zeroOpenConfirm = (await zeroPending) === "archive_only";
    if (!zeroOpenConfirm) throw new Error("N=0 confirm must choose archive_only");

    const zeroCancelPending = flow.prompt(0);
    await modalButton("channel-archive-back").dispatchClick();
    const zeroOpenCancelAborts = BossModThreadArchive.isAbort(await zeroCancelPending);
    if (!zeroOpenCancelAborts) throw new Error("N=0 Cancel must abort archive");
    if (modal()) throw new Error("choosing must close the dialog");

    // ─── 3. Archiving through the source ───

    const annSource = sourceFor("open-a", "cancel_and_archive");
    const offAnn = annSource.subscribe(signals);
    await annSource.load();
    if (annSource.chrome().actions[0].id !== "channel-archive-btn") {
        throw new Error("a live thread must offer Archive");
    }
    calls.length = 0;
    await annSource.chrome().actions[0].onSelect();
    if (calls.some((item) => item.url === "/api/tasks/cancel")) {
        throw new Error("cancel-and-archive must not use a separate cancel POST");
    }
    const cancelAndArchive = calls.some((item) => item.method === "POST"
        && item.url.includes("/api/channels/open-a/archive")
        && item.url.includes("cancel_open_tasks=true"));
    if (!cancelAndArchive) {
        throw new Error("primary must cancel tasks via archive?cancel_open_tasks=true");
    }

    const beaSource = sourceFor("open-b", "archive_only");
    const offBea = beaSource.subscribe(signals);
    await beaSource.load();
    calls.length = 0;
    await beaSource.chrome().actions[0].onSelect();
    if (calls.some((item) => item.url === "/api/tasks/cancel")) {
        throw new Error("archive only must leave tasks open");
    }
    const archiveOnly = calls.some((item) => item.method === "DELETE"
        && item.url === "/api/channels/open-b");
    if (!archiveOnly) throw new Error("archive only must still archive the thread");

    // Aborting leaves the thread live and still archivable.
    const deeSource = sourceFor("open-d", "back");
    const offDee = deeSource.subscribe(signals);
    await deeSource.load();
    calls.length = 0;
    await deeSource.chrome().actions[0].onSelect();
    if (calls.some((item) => item.method === "DELETE" && item.url === "/api/channels/open-d")) {
        throw new Error("Back must abort archive");
    }
    if (deeSource.canSend() !== true
        || deeSource.chrome().actions[0].id !== "channel-archive-btn") {
        throw new Error("an aborted archive must leave the thread archivable");
    }

    // ─── 4. A sealed room stays sealed ───

    if (annSource.canSend() !== false || beaSource.canSend() !== false) {
        throw new Error("archived threads must not accept posts");
    }
    if (annSource.disabledReason() !== "Archived — reopen to post again.") {
        throw new Error("the sealed composer must say why");
    }
    const sealedChrome = annSource.chrome();
    if (sealedChrome.actions.length !== 1
        || sealedChrome.actions[0].id !== "channel-reopen-btn"
        || sealedChrome.actions[0].label !== "Reopen") {
        throw new Error("an archived thread must offer Reopen and only Reopen");
    }
    offAnn();
    let leaked = 0;
    const offSpam = annSource.subscribe({ ...signals, message: () => { leaked += 1; } });
    bus.publish("channel_message", {
        channel_id: "open-a",
        content: "spam after archive",
        author_type: "agent",
        author_name: "Ada",
        message_id: "spam-1",
    });
    bus.publish("channel_presence", {
        channel_id: "open-a",
        agent_id: "ada",
        agent_name: "Ada",
        phase: "thinking",
    });
    const archivedNotLive = leaked === 0
        && presence.list("open-a").length === 0
        && forgotten.includes("open-a")
        && annSource.canSend() === false;
    if (!archivedNotLive) throw new Error("live handlers must not revive an archived thread");
    offSpam();

    // ─── 5. Reopen unseals ───

    const calSource = sourceFor("none-c", "archive_only");
    const offCal = calSource.subscribe(signals);
    await calSource.load();
    await calSource.chrome().actions[0].onSelect();
    if (calSource.canSend() !== false) throw new Error("none-c must archive");
    calls.length = 0;
    await calSource.chrome().actions[0].onSelect();
    const reopened = calls.some((item) => item.method === "POST"
        && item.url.includes("/api/channels/none-c/reopen"));
    if (!reopened) throw new Error("Reopen must POST /reopen");
    if (calSource.canSend() !== true || calSource.disabledReason() !== "") {
        throw new Error("Reopen must unseal the thread");
    }
    calls.length = 0;
    await calSource.send("hello again");
    const reopenUnseals = reopened && calls.some((item) => item.method === "POST"
        && item.url.includes("/api/channels/none-c/messages"));
    if (!reopenUnseals) throw new Error("an unsealed thread must accept a new post");

    offBea();
    offDee();
    offCal();

    // ─── 6. The archived list is still reachable, from the roster ───

    const store = BossModStore.createStore({
        roster: [], threads: [], rosterQuery: "", needs: [], runtimePaused: false,
    });
    const rail = documentStub.createElement("aside");
    documentStub.body.append(rail);
    const unmountRoster = BossModRoster.mount(rail, {
        store, bus, apiFetch: api, navigate: noop, onHire: noop,
    });
    await tick();

    const archivedFilter = rail.querySelector("#channels-filter-archived");
    if (!archivedFilter) throw new Error("Archived filter missing from the roster");
    calls.length = 0;
    await archivedFilter.dispatchClick();
    await tick();
    if (!calls.some((item) => item.method === "GET" && item.url.includes("status=archived"))) {
        throw new Error("Archived filter must list archived threads");
    }
    const listed = rail.querySelectorAll(".roster-thread")
        .map((el) => el.getAttribute("data-thread-id"));
    const archivedFilterLists = listed.includes("open-a")
        && listed.includes("open-b")
        && !listed.includes("open-d")
        && !listed.includes("none-c");
    if (!archivedFilterLists) {
        throw new Error(`Archived filter must show sealed threads only, got ${listed.join(",")}`);
    }
    unmountRoster();

    process.stdout.write(JSON.stringify({
        ok: true,
        cancelAndArchive,
        archiveOnly,
        zeroOpenTwoButtons,
        openTasksThreeButtons,
        zeroOpenConfirm,
        zeroOpenCancelAborts,
        backAborts,
        archivedNotLive,
        honestyCopy: true,
        archivedFilterLists,
        reopenUnseals,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
