/**
 * Node harness: the Files place's named-path open uses the API's own `kind`,
 * not a dotted-name heuristic, and a denied path stays visible.
 *
 * Re-pointed in Phase 3B from company-files.js to places/files/. The payload
 * keys are byte-identical to the dock-era harness: the properties are the same,
 * only the modules that hold them moved. Invoked by
 * tests/test_js_company_files.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;
global.window.BossModApi = {
    formatError(payload, status) {
        if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
            return payload.detail;
        }
        return `Request failed (${status})`;
    },
};

const NAMES = [
    "BossModDom", "BossModStore", "BossModBus", "BossModFormat", "BossModGates",
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

let fetchImpl = () => Promise.resolve(directory("/"));

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
