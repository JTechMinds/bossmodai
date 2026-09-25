/**
 * Round markers stay on the channel for the engine; the thread adapter must
 * not paint them in the operator transcript (load or live WS).
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModStore", "BossModBus", "BossModOperatorInvalidate", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModOverlays", "BossModMenu",
    "BossModThreadArchive", "BossModThreadSeat", "BossModThreadRequests", "BossModThreadSource",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const {
    BossModStore, BossModBus, BossModGates,
    BossModThreadArchive, BossModThreadSeat, BossModThreadSource,
} = global;

const thread = {
    id: "t1",
    name: "Debra, Jim",
    status: "active",
    members: [{ id: "debra", name: "Debra" }, { id: "jim", name: "Jim" }],
};
const wire = [
    {
        id: "m1",
        content: "Ship the fix.",
        author_type: "human",
        author_name: "You",
    },
    {
        id: "m2",
        content: "Round 2",
        author_type: "system",
        author_name: "BossMod",
        notification_kind: "channel_round_marker",
    },
    {
        id: "m3",
        content: "On it.",
        author_type: "agent",
        author_name: "Debra",
        author_agent_id: "debra",
    },
    {
        id: "m4",
        content: "Thread paused.",
        author_type: "system",
        author_name: "BossMod",
        notification_kind: "thread_paused",
    },
];

const api = async (url) => {
    if (String(url).startsWith("/api/channels/t1") && !String(url).includes("/members")) {
        return {
            ok: true,
            async json() {
                return { channel: { ...thread }, messages: wire.slice() };
            },
        };
    }
    throw new Error(`unhandled ${url}`);
};

const store = BossModStore.createStore({
    roster: [
        { id: "debra", name: "Debra", role: "Eng" },
        { id: "jim", name: "Jim", role: "Eng" },
    ],
    threads: [thread],
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
BossModOperatorInvalidate.attach({ bus });
const presence = BossModGates.createChannelPresenceController();

async function main() {
    const source = BossModThreadSource.createThreadSource("t1", {
        api,
        bus,
        presence,
        archive: BossModThreadArchive.createThreadArchive({ api }),
        seat: BossModThreadSeat.createThreadSeat({ api, store }),
        forgetCache() {},
        store,
    });

    const loaded = await source.load();
    const loadHidesRound = loaded.every((row) => row.text !== "Round 2")
        && loaded.some((row) => row.text === "Ship the fix.")
        && loaded.some((row) => row.text === "On it.")
        && loaded.some((row) => row.text === "Thread paused.");

    const painted = [];
    const sub = source.subscribe({
        message(msg) { painted.push(msg); },
        reset() {},
        presence() {},
        chrome() {},
    });

    sub.onLiveEvent("channel_message", {
        channel_id: "t1",
        message_id: "live-round",
        content: "Round 3",
        author_type: "system",
        author_name: "BossMod",
        notification_kind: "channel_round_marker",
    });
    sub.onLiveEvent("channel_message", {
        channel_id: "t1",
        message_id: "live-ok",
        content: "Still here.",
        author_type: "agent",
        author_name: "Jim",
        author_agent_id: "jim",
    });
    sub.dispose();

    const liveHidesRound = painted.every((row) => row.text !== "Round 3")
        && painted.some((row) => row.text === "Still here.");

    process.stdout.write(JSON.stringify({
        ok: loadHidesRound && liveHidesRound,
        loadHidesRound,
        liveHidesRound,
        loadedTexts: loaded.map((row) => row.text),
        paintedTexts: painted.map((row) => row.text),
    }) + "\n");
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
