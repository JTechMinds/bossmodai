/**
 * Node harness: per-member channel presence is keyed by channel + agent.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = {
    createElement() {
        return { textContent: "", innerHTML: "", style: {}, classList: { toggle() {} } };
    },
    getElementById() {
        return null;
    },
    addEventListener() {},
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModUtils = BossModUtils;\n`);

if (typeof BossModUtils.createChannelPresenceController !== "function") {
    throw new Error("createChannelPresenceController missing");
}

const presence = BossModUtils.createChannelPresenceController();
if (presence.start("ch-1", "jim", "Jim") !== true) {
    throw new Error("start must accept a channel member");
}
if (presence.start("ch-1", "laura", "Laura") !== true) {
    throw new Error("start must accept a second member");
}
if (presence.start("ch-2", "jim", "Jim") !== true) {
    throw new Error("same agent in another channel must be independent");
}

const first = presence.list("ch-1");
if (first.length !== 2 || !presence.has("ch-1", "jim") || !presence.has("ch-1", "laura")) {
    throw new Error("ch-1 must list both in-flight members");
}
if (!presence.has("ch-2", "jim") || presence.has("ch-2", "laura")) {
    throw new Error("presence must stay per-channel");
}

if (presence.stop("ch-1", "jim") !== true || presence.has("ch-1", "jim")) {
    throw new Error("stop must clear that member only");
}
if (!presence.has("ch-1", "laura") || !presence.has("ch-2", "jim")) {
    throw new Error("stop must not clear other members or channels");
}

if (presence.stopAll("ch-1") !== 1 || presence.list("ch-1").length !== 0) {
    throw new Error("stopAll must clear one channel");
}
if (!presence.has("ch-2", "jim")) {
    throw new Error("stopAll must leave other channels alone");
}

process.stdout.write(JSON.stringify({
    ok: true,
    tracksMembers: true,
    isolatesChannels: true,
    stopsOne: true,
    stopsAll: true,
}));
