/**
 * Node harness: the Files place's named-path open uses the API's own `kind`,
 * not a dotted-name heuristic, and a denied path stays visible. The company
 * top level shows floors by name with the `layers` glyph, and New is held
 * shut there (still focusable, with its reason) until a floor is open.
 * The place opens on the active floor's folder, follows a floor switch, and
 * comes back to the folder (and scroll) it left; a deep link wins over both.
 * A read still in flight when the place is left never paints the next mount.
 * The real file viewer's folder line lists folders only (house root, chevron
 * separators, the server's root label kept as a visually-hidden name) and is
 * absent for a file directly under the root. The viewer's Save and image
 * preview use the endpoints its opener passed (a desk opener passes the
 * desk's), and fall back to the company endpoints when none are passed.
 *
 * Re-pointed in Phase 3B from company-files.js to places/files/. The payload
 * keys are byte-identical to the dock-era harness: the properties are the same,
 * only the modules that hold them moved. Invoked by
 * tests/test_js_company_files.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// core/markdown.js reads `marked`, `hljs` and `DOMParser`; Node has none
// of them, and this harness is not what proves the sanitiser correct.
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;
const { installIconsStub } = require("./js_icons_stub.cjs");
const iconsStub = installIconsStub();
global.window.BossModApi = {
    formatError(payload, status) {
        if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
            return payload.detail;
        }
        return `Request failed (${status})`;
    },
};

const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModStore", "BossModBus", "BossModFormat", "BossModGates",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModPlaces",
    "BossModFloorScope", "BossModFileContent", "BossModFileForm",
    "BossModFileOps", "BossModFileViewer", "BossModFilesData", "BossModHostRoots",
    "BossModDeskOpener", "BossModFolderOpener", "BossModFileGrid",
    "BossModFileActions", "BossModFilesToolbar", "BossModFilesPlace",
];
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModFilesPlace } = global;

// Kept before the spy replaces it: the folder-line cases below open the real
// viewer with a payload.
const RealFileViewer = global.BossModFileViewer;

// The place opens the shared viewer by name at call time, so a spy here
// records exactly what the real one would have been asked to open.
const viewerOpens = [];
global.BossModFileViewer = {
    open(path) { viewerOpens.push(path); return Promise.resolve(); },
    close() {},
};

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

function ok(body) {
    return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(""),
    };
}

function directory(path, crumbs) {
    return ok({
        kind: "directory",
        path,
        entries: [],
        breadcrumbs: crumbs || [],
        workspace_note: "",
        host_roots: [],
    });
}

// The company top level: two floors (folders named by id) and the archive.
const TOP_LEVEL = {
    kind: "directory",
    path: "/",
    entries: [
        { name: "lobby", path: "/lobby", is_dir: true, floor_name: "Lobby", mount: "floor" },
        { name: "f1", path: "/f1", is_dir: true, floor_name: "Finance", mount: "floor" },
        {
            name: ".archived-floors", path: "/.archived-floors", is_dir: true,
            floor_name: "Archived floors", mount: "archive",
        },
    ],
    breadcrumbs: [{ path: "/", label: "Company" }],
    workspace_note: "",
    host_roots: [],
};

let fetchImpl = (input) => {
    const url = decodeURIComponent(String(input));
    if (url.endsWith("path=/f1")) {
        return Promise.resolve(ok({
            kind: "directory",
            path: "/f1",
            entries: [{ name: "books", path: "/f1/books", is_dir: true, floor_name: null }],
            breadcrumbs: [{ path: "/", label: "Company" }, { path: "/f1", label: "Finance" }],
            workspace_note: "",
            host_roots: [],
        }));
    }
    return Promise.resolve(ok(TOP_LEVEL));
};

/** The New toggle, found by its label. */
function newToggle(root) {
    return root.querySelectorAll(".file-toolbar-btn").find((button) => button.textContent === "New");
}

/**
 * Open the REAL viewer on a file payload and read back its folder line.
 *
 * Each child of `.file-view-crumbs` becomes one token, so the assertion
 * sees order, glyphs and separators at once: `icon:<lucide name>`,
 * `hidden:<text>` for the root's visually-hidden label, `crumb:<text>`, and
 * `text:<text>` for any bare text node (a typed separator would show here).
 *
 * @param {object[]} crumbs  The payload's breadcrumbs, root first, file last.
 * @returns {Promise<{rows: number, tokens: string[], painted: boolean}>}
 */
async function viewerFolderLine(crumbs) {
    const paintsBefore = iconsStub.calls.length;
    await RealFileViewer.open("/ignored", {
        api: () => Promise.resolve(ok({
            name: "a.md", path: "/me/reports/a.md", content: "hello", size_bytes: 5,
            breadcrumbs: crumbs,
        })),
    });
    const rows = documentStub.body.querySelectorAll(".file-view-crumbs");
    const tokens = rows.length ? rows[0].childNodes.map((node) => {
        if (node.nodeType !== 1) return `text:${node.textContent}`;
        const icon = node.getAttribute("data-lucide");
        if (icon) return `icon:${icon}`;
        if (node.classList.contains("visually-hidden")) return `hidden:${node.textContent}`;
        return `crumb:${node.textContent}`;
    }) : [];
    // The body itself is what gets painted: createModal paints only its head.
    const painted = iconsStub.calls.slice(paintsBefore)
        .some((call) => call.context === "file-viewer" && call.root.classList.contains("file-view"));
    RealFileViewer.close();
    return { rows: rows.length, tokens, painted };
}

/**
 * Open the REAL viewer, Save an edit, and record where each request went.
 *
 * A text file is edited and saved, then an image is opened, so the result
 * names the read, the PUT, and the raw-bytes fetch in order.
 *
 * @param {object} endpoints  `apiUrl`, `saveUrl`, `rawUrl`, or none of them.
 * @returns {Promise<{saveCalls: string[], rawCalls: string[], saveBody: object|null}>}
 */
async function viewerEndpoints(endpoints) {
    const calls = [];
    let reading = { name: "a.md", path: "/me/a.md", content: "hello", size_bytes: 5, breadcrumbs: [] };
    const api = (url, init) => {
        calls.push({ url: String(url), method: (init && init.method) || "GET", body: init && init.body });
        if (String(url).includes("raw?path=")) {
            return Promise.resolve({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["png"])) });
        }
        if (init && init.method === "PUT") return Promise.resolve(ok({ status: "ok" }));
        return Promise.resolve(ok(reading));
    };
    await RealFileViewer.open("/me/a.md", { api, ...endpoints });
    const button = (label) => documentStub.body.querySelectorAll("button")
        .find((el) => el.textContent === label);
    await button("Edit").dispatchClick();
    documentStub.body.querySelector(".text-editor").value = "edited";
    await button("Save").dispatchClick();
    await drain();
    RealFileViewer.close();
    reading = { name: "pic.png", path: "/me/pic.png", size_bytes: 3, breadcrumbs: [] };
    await RealFileViewer.open("/me/pic.png", { api, ...endpoints });
    await drain();
    RealFileViewer.close();
    const put = calls.find((call) => call.method === "PUT");
    return {
        saveCalls: calls.filter((call) => call.method === "PUT").map((call) => call.url),
        rawCalls: calls.filter((call) => call.url.includes("raw?path=")).map((call) => call.url),
        saveBody: put ? JSON.parse(put.body) : null,
    };
}

async function main() {
    // ─── The viewer's endpoints come from its opener ───
    const deskEndpoints = await viewerEndpoints({
        apiUrl: "/api/agents/a1/desk?path=%2Fme%2Fa.md",
        saveUrl: "/api/agents/a1/desk",
        rawUrl: "/api/agents/a1/desk/raw",
    });
    const companyEndpoints = await viewerEndpoints({});

    // ─── The viewer's folder line: folders only, house root, chevrons ───
    const deskLine = await viewerFolderLine([
        { path: "/", label: "/" },
        { path: "/me", label: "me" },
        { path: "/me/reports", label: "reports" },
        { path: "/me/reports/a.md", label: "a.md" },
    ]);
    const companyLine = await viewerFolderLine([
        { path: "/", label: "Company" },
        { path: "/f1", label: "Finance" },
        { path: "/f1/a.md", label: "a.md" },
    ]);
    const rootFileLine = await viewerFolderLine([
        { path: "/", label: "/" },
        { path: "/a.md", label: "a.md" },
    ]);

    // A deep link to the company top level: the floors themselves.
    const store = BossModStore.createStore({ placeParams: { path: "/" } });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const root = documentStub.createElement("div");
    documentStub.body.append(root);

    BossModFilesPlace.mount(root, {
        store,
        bus,
        api: (...args) => fetchImpl(...args),
        navigate() {},
    });
    await drain();

    // ─── The company top level is floors ───
    const rowNames = root.querySelectorAll(".file-entry-name").map((el) => el.textContent);
    const floorRowsShowNames = rowNames.join("|") === "Lobby/|Finance/|Archived floors/";
    const floorGlyphs = root.querySelectorAll('[data-lucide="layers"]').length;
    const gridPainted = iconsStub.calls.some((call) => call.context === "file-grid");
    const toggle = newToggle(root);
    const hint = root.querySelector("#file-new-hint");
    const topLevelNewHeldShut = toggle.getAttribute("aria-disabled") === "true"
        && toggle.getAttribute("aria-describedby") === "file-new-hint"
        && /Pick a floor first/.test(hint.textContent)
        && toggle.disabled !== true;
    await toggle.dispatchClick();
    const topLevelNewStaysClosed = root.querySelector(".file-new-list").hidden === true;

    // A floor row offers only what the server allows on a floor folder. The
    // fake DOM has no layout, so the menu's anchor gets a box and the window
    // a size to position against.
    global.window.innerWidth = 1024;
    global.window.innerHeight = 768;
    const placeable = (button) => {
        button.getBoundingClientRect = () => ({ left: 0, bottom: 0 });
        return button;
    };
    // The row menu only: the New menu's items share the item class.
    const menuLabels = () => documentStub.body.querySelector(".file-menu")
        .querySelectorAll(".file-menu-item")
        .map((item) => item.getAttribute("data-action"));
    const financeMenu = root.querySelectorAll(".file-entry-menu")
        .find((button) => button.getAttribute("aria-label") === "Actions for Finance");
    await placeable(financeMenu).dispatchClick();
    const floorRowActions = menuLabels().join("|");
    documentStub.body.querySelector(".file-menu").remove();
    const { itemsFor } = global.BossModFileActions;
    const actionsOf = (entry) => itemsFor(entry).map((item) => item.action).join("|");
    const archivedRowActions = actionsOf({ name: "old", path: "/.archived-floors/old", is_dir: true });
    const strayTopRowActions = actionsOf({ name: "stray", path: "/stray", is_dir: true });

    // A search hit shows its display path (floor by name), not the raw id path.
    const hit = global.BossModFileGrid.renderEntry(
        { name: "plan.md", path: "/f1/books/plan.md", display_path: "/Finance/books/plan.md", is_dir: false },
        { showPath: true, onOpen() {}, onMenu() {} });
    const searchHitPath = hit.querySelector(".file-entry-path").textContent;

    const finance = root.querySelectorAll(".file-entry")
        .find((button) => button.getAttribute("data-path") === "/f1");
    await finance.dispatchClick();
    await drain();
    const insideFloorNewOpen = newToggle(root).getAttribute("aria-disabled") === "false"
        && !newToggle(root).hasAttribute("aria-describedby");
    const booksMenu = root.querySelectorAll(".file-entry-menu")
        .find((button) => button.getAttribute("aria-label") === "Actions for books");
    await placeable(booksMenu).dispatchClick();
    const insideFloorRowActions = menuLabels().join("|");
    documentStub.body.querySelector(".file-menu").remove();

    fetchImpl = (input) => {
        const url = decodeURIComponent(String(input));
        if (url.includes(".config")) {
            return Promise.resolve(directory("/tmp/.config", [
                { path: "/", label: "Company Workspace" },
                { path: "/tmp/.config", label: ".config" },
            ]));
        }
        if (url.includes("app.py")) {
            return Promise.resolve(ok({
                kind: "file", path: "/tmp/app.py", name: "app.py", content: "x = 1\n",
            }));
        }
        if (url.includes("/etc/passwd")) {
            const detail = "Path '/etc/passwd' is outside the allowed workspace roots";
            return Promise.resolve({
                ok: false,
                status: 400,
                json: () => Promise.resolve({ detail }),
                text: () => Promise.resolve(JSON.stringify({ detail })),
            });
        }
        return Promise.resolve(directory("/"));
    };

    // A dotted DIRECTORY name must not be mistaken for a file. The dock-era
    // browser guessed from the name and opened the viewer on ".config".
    viewerOpens.length = 0;
    await BossModFilesPlace.openNamedPath("/tmp/.config");
    await drain();
    const dottedDirOpenedViewer = viewerOpens.length > 0;

    viewerOpens.length = 0;
    await BossModFilesPlace.openNamedPath("/tmp/app.py");
    await drain();
    const fileOpenedViewer = viewerOpens[0] === "/tmp/app.py";

    await BossModFilesPlace.openNamedPath("/etc/passwd");
    await drain();
    const banner = root.querySelector(".files-error");
    if (!banner) throw new Error("the Files place must render an error banner");
    const deniedPathError = banner.textContent || "";

    BossModFilesPlace.unmount();

    // ─── Start path, floor follow, and the remembered view ───
    const requested = [];
    fetchImpl = (input) => {
        const url = decodeURIComponent(String(input));
        const path = url.split("path=")[1] || "";
        requested.push(path);
        if (path === "/f1") {
            return Promise.resolve(ok({
                kind: "directory",
                path: "/f1",
                entries: [{ name: "books", path: "/f1/books", is_dir: true, floor_name: null }],
                breadcrumbs: [{ path: "/", label: "Company" }, { path: "/f1", label: "Finance" }],
                workspace_note: "",
                host_roots: [],
            }));
        }
        return Promise.resolve(directory(path));
    };
    const floorStore = BossModStore.createStore({ placeParams: {}, currentFloorId: "f1" });
    const floorCtx = { store: floorStore, bus, api: (...args) => fetchImpl(...args), navigate() {} };
    const lastRequest = () => requested[requested.length - 1];

    // The last view was on another floor, so this floor's folder wins.
    BossModFilesPlace.mount(root, floorCtx);
    await drain();
    const opensOnFloor = lastRequest() === "/f1";

    const books = root.querySelectorAll(".file-entry")
        .find((button) => button.getAttribute("data-path") === "/f1/books");
    await books.dispatchClick();
    await drain();
    root.querySelector(".files-body").scrollTop = 120;
    BossModFilesPlace.unmount();

    BossModFilesPlace.mount(root, floorCtx);
    await drain();
    const returnsToFolder = lastRequest() === "/f1/books";
    const returnsToScroll = root.querySelector(".files-body").scrollTop === 120;
    BossModFilesPlace.unmount();

    floorStore.setState({ placeParams: { path: "/f1" } });
    BossModFilesPlace.mount(root, floorCtx);
    await drain();
    const deepLinkWins = lastRequest() === "/f1";

    floorStore.setState({ currentFloorId: "lobby" });
    await drain();
    const floorSwitchFollows = lastRequest() === "/lobby";
    BossModFilesPlace.unmount();

    // ─── A load from a left mount never paints the next one ───
    // Away and back before the first read settles: both mounts ask for the
    // same remembered folder, so only the load generation can tell them apart.
    const pendingReads = [];
    fetchImpl = () => new Promise((resolve) => { pendingReads.push(resolve); });
    const listing = (name) => ok({
        kind: "directory",
        path: "/lobby",
        entries: [{ name, path: `/lobby/${name}`, is_dir: true, floor_name: null }],
        breadcrumbs: [],
        workspace_note: "",
        host_roots: [],
    });
    const staleCtx = {
        store: BossModStore.createStore({ placeParams: {}, currentFloorId: "lobby" }),
        bus, api: (...args) => fetchImpl(...args), navigate() {},
    };
    BossModFilesPlace.mount(root, staleCtx);
    BossModFilesPlace.unmount();
    BossModFilesPlace.mount(root, staleCtx);
    pendingReads[1](listing("fresh"));
    await drain();
    pendingReads[0](listing("stale"));
    await drain();
    const shownRows = root.querySelectorAll(".file-entry-name").map((el) => el.textContent).join("|");
    const staleLoadDropped = pendingReads.length === 2
        && shownRows.includes("fresh") && !shownRows.includes("stale");
    BossModFilesPlace.unmount();

    process.stdout.write(JSON.stringify({
        ok: true,
        viewerDeskLine: deskLine,
        viewerCompanyLine: companyLine,
        viewerRootFileLine: rootFileLine,
        viewerDeskEndpoints: deskEndpoints,
        viewerCompanyEndpoints: companyEndpoints,
        floorRowsShowNames,
        floorGlyphs,
        gridPainted,
        topLevelNewHeldShut,
        topLevelNewStaysClosed,
        insideFloorNewOpen,
        floorRowActions,
        archivedRowActions,
        strayTopRowActions,
        insideFloorRowActions,
        searchHitPath,
        dottedDirOpenedViewer,
        fileOpenedViewer,
        deniedPathErrorVisible: Boolean(deniedPathError),
        deniedPathError,
        opensOnFloor,
        returnsToFolder,
        returnsToScroll,
        deepLinkWins,
        floorSwitchFollows,
        staleLoadDropped,
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
