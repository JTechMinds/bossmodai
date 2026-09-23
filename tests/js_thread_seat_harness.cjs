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
    "BossModDom", "BossModStore", "BossModBus", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModOverlays",
    "BossModThreadArchive", "BossModThreadSeat", "BossModThreadRequests", "BossModThreadSource",
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
    members: [{ id: "debra", name: "Debra" }, { id: "jim", name: "Jim" }],
};
const messages = [
    { id: "m1", content: "Lock the requirements.", author_type: "human", author_name: "You" },
    { id: "m2", content: "Locked. Waiting on CLEAR.", author_type: "agent", author_name: "Debra", author_agent_id: "debra" },
];

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
        if (body.agent_id !== "hugh") {
            return { ok: false, status: 404, async json() { return { detail: "Agent not found" }; } };
        }
        thread.members = thread.members.concat([{ id: "hugh", name: "Hugh" }]);
        return { ok: true, async json() { return { ...thread, members: thread.members.slice() }; } };
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
        { id: "debra", name: "Debra", role: "Requirements Analyst" },
        { id: "jim", name: "Jim", role: "Eng" },
        { id: "hugh", name: "Hugh", role: "Code Auditor" },
    ],
    threads: [thread],
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const presence = BossModGates.createChannelPresenceController();

async function main() {
    const candidates = BossModThreadSeat.liveCandidates(
        store.getState().roster,
        thread.members.map((row) => row.id),
    );
    if (candidates.length !== 1 || candidates[0].id !== "hugh") {
        throw new Error("only the live non-member should be offered");
    }

    const empty = BossModThreadSeat.liveCandidates(store.getState().roster, ["debra", "jim", "hugh"]);
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
    const summary = await seater.pickAndSeat("t1", thread.members.filter((row) => row.id !== "hugh"));
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
    const liveChrome = source.chrome();
    if (!liveChrome.actions.some((item) => item.id === "channel-seat-btn")) {
        throw new Error("a live thread must offer Add to thread");
    }

    thread.status = "archived";
    const sealed = await source.load();
    if (sealed.length !== 2) throw new Error("an archived load must keep history");
    if (source.chrome().actions.some((item) => item.id === "channel-seat-btn")) {
        throw new Error("a sealed thread must not offer Add to thread");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        offeredOnlyHugh: true,
        emptyPickerFailClosed: true,
        seatedWithoutWipe: true,
        duplicateFailClosed: true,
        liveChromeOffersSeat: true,
        archivedHidesSeat: true,
    }));
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
