/**
 * Node harness: named-path open uses API kind, not a dotted-name heuristic.
 * Denied paths and open-folder failures stay visible. Invoked by
 * tests/test_js_company_files.py. Not a browser bundle.
 */
const fs = require("fs");

function makeClassList() {
    const set = new Set();
    return {
        toggle(name, on) {
            if (on) set.add(name);
            else set.delete(name);
        },
        add(name) { set.add(name); },
        remove(name) { set.delete(name); },
        contains(name) { return set.has(name); },
    };
}

function makeEl(id) {
    return {
        id,
        textContent: "",
        value: "",
        innerHTML: "",
        className: "",
        style: {},
        dataset: {},
        classList: makeClassList(),
        addEventListener() {},
        removeEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        contains() { return false; },
        appendChild() {},
        remove() {},
        focus() {},
        setSelectionRange() {},
        getAttribute() { return ""; },
        setAttribute() {},
    };
}

const actionBanner = makeEl("cf-action-error");
const container = {
    innerHTML: "",
    querySelector(sel) {
        if (sel === "#cf-action-error") return actionBanner;
        return makeEl(sel);
    },
    querySelectorAll() { return []; },
};

const viewerOpens = [];
let fetchImpl = async () => ({
    ok: true,
    status: 200,
    async json() {
        return {
            kind: "directory",
            path: "/",
            entries: [],
            breadcrumbs: [],
            workspace_note: "",
            host_roots: [],
        };
    },
    async text() { return ""; },
});

global.document = {
    addEventListener() {},
    removeEventListener() {},
    createElement() { return makeEl("el"); },
    querySelector() { return null; },
    body: { appendChild() {}, style: {} },
};
global.window = {
    document: global.document,
    lucide: null,
    BossModApi: {
        formatError(payload, status) {
            if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
                return payload.detail;
            }
            return `Request failed (${status})`;
        },
    },
};
global.lucide = null;
global.console = console;
global.CompanyFileViewer = {
    open(path) { viewerOpens.push(path); },
    close() {},
};
global.CompanyFileOps = {};
global.SettingsView = { open() {} };
global.apiFetch = (...args) => fetchImpl(...args);
global.apiFetchOk = async (...args) => {
    const res = await fetchImpl(...args);
    if (!res.ok) throw new Error(window.BossModApi.formatError(await res.json().catch(() => ({})), res.status));
    return res;
};

const utilsSrc = fs.readFileSync(process.argv[2], "utf8");
const filesSrc = fs.readFileSync(process.argv[3], "utf8");
eval(`${utilsSrc}\n;global.BossModUtils = BossModUtils;\n`);
eval(`${filesSrc}\n;global.CompanyFiles = CompanyFiles;\n`);

if (typeof global.CompanyFiles !== "object" || typeof global.CompanyFiles.openNamedPath !== "function") {
    throw new Error("CompanyFiles.openNamedPath missing");
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
    CompanyFiles.render(container);
    await delay(10);

    fetchImpl = async (input) => {
        const url = decodeURIComponent(String(input));
        if (url.includes(".config")) {
            return {
                ok: true,
                status: 200,
                async json() {
                    return {
                        kind: "directory",
                        path: "/tmp/.config",
                        entries: [],
                        breadcrumbs: [{ path: "/", label: "Company Workspace" }, { path: "/tmp/.config", label: ".config" }],
                        workspace_note: "",
                        host_roots: [],
                    };
                },
                async text() { return ""; },
            };
        }
        if (url.includes("app.py")) {
            return {
                ok: true,
                status: 200,
                async json() {
                    return {
                        kind: "file",
                        path: "/tmp/app.py",
                        name: "app.py",
                        content: "x = 1\n",
                    };
                },
                async text() { return ""; },
            };
        }
        if (url.includes("/etc/passwd")) {
            return {
                ok: false,
                status: 400,
                async json() {
                    return { detail: "Path '/etc/passwd' is outside the allowed workspace roots" };
                },
                async text() {
                    return JSON.stringify({ detail: "Path '/etc/passwd' is outside the allowed workspace roots" });
                },
            };
        }
        return {
            ok: true,
            status: 200,
            async json() {
                return { kind: "directory", path: "/", entries: [], breadcrumbs: [], workspace_note: "", host_roots: [] };
            },
            async text() { return ""; },
        };
    };

    viewerOpens.length = 0;
    await CompanyFiles.openNamedPath("/tmp/.config");
    const dottedDirOpenedViewer = viewerOpens.length > 0;

    viewerOpens.length = 0;
    await CompanyFiles.openNamedPath("/tmp/app.py");
    const fileOpenedViewer = viewerOpens[0] === "/tmp/app.py";

    actionBanner.textContent = "";
    await CompanyFiles.openNamedPath("/etc/passwd");
    const deniedPathError = actionBanner.textContent || "";

    process.stdout.write(JSON.stringify({
        ok: true,
        dottedDirOpenedViewer,
        fileOpenedViewer,
        deniedPathErrorVisible: Boolean(deniedPathError),
        deniedPathError,
    }));
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
