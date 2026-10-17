/**
 * Node harness: the desk's Processes section (context/desk-processes.js).
 * Invoked by tests/test_ui_desk_processes.py. Not a browser bundle.
 *
 * Against the real section, the real overlays and the real bus, with a
 * scripted `api`: its loading, empty, error-with-retry and rows states; Stop
 * POSTs and repaints, and a refused Stop is said; Log opens a layer with the
 * fresh tail; activity for this agent repaints and for another does not;
 * destroy closes an open log.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const NAMES = ["BossModApi", "BossModDom", "BossModFormat", "BossModGates", "BossModBus",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays",
    "BossModDeskProcesses"];
const WINDOW_PUBLISHED = new Set(["BossModApi"]);
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    const source = WINDOW_PUBLISHED.has(name) ? `window.${name}` : name;
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${source};\n`);
});
const { BossModBus, BossModDeskProcesses } = global;

const MINUTE = 60 * 1000;
const RUNNING = {
    id: "p1", agent_id: "a1", command: "npx vite --port 5199", cwd: "/projects/site", pid: 4242,
    status: "running", stopped_by: null, stop_reason: null,
    started_at: new Date(Date.now() - 5 * MINUTE).toISOString(), ended_at: null,
    checkin_requested_at: new Date().toISOString(), log_tail: "VITE ready in 300 ms\n", log_error: null,
};
const ENDED = {
    id: "p2", agent_id: "a1", command: "node serve.mjs", cwd: "/me", pid: 4343,
    status: "stopped", stopped_by: "operator", stop_reason: "Stopped by you",
    started_at: new Date(Date.now() - 90 * MINUTE).toISOString(),
    ended_at: new Date(Date.now() - 10 * MINUTE).toISOString(),
    checkin_requested_at: null, log_tail: "", log_error: null,
};

function jsonResponse(body, status = 200) {
    return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

/** A scripted api: `answer` decides each response; every call is recorded. */
function scriptedApi(answer) {
    const calls = [];
    const api = (url, init) => {
        calls.push({ url: String(url), method: (init && init.method) || "GET" });
        return answer(String(url), init || {});
    };
    return { api, calls };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function texts(root, selector) {
    return root.querySelectorAll(selector).map((el) => el.textContent);
}

async function main() {
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const results = {};

    // 1. Loading, then rows: running first with its check-in tone and Stop; ended without Stop.
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    const first = scriptedApi((url, init) => {
        if (init.method === "POST") return jsonResponse({ ...RUNNING, status: "stopped" });
        return held.then(() => jsonResponse([RUNNING, ENDED]));
    });
    const section = BossModDeskProcesses.createDeskProcesses({ api: first.api, bus, agentId: "a1" });
    results.loadingShows = section.element.textContent.includes("Loading background processes…");
    release();
    await tick(); await tick();
    const rows = section.element.querySelectorAll(".desk-process");
    results.rowsRender = rows.length === 2
        && rows[0].getAttribute("data-process-id") === "p1"
        && texts(rows[0], ".desk-process-command")[0] === "npx vite --port 5199"
        && texts(rows[0], ".desk-process-meta")[0] === "/projects/site · Running for 5m"
        && texts(rows[0], ".desk-process-tone")[0] === "Check-in waiting"
        // A question, not an error: the calm "ask" tone (--accent), never "alert".
        && rows[0].querySelector(".desk-process-tone").getAttribute("data-tone") === "ask"
        && Boolean(rows[0].querySelector("[data-action='stop']"))
        && texts(rows[1], ".desk-process-meta")[0] === "/me · Ended 10m ago · Stopped by you"
        && !rows[1].querySelector("[data-action='stop']")
        && rows[1].querySelectorAll(".desk-process-tone").length === 0;
    results.rowsAreLabelled = rows[0].querySelector("[data-action='stop']").getAttribute("aria-label")
        === "Stop npx vite --port 5199"
        && rows[0].querySelector("[data-action='log']").getAttribute("aria-label")
        === "Show the log of npx vite --port 5199";

    // 2. Stop POSTs to the process's route, then re-reads.
    const readsBeforeStop = first.calls.filter((call) => call.method === "GET").length;
    await rows[0].querySelector("[data-action='stop']").dispatchClick();
    await tick(); await tick();
    results.stopPosts = first.calls.some((call) => call.method === "POST" && call.url === "/api/agent-processes/p1/stop")
        && first.calls.filter((call) => call.method === "GET").length === readsBeforeStop + 1;

    // 3. Log opens a layer with the fresh tail; destroy closes it.
    await section.element.querySelector("[data-action='log']").dispatchClick();
    await tick(); await tick();
    const panel = documentStub.body.querySelector(".modal-panel");
    results.logOpensWithTheTail = Boolean(panel)
        && texts(panel, ".log-pre")[0] === "VITE ready in 300 ms"
        && texts(panel, ".desk-process-command")[0] === "npx vite --port 5199";

    // 4. Activity for this agent repaints (debounced); for another agent it does not.
    const readsBeforeActivity = first.calls.filter((call) => call.method === "GET").length;
    bus.publish("activity", { event: "agent_process_changed", agent_id: "someone-else" });
    bus.publish("activity", { event: "schedule_changed", agent_id: "a1" });
    await wait(600);
    const ignored = first.calls.filter((call) => call.method === "GET").length === readsBeforeActivity;
    bus.publish("activity", { event: "agent_process_checkin", agent_id: "a1" });
    bus.publish("activity", { event: "agent_process_changed", agent_id: "a1" });
    await wait(600);
    await tick();
    results.activityRepaintsOnlyThisAgent = ignored
        && first.calls.filter((call) => call.method === "GET").length === readsBeforeActivity + 1;

    section.destroy();
    results.destroyClosesTheLog = documentStub.body.querySelectorAll(".modal-panel").length === 0;

    // 5. Empty.
    const empty = BossModDeskProcesses.createDeskProcesses({
        api: scriptedApi(() => jsonResponse([])).api, bus, agentId: "a2",
    });
    await tick(); await tick();
    results.emptySays = empty.element.textContent === "Nothing running in the background.";
    empty.destroy();

    // 6. Error with a retry that recovers.
    let failing = true;
    const flaky = BossModDeskProcesses.createDeskProcesses({
        api: scriptedApi(() => (failing ? jsonResponse({ detail: "boom" }, 500) : jsonResponse([ENDED]))).api,
        bus, agentId: "a3",
    });
    await tick(); await tick();
    results.errorSays = flaky.element.textContent.includes("Could not load background processes.")
        && Boolean(flaky.element.querySelector("#desk-processes-retry"))
        && flaky.element.querySelectorAll("[role='alert']").length >= 1;
    failing = false;
    await flaky.element.querySelector("#desk-processes-retry").dispatchClick();
    await tick(); await tick();
    results.retryRecovers = flaky.element.querySelectorAll(".desk-process").length === 1;
    flaky.destroy();

    // 7. A refused Stop is said, and the button comes back.
    const refusing = BossModDeskProcesses.createDeskProcesses({
        api: scriptedApi((url, init) => (init.method === "POST"
            ? jsonResponse({ detail: "No running background process with that id" }, 404)
            : jsonResponse([RUNNING]))).api,
        bus, agentId: "a4",
    });
    await tick(); await tick();
    const stopButton = refusing.element.querySelector("[data-action='stop']");
    await stopButton.dispatchClick();
    await tick(); await tick();
    results.refusedStopIsSaid = refusing.element.textContent
        .includes("Could not stop it: No running background process with that id")
        && stopButton.disabled === false && stopButton.textContent === "Stop";
    refusing.destroy();

    // 8. Missing dependencies are refused, not silently no-opped.
    let refusesMissingDeps = false;
    try {
        BossModDeskProcesses.createDeskProcesses({ api: () => null, bus });
    } catch (err) {
        refusesMissingDeps = true;
    }
    results.refusesMissingDeps = refusesMissingDeps;

    process.stdout.write(JSON.stringify({ ok: true, ...results }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
