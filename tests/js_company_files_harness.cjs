/**
 * Node harness: the Files place's named-path open uses the API's own `kind`,
 * not a dotted-name heuristic, and a denied path stays visible. The company
 * top level shows floors by name with the `layers` glyph, and New is held
 * shut there (still focusable, with its reason) until a floor is open.
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
    "BossModOverlayFocus", "BossModOverlays", "BossModPlaces",
    "BossModFileContent", "BossModFileForm",
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

async function main() {
    const store = BossModStore.createStore({ placeParams: {} });
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

    process.stdout.write(JSON.stringify({
        ok: true,
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
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
