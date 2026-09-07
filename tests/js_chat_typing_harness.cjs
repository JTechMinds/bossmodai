/**
 * Node harness: the thinking indicator is scoped to the selected conversation.
 * Invoked by tests/test_ui_chat_typing.py. Not a browser bundle.
 *
 * Phase 2A replaced the predicate-guarded createChatTypingController with a
 * presence model keyed `conversationId::agentId` and repainted by the
 * transcript. The five properties are unchanged and re-proven against the new
 * pair; the emitted payload keys are byte-identical.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const [domPath, gatesPath, transcriptPath] = process.argv.slice(2);
const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
load(domPath, "BossModDom");
load(gatesPath, "BossModGates");
load(transcriptPath, "BossModTranscript");

const BossModGates = global.BossModGates;
const BossModTranscript = global.BossModTranscript;
if (typeof BossModGates.createChannelPresenceController !== "function") {
    throw new Error("createChannelPresenceController missing");
}

const presence = BossModGates.createChannelPresenceController();
const transcript = BossModTranscript.createTranscript({
    presence,
    renderMessage() {
        throw new Error("this harness paints presence only");
    },
    renderEventCard() {
        throw new Error("this harness paints presence only");
    },
});
const slot = transcript.element.querySelector(".transcript-presence");
if (!slot) throw new Error("the transcript must expose a presence slot");

function rows() {
    return slot.children.map((row) => row.textContent);
}

// paintsSelected — the conversation on screen shows its own member.
presence.start("a", "a", "Ada");
transcript.renderPresence("a");
const paintsSelected = rows().length === 1 && rows()[0] === "Ada is thinking...";
if (!paintsSelected) throw new Error(`expected Ada's row, got ${JSON.stringify(rows())}`);

// ignoresForeignHide — a stop in another conversation leaves this one alone.
presence.stop("b", "b");
transcript.renderPresence("a");
const ignoresForeignHide = rows().length === 1;
if (!ignoresForeignHide) throw new Error("a foreign stop must not clear this conversation");

// hidesOnSwitch — switching away shows nothing that belongs to `a`.
transcript.renderPresence("b");
const hidesOnSwitch = rows().length === 0;
if (!hidesOnSwitch) throw new Error("switching conversation must not carry a row across");

// restoresOnReturn — `a`'s member is still mid-turn, so it comes back.
transcript.renderPresence("a");
const restoresOnReturn = rows().length === 1 && rows()[0] === "Ada is thinking...";
if (!restoresOnReturn) throw new Error("returning must restore the pending indicator");

// ignoresForeignShow — a start in another conversation never leaks in.
presence.start("b", "b", "Bo");
transcript.renderPresence("a");
const ignoresForeignShow = rows().length === 1 && rows()[0] === "Ada is thinking...";
if (!ignoresForeignShow) throw new Error("another conversation's start must not paint here");
if (!presence.has("b", "b")) throw new Error("the foreign start must still be recorded");

// Numeric and string ids name the same member, as they did before.
presence.start(1, 1, "One");
if (!presence.has("1", "1")) throw new Error("ids must key the same whether string or number");
presence.stop(1, 1);
if (presence.has("1", "1")) throw new Error("stop must clear the same key show set");

process.stdout.write(JSON.stringify({
    ok: true,
    paintsSelected,
    ignoresForeignHide,
    hidesOnSwitch,
    restoresOnReturn,
    ignoresForeignShow,
}));
