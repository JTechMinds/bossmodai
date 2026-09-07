/**
 * Node harness: incremental transcript updates never remount or clear a draft.
 * Invoked by tests/test_meeting_ui_incremental.py. Not a browser bundle.
 *
 * Phase 2A merged the meeting sub-view into the one conversation surface, so
 * the three properties this harness has always guarded now belong to
 * conversation/transcript.js: the shell stays mounted across appends, a
 * sibling composer's draft is never touched, and a repeated message id renders
 * once. The emitted payload is unchanged.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");

installDom();

const [domPath, gatesPath, transcriptPath] = process.argv.slice(2);
const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
load(domPath, "BossModDom");
load(gatesPath, "BossModGates");
load(transcriptPath, "BossModTranscript");

const BossModTranscript = global.BossModTranscript;
if (typeof BossModTranscript !== "object" || BossModTranscript === null) {
    throw new Error("BossModTranscript missing");
}

function message(key, author, text) {
    return {
        key,
        author: "agent",
        authorName: author,
        showAuthor: true,
        text,
        createdAt: "",
        kind: "message",
        card: null,
        deskPath: null,
        systemReceipt: false,
    };
}

const transcript = BossModTranscript.createTranscript({
    presence: global.BossModGates.createChannelPresenceController(),
    renderMessage(m) {
        const node = document.createElement("div");
        node.className = "msg";
        node.textContent = `${m.authorName}: ${m.text}`;
        return node;
    },
    renderEventCard() {
        throw new Error("this harness sends only ordinary messages");
    },
});

// The mounted session shell: transcript plus the composer that sits under it.
const session = document.createElement("div");
const composer = document.createElement("textarea");
composer.value = "draft still here";
session.append(transcript.element, composer);

const listing = transcript.element.querySelector("[data-transcript]");
const mounted = Boolean(listing && transcript.element.querySelector(".transcript-list"));
if (!mounted) throw new Error("the transcript shell must mount a list");

listing.clientHeight = 40;
listing.scrollHeight = 80;
listing.scrollTop = 40;

const first = transcript.append(message("m1", "Ada", "hello"));
const duplicate = transcript.append(message("m1", "Ada", "hello again"));
if (first !== true || duplicate !== false) {
    throw new Error("append should add once and dedup by key");
}
transcript.append(message("m2", "Grace", "ack"));

if (transcript.messageCount() !== 2) {
    throw new Error(`expected 2 message nodes, got ${transcript.messageCount()}`);
}
if (transcript.element !== session.children[0]) {
    throw new Error("an incremental append must not remount the shell");
}
if (composer.value !== "draft still here") {
    throw new Error("append must not touch the composer");
}

// Scrolled away from the bottom, the append lands silently and the prior
// scroll position survives — the operator is reading, not being dragged.
listing.scrollHeight = 1000;
listing.scrollTop = 100;
transcript.append(message("m3", "Ada", "one more"));
if (listing.scrollTop !== 100) {
    throw new Error("sync while away from bottom should keep the prior scroll");
}
if (composer.value !== "draft still here") {
    throw new Error("in-place sync must preserve typed draft");
}
transcript.setMessages([message("m1", "Ada", "hello"), message("m2", "Grace", "ack")]);
if (composer.value !== "draft still here") {
    throw new Error("a full repaint must still leave the composer alone");
}

process.stdout.write(JSON.stringify({
    ok: true,
    mounted,
    preservedDraft: composer.value === "draft still here",
    messageCount: transcript.messageCount(),
}));
