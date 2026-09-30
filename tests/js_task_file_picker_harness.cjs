/**
 * Node harness: the task detail's file picker, built from the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * Opens the real picker in the real modal against a recording api whose
 * answers the harness releases by hand, so a late answer can be proved
 * dropped. It lists /projects, walks into a folder, selects a file (which
 * fills the name), names a new one, and hands `<folder>/<name>` back; it
 * refuses a name with a `/` and the `/` mount list; and a failed listing
 * says so with Retry and "Go to /".
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

installDom();
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModFormat", "BossModGates",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays",
    "BossModFileListing", "BossModTaskFilePicker",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const NOW = new Date().toISOString();
const crumbsTo = (path) => {
    const crumbs = [{ label: "/", path: "/" }];
    let at = "";
    path.split("/").filter(Boolean).forEach((part) => {
        at += `/${part}`;
        crumbs.push({ label: part, path: at });
    });
    return crumbs;
};
const folder = (path, entries) => ({ kind: "directory", path, name: path, breadcrumbs: crumbsTo(path), entries });
const LISTINGS = {
    "/": folder("/", [
        { name: "me", path: "/me", is_dir: true, updated_at: NOW },
        { name: "projects", path: "/projects", is_dir: true, updated_at: NOW },
    ]),
    "/projects": folder("/projects", [
        { name: "reports", path: "/projects/reports", is_dir: true, updated_at: NOW },
        { name: "plan.md", path: "/projects/plan.md", is_dir: false, size_bytes: 120, updated_at: NOW },
    ]),
    "/projects/reports": folder("/projects/reports", [
        { name: "q3.md", path: "/projects/reports/q3.md", is_dir: false, size_bytes: 2048, updated_at: NOW },
    ]),
};

/** Every request, in order: `{path, resolve}`; answered by the harness. */
const pending = [];
const requested = [];

function api(url, init) {
    const match = /^\/api\/agents\/([^/]+)\/desk\?path=(.*)$/.exec(String(url));
    if (!match) throw new Error(`[task-file-picker-harness] unexpected request ${url}`);
    if (!init || init.cache !== "no-store") throw new Error("[task-file-picker-harness] a listing must not be cached");
    const path = decodeURIComponent(match[2]);
    requested.push({ agent: decodeURIComponent(match[1]), path });
    return new Promise((resolve) => pending.push({ path, resolve }));
}

/** Answer the oldest request for `path` with its listing, or with a failure. */
function answer(path, failure) {
    const at = pending.findIndex((item) => item.path === path);
    if (at === -1) throw new Error(`[task-file-picker-harness] no request for ${path} is waiting`);
    const [item] = pending.splice(at, 1);
    item.resolve(failure
        ? { ok: false, status: failure.status, json: () => Promise.resolve({ detail: failure.detail }) }
        : { ok: true, status: 200, json: () => Promise.resolve(LISTINGS[path]) });
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

async function click(el, what) {
    if (!el) fail(`nothing to click: ${what}`);
    await el.dispatchClick();
    await drain();
}

function type(input, text) {
    input.value = text;
    input.dispatchEvent({ type: "input" });
}

const layers = () => document.body.querySelectorAll(".modal-panel");
const rowFor = (panel, path) => panel.querySelectorAll(".desk-entry").find((row) => row.getAttribute("data-path") === path) || null;

async function main() {
    const picks = [];
    const handle = global.BossModTaskFilePicker.open({
        api, agentId: "a 1", startPath: "/projects", onPick: (path) => picks.push(path),
    });
    await drain();
    const panel = layers().pop();
    const use = panel.querySelector("#task-file-picker-use");
    const name = panel.querySelector("#task-file-picker-name");
    const label = panel.querySelectorAll("label").find((node) => node.getAttribute("for") === "task-file-picker-name");

    // ── opensAsALayer ───────────────────────────────────────────────────
    const loadingFirst = Boolean(panel.querySelector(".context-skeleton")) && use.disabled;
    answer("/projects");
    await drain();
    const opensAsALayer = typeof handle.close === "function" && layers().length === 1
        && panel.getAttribute("aria-label") === "Choose a file" && panel.getAttribute("data-size") === "panel"
        && Boolean(label) && label.textContent === "File name" && name.classList.contains("assign-input")
        && document.activeElement === name && loadingFirst;
    if (!opensAsALayer) fail("the picker is not a labelled panel layer that starts in the name field");

    // ── listsTheStartFolder ─────────────────────────────────────────────
    const crumbs = panel.querySelectorAll(".desk-crumb").map((node) => node.getAttribute("data-path"));
    const folderRow = rowFor(panel, "/projects/reports");
    const fileRow = rowFor(panel, "/projects/plan.md");
    const listsTheStartFolder = requested.length === 1 && requested[0].agent === "a 1"
        && requested[0].path === "/projects" && crumbs.join(",") === "/,/projects"
        && Boolean(folderRow) && !folderRow.hasAttribute("aria-pressed")
        && Boolean(fileRow) && fileRow.getAttribute("aria-pressed") === "false" && use.disabled;
    if (!listsTheStartFolder) fail(`the start folder listed crumbs [${crumbs}] after ${JSON.stringify(requested)}`);

    // ── fileSelectsAndFillsTheName ──────────────────────────────────────
    await click(fileRow, "plan.md");
    const fileSelectsAndFillsTheName = fileRow.getAttribute("aria-pressed") === "true"
        && name.value === "plan.md" && !use.disabled;
    if (!fileSelectsAndFillsTheName) fail(`selecting plan.md left the name "${name.value}"`);

    // ── folderNavigates (the typed name survives the move) ──────────────
    await click(folderRow, "the reports folder");
    const whileLoading = use.disabled;
    answer("/projects/reports");
    await drain();
    const inner = rowFor(panel, "/projects/reports/q3.md");
    const folderNavigates = whileLoading && requested[1].path === "/projects/reports" && Boolean(inner)
        && inner.getAttribute("aria-pressed") === "false" && name.value === "plan.md";
    if (!folderNavigates) fail("a folder row did not open that folder");

    // ── refusesBadNames ─────────────────────────────────────────────────
    type(name, "sub/file.md");
    const slashRefused = use.disabled;
    type(name, "..");
    const dotsRefused = use.disabled;
    type(name, "   ");
    const blankRefused = use.disabled;
    await click(use, "Use this file (refused)");
    const refusesBadNames = slashRefused && dotsRefused && blankRefused && picks.length === 0 && layers().length === 1;
    if (!refusesBadNames) fail("a name with a slash, a dot-dot or nothing was accepted");

    // ── typedNameSelects: an existing name marks its row; a new one none ─
    type(name, "q3.md");
    const typedExisting = inner.getAttribute("aria-pressed") === "true";
    type(name, "summary.md");
    const typedNew = inner.getAttribute("aria-pressed") === "false" && !use.disabled;
    const typedNameSelects = typedExisting && typedNew;
    if (!typedNameSelects) fail("the selected row disagrees with the typed name");

    // ── pickHandsBackTheJoinedPath ──────────────────────────────────────
    await click(use, "Use this file");
    const pickHandsBackTheJoinedPath = picks.join(",") === "/projects/reports/summary.md" && layers().length === 0;
    if (!pickHandsBackTheJoinedPath) fail(`the pick handed back [${picks}]`);

    // ── rootIsRefused, staleListingIsDropped ────────────────────────────
    const second = global.BossModTaskFilePicker.open({
        api, agentId: "a1", startPath: "/projects/reports", onPick: (path) => picks.push(path),
    });
    await drain();
    answer("/projects/reports");
    await drain();
    const again = layers().pop();
    const rootCrumb = again.querySelectorAll(".desk-crumb").find((node) => node.getAttribute("data-path") === "/");
    const projectsCrumb = again.querySelectorAll(".desk-crumb")
        .find((node) => node.getAttribute("data-path") === "/projects");
    // Two quick clicks: /projects is left for / before it answers, and its
    // answer lands last.
    await click(projectsCrumb, "the /projects crumb");
    await click(rootCrumb, "the root crumb");
    answer("/");
    await drain();
    answer("/projects");
    await drain();
    const shown = again.querySelectorAll(".desk-entry").map((row) => row.getAttribute("data-path")).join(",");
    const staleListingIsDropped = shown === "/me,/projects";
    if (!staleListingIsDropped) fail(`a late /projects answer painted over /: [${shown}]`);
    const secondUse = again.querySelector("#task-file-picker-use");
    const secondName = again.querySelector("#task-file-picker-name");
    type(secondName, "loose.md");
    const rootIsRefused = secondUse.disabled;
    await click(secondUse, "Use this file at /");
    if (!rootIsRefused || picks.length !== 1) fail("the / mount list was accepted as a folder");

    // ── errorOffersRetry ────────────────────────────────────────────────
    await click(again.querySelectorAll(".desk-entry").find((row) => row.getAttribute("data-path") === "/me"), "/me");
    answer("/me", { status: 404, detail: "Path not found" });
    await drain();
    const error = again.querySelector(".context-error");
    const retry = again.querySelector("#task-file-picker-retry");
    const toRoot = again.querySelector("#task-file-picker-root");
    const shownError = Boolean(error) && error.getAttribute("role") === "alert"
        && error.textContent.includes("Path not found") && error.textContent.includes("/me")
        && Boolean(retry) && retry.textContent === "Retry" && Boolean(toRoot) && toRoot.textContent === "Go to /"
        && secondUse.disabled;
    await click(retry, "Retry");
    const retried = requested[requested.length - 1].path === "/me";
    answer("/me", { status: 500, detail: "Disk unavailable" });
    await drain();
    await click(again.querySelector("#task-file-picker-root"), "Go to /");
    const wentToRoot = requested[requested.length - 1].path === "/";
    answer("/");
    await drain();
    const errorOffersRetry = shownError && retried && wentToRoot
        && again.querySelectorAll(".desk-entry").length === 2 && !again.querySelector(".context-error");
    if (!errorOffersRetry) fail(`a failed listing: shown ${shownError}, retried ${retried}, root ${wentToRoot}`);

    // ── cancelPicksNothing (and a load answered after close paints nothing)
    await click(again.querySelectorAll(".desk-entry").find((row) => row.getAttribute("data-path") === "/me"), "/me");
    const footer = again.querySelectorAll(".modal-actions")[0].querySelectorAll("button");
    await click(footer.find((item) => item.textContent === "Cancel"), "Cancel");
    answer("/me");
    await drain();
    const cancelPicksNothing = layers().length === 0 && picks.length === 1 && pending.length === 0
        && typeof second.close === "function";
    if (!cancelPicksNothing) fail("Cancel picked something or left the layer up");

    process.stdout.write(JSON.stringify({
        ok: true,
        opensAsALayer,
        listsTheStartFolder,
        fileSelectsAndFillsTheName,
        folderNavigates,
        refusesBadNames,
        typedNameSelects,
        pickHandsBackTheJoinedPath,
        staleListingIsDropped,
        rootIsRefused,
        errorOffersRetry,
        cancelPicksNothing,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
