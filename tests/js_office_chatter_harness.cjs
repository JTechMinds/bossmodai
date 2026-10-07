/**
 * Node harness: the Office chatter panel (context/office-chatter.js).
 *
 * Invoked by tests/test_ui_office_chatter.py. Not a browser bundle.
 *
 * What is proven here is the reconciliation no source grep can reach: a live
 * row against a loaded page, a page that answers after the floor changed, the
 * trim, "Show older", and that destroy() leaves nothing subscribed. The API is
 * scripted per request so a test can hold one answer while it does something
 * else.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// core/markdown.js reads `marked`, `hljs` and `DOMParser`; the stub renders a
// message's source as ONE text node, which is what "inert" means here. The
// real sanitiser is tests/js_markdown_harness.cjs's subject.
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);

const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModClampedMarkdown", "BossModAvatar", "BossModStore",
    "BossModBus", "BossModFormat", "BossModGates", "BossModFloorScope", "BossModOfficeChatter",
];
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});
const { BossModStore, BossModBus, BossModOfficeChatter } = global;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

const ROSTER = [
    { id: "a1", name: "Jim", color: "#3b82f6", floorId: "lobby" },
    { id: "a2", name: "Laura", color: "#f59e0b", floorId: "lobby" },
    { id: "a3", name: "Ada", color: "#10b981", floorId: "finance" },
];
const FLOORS = [{ id: "lobby", name: "Lobby" }, { id: "finance", name: "Finance" }];

/** One row exactly as peer_message_event builds it. */
function row(id, from, to, content, floor = "lobby", minute = 0) {
    return {
        message_id: id, from_agent_id: from, to_agent_id: to, content, message_type: "social",
        created_at: `2026-10-07T09:${String(minute).padStart(2, "0")}:00+00:00`, floor_id: floor,
    };
}

function respond(body, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => (body === undefined ? Promise.reject(new Error("not json")) : Promise.resolve(body)),
    };
}

// Each request takes the next scripted answer: a value, or a held promise.
const requests = [];
const script = [];
function api(url) {
    requests.push(String(url));
    const next = script.shift();
    if (next === undefined) throw new Error(`unscripted request: ${url}`);
    return next instanceof Promise ? next : Promise.resolve(next);
}
function hold() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

async function main() {
    const store = BossModStore.createStore({ currentFloorId: "lobby", roster: [], floors: FLOORS });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const storeBaseline = store.subscriberCount();
    const busBaseline = bus.subscriberCount();
    const opened = [];

    // ─── Loading: the page is held, and so is the roster ───
    const first = hold();
    script.push(first.promise);
    const chatter = BossModOfficeChatter.createOfficeChatter({
        store, bus, api, openDesk: (id) => opened.push(id),
    });
    const el = chatter.element;
    documentStub.body.append(el);
    const text = (selector) => el.querySelectorAll(selector).map((node) => node.textContent).join(" | ");
    const rowsOf = () => el.querySelectorAll(".office-chatter-row");
    const olderBtn = () => el.querySelector(".office-chatter-older");
    await drain();
    const loadingRenders = text(".context-skeleton") === "Loading conversations…"
        && rowsOf().length === 0 && olderBtn().hidden === true
        && requests[0] === "/api/office/chatter?floor_id=lobby"
        && text(".context-title") === "Office chatter" && text(".context-meta") === "Lobby"
        && el.getAttribute("aria-labelledby") === el.querySelector(".context-title").id;

    // A live row while page 1 is in flight is held, then merged without doubling.
    bus.publish("peer_message", row("m3", "a2", "a1", "live during load", "lobby", 3));
    store.setState({ roster: ROSTER });
    first.resolve(respond({
        messages: [row("m3", "a2", "a1", "live during load", "lobby", 3),
            row("m2", "a1", "a2", "second", "lobby", 2), row("m1", "a2", "a1", "first", "lobby", 1)],
        has_more: false,
    }));
    await drain();
    const ids = () => rowsOf().map((node) => node.querySelector(".office-chatter-text").textContent);
    const readyRenders = ids().join(",") === "live during load,second,first"
        && text(".context-skeleton") === "" && text(".context-empty") === ""
        && el.querySelector("time").getAttribute("datetime") === "2026-10-07T09:03:00+00:00";
    const heldLiveRowMerged = rowsOf().length === 3;

    // ─── Live rows ───
    bus.publish("peer_message", row("m4", "a1", "a2", "newest", "lobby", 4));
    await drain();
    const livePrepended = ids()[0] === "newest" && rowsOf().length === 4;
    bus.publish("peer_message", row("x1", "a3", "a3", "other floor", "finance", 5));
    await drain();
    const otherFloorIgnored = rowsOf().length === 4;
    bus.publish("peer_message", row("m4", "a1", "a2", "newest", "lobby", 4));
    await drain();
    const duplicateIgnored = rowsOf().length === 4;
    // No page-1 cap: the first page held the whole history, so nothing was cut.
    const shortFloorNotTrimmed = olderBtn().hidden === true;

    // Off-roster participant: the row is not drawn.
    bus.publish("peer_message", row("m5", "a1", "ghost", "to someone gone", "lobby", 5));
    await drain();
    const offRosterHidden = rowsOf().length === 4 && !ids().includes("to someone gone");

    // HTML-looking content renders as text, never as a node.
    bus.publish("peer_message", row("m6", "a2", "a1", "<img src=x onerror=alert(1)><script>x()</script>", "lobby", 6));
    await drain();
    const inertHtml = ids()[0] === "<img src=x onerror=alert(1)><script>x()</script>"
        && el.querySelectorAll("img").length === 0 && el.querySelectorAll("script").length === 0;

    // Who-buttons open the desk and carry a name that contains the visible one.
    const whos = rowsOf()[0].querySelectorAll(".office-chatter-who");
    await whos[0].dispatchClick();
    await whos[1].dispatchClick();
    const whoOpensDesk = opened.join(",") === "a2,a1"
        && whos[0].getAttribute("aria-label") === "Open Laura's desk"
        && whos[0].getAttribute("type") === "button"
        && rowsOf()[0].querySelector(".office-chatter-arrow").getAttribute("aria-hidden") === "true";

    // A roster change that renames one agent repaints; an unrelated tick does not rebuild.
    const before = rowsOf()[1];
    store.setState({ roster: ROSTER.map((agent) => ({ ...agent })) });
    await drain();
    const tickKeepsRows = rowsOf()[1] === before;
    store.setState({ roster: ROSTER.map((agent) => (agent.id === "a1" ? { ...agent, name: "James" } : agent)) });
    await drain();
    const renameRepaints = text(".office-chatter-name").includes("James");
    store.setState({ roster: ROSTER });
    await drain();

    // A floor rename repaints the head's floor name and fetches nothing.
    const requestsBeforeRename = requests.length;
    store.setState({ floors: FLOORS.map((floor) => (floor.id === "lobby" ? { ...floor, name: "Ground" } : floor)) });
    await drain();
    const floorRenameRepaintsHead = text(".context-meta") === "Ground"
        && requests.length === requestsBeforeRename && rowsOf().length === 5;
    store.setState({ floors: FLOORS });
    await drain();

    // ─── A stale page after a floor switch is dropped; an empty floor says so ───
    const stale = hold();
    script.push(stale.promise);
    bus.publish("resync", {});
    await drain();
    script.push(respond({ messages: [], has_more: false }));
    store.setState({ currentFloorId: "finance" });
    await drain();
    stale.resolve(respond({ messages: [row("s1", "a1", "a2", "stale lobby page")], has_more: false }));
    await drain();
    const staleDropped = rowsOf().length === 0 && text(".context-meta") === "Finance"
        && requests[requests.length - 1] === "/api/office/chatter?floor_id=finance";
    const emptyRenders = text(".context-empty") === "No one on this floor has messaged a coworker yet.";

    // ─── Error with retry ───
    script.push(respond({ detail: "Floor not found" }, 404));
    store.setState({ currentFloorId: "lobby" });
    await drain();
    const retry = el.querySelector(".office-chatter-state").querySelector("button");
    const errorRenders = text(".context-error") === "Floor not found"
        && el.querySelector(".context-error").getAttribute("role") === "alert"
        && Boolean(retry) && retry.textContent === "Try again" && rowsOf().length === 0;
    script.push(respond(undefined, 503));
    await retry.dispatchClick();
    await drain();
    const statusWhenNoDetail = text(".context-error") === "HTTP 503";

    // ─── Show older, and the trim ───
    script.push(respond({
        messages: [row("p3", "a1", "a2", "p3", "lobby", 30), row("p2", "a2", "a1", "p2", "lobby", 20)],
        has_more: true,
    }));
    await el.querySelector(".office-chatter-state").querySelector("button").dispatchClick();
    await drain();
    const retryRecovers = text(".context-error") === "" && ids().join(",") === "p3,p2"
        && olderBtn().hidden === false;
    bus.publish("peer_message", row("p4", "a2", "a1", "p4", "lobby", 40));
    await drain();
    const trimmedToFirstPage = ids().join(",") === "p4,p3" && olderBtn().hidden === false;

    script.push(respond({ detail: "boom" }, 500));
    await olderBtn().dispatchClick();
    await drain();
    const olderFailureKeepsRows = ids().join(",") === "p4,p3" && text(".context-error") === "boom";

    script.push(respond({
        messages: [row("p2", "a2", "a1", "p2", "lobby", 20), row("p1", "a1", "a2", "p1", "lobby", 10)],
        has_more: false,
    }));
    await olderBtn().dispatchClick();
    await drain();
    const olderSendsBefore = requests[requests.length - 1] === "/api/office/chatter?floor_id=lobby&before=p3";
    const olderAppends = ids().join(",") === "p4,p3,p2,p1" && olderBtn().hidden === true
        && text(".context-error") === "";
    bus.publish("peer_message", row("p5", "a1", "a2", "p5", "lobby", 50));
    await drain();
    const noTrimAfterOlder = ids().join(",") === "p5,p4,p3,p2,p1";

    // ─── Teardown ───
    const late = hold();
    script.push(late.promise);
    bus.publish("resync", {});
    chatter.destroy();
    late.resolve(respond({ messages: [row("z1", "a1", "a2", "after destroy")], has_more: false }));
    await drain();
    const destroyDrains = store.subscriberCount() === storeBaseline
        && bus.subscriberCount() === busBaseline && !ids().includes("after destroy");

    let missingDepThrows = false;
    try {
        BossModOfficeChatter.createOfficeChatter({ store, bus, api });
    } catch (err) {
        missingDepThrows = /openDesk/.test(err.message);
    }

    process.stdout.write(JSON.stringify({
        loadingRenders,
        readyRenders,
        heldLiveRowMerged,
        livePrepended,
        otherFloorIgnored,
        duplicateIgnored,
        shortFloorNotTrimmed,
        offRosterHidden,
        inertHtml,
        whoOpensDesk,
        tickKeepsRows,
        renameRepaints,
        floorRenameRepaintsHead,
        staleDropped,
        emptyRenders,
        errorRenders,
        statusWhenNoDetail,
        retryRecovers,
        trimmedToFirstPage,
        olderFailureKeepsRows,
        olderSendsBefore,
        olderAppends,
        noTrimAfterOlder,
        destroyDrains,
        missingDepThrows,
    }) + "\n");
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
