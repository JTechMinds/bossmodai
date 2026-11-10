/**
 * Node harness: the thinking indicator is scoped to the selected conversation.
 * Invoked by tests/test_ui_chat_typing.py. Not a browser bundle.
 *
 * Phase 2A replaced the predicate-guarded createChatTypingController with a
 * presence model keyed `conversationId::agentId`. Presence is page-lifetime
 * now: the rows live in the store (core/presence.js, keyed agent + channel)
 * and each conversation's source SELECTS its own (`presenceFor`) for the
 * transcript to paint. The five properties are unchanged and re-proven
 * against that pair, with real agent sources; the emitted payload keys are
 * byte-identical.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const [
    domPath, storePath, busPath, presencePath, noteGroupPath, transcriptPath,
    agentRequestsPath, agentSourcePath, agentMenuPath,
] = process.argv.slice(2);
const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
load(domPath, "BossModDom");
load(storePath, "BossModStore");
load(busPath, "BossModBus");
load(presencePath, "BossModPresence");
load(noteGroupPath, "BossModNoteGroup");
load(transcriptPath, "BossModTranscript");
load(agentRequestsPath, "BossModAgentRequests");
load(agentSourcePath, "BossModAgentSource");
load(agentMenuPath, "BossModAgentMenu");

const {
    BossModStore, BossModBus, BossModPresence, BossModTranscript, BossModAgentSource, BossModAgentMenu,
} = global;

const transcript = BossModTranscript.createTranscript({
    renderMessage() {
        throw new Error("this harness paints presence only");
    },
    renderEventCard() {
        throw new Error("this harness paints presence only");
    },
});
const slot = transcript.element.querySelector(".transcript-presence");
if (!slot) throw new Error("the transcript must expose a presence slot");

// The page's store and bus, presence attached as shell.js does it, and two
// real DM sources. Only the selection under test is exercised, so the API is
// never reached.
const api = async (url) => { throw new Error(`unexpected request ${url}`); };
const store = BossModStore.createStore({ roster: [], presence: [] });
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
BossModPresence.attach({ store, bus });
const agentMenu = BossModAgentMenu.create({ api, store, navigate() {} });
const sources = {
    a: BossModAgentSource.createAgentSource("a", { api, bus, store, agentMenu }),
    b: BossModAgentSource.createAgentSource("b", { api, bus, store, agentMenu }),
};

const since = new Date().toISOString();
const publish = (agentId, name, phase) => bus.publish("agent_presence", {
    agent_id: agentId, agent_name: name, phase, since,
});
function show(conversationId) {
    transcript.renderPresence(sources[conversationId].presenceFor(store.getState().presence));
}
function rows() {
    return slot.children.map((row) => row.textContent);
}

// paintsSelected — the conversation on screen shows its own member.
publish("a", "Ada", "thinking");
show("a");
const paintsSelected = rows().length === 1 && rows()[0] === "Ada is thinking...";
if (!paintsSelected) throw new Error(`expected Ada's row, got ${JSON.stringify(rows())}`);

// ignoresForeignHide — another agent's idle leaves this one alone.
const beforeForeignIdle = store.getState().presence;
publish("b", "Bo", "idle");
show("a");
const ignoresForeignHide = rows().length === 1 && store.getState().presence === beforeForeignIdle;
if (!ignoresForeignHide) throw new Error("a foreign idle must not clear this conversation");

// hidesOnSwitch — switching away shows nothing that belongs to `a`.
show("b");
const hidesOnSwitch = rows().length === 0;
if (!hidesOnSwitch) throw new Error("switching conversation must not carry a row across");

// restoresOnReturn — `a`'s member is still mid-turn, so it comes back.
show("a");
const restoresOnReturn = rows().length === 1 && rows()[0] === "Ada is thinking...";
if (!restoresOnReturn) throw new Error("returning must restore the pending indicator");

// ignoresForeignShow — another agent's thinking never leaks in.
publish("b", "Bo", "thinking");
show("a");
const ignoresForeignShow = rows().length === 1 && rows()[0] === "Ada is thinking...";
if (!ignoresForeignShow) throw new Error("another conversation's start must not paint here");
if (!BossModPresence.agentRow(store.getState().presence, "b")) {
    throw new Error("the foreign start must still be recorded");
}

// Ids are the server's strings. A number is a wiring bug, refused rather than
// quietly keyed under a coerced name.
let refusesNonStringId = false;
try {
    BossModPresence.applyEvent(store.getState().presence, { agent_id: 1, agent_name: "One", phase: "thinking", since });
} catch (err) {
    refusesNonStringId = /agent_id/.test(err.message);
}
if (!refusesNonStringId) throw new Error("a numeric agent_id must be refused, naming the field");

// The clock a thinking row starts must not hold this process open.
transcript.destroy();

process.stdout.write(JSON.stringify({
    ok: true,
    paintsSelected,
    ignoresForeignHide,
    hidesOnSwitch,
    restoresOnReturn,
    ignoresForeignShow,
}));
