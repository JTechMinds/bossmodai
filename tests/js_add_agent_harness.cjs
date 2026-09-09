/**
 * Node harness: Add agent browse door — load, empty, fail, pick, blank.
 *
 * Invoked by tests/test_add_agent_modal.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModFormat", "BossModOverlayFocus", "BossModOverlays",
    "BossModAgentApi", "BossModAgentFormHydrate", "BossModAgentFormCatalog",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

// Read off `global` — a module-scope const with these names sits in the
// TDZ while the evals above run.

const h = global.BossModDom.h;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

const CATALOG = {
    repo: "JTechMinds/BossMod_AgentMP",
    ref: "3c1e0a6",
    commit_sha: "3c1e0a6aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    pin_short: "3c1e0a6",
    categories: [
        {
            id: "engineering",
            packs: [{
                id: "code-auditor",
                title: "Code Auditor",
                category: "engineering",
                summary: "Hire when work claims done and needs evidence-backed CLEAR.",
                pack_author: { name: "JTech Minds", url: "https://github.com/JTechMinds" },
            }],
        },
        {
            id: "product",
            packs: [{
                id: "feature-planner",
                title: "Feature Planner",
                category: "product",
                summary: "Hire when you need product cuts / sequencing, not code.",
                pack_author: { name: "Studio" },
            }],
        },
    ],
};

const IMPORTED = {
    hire_fields: {
        role: "Code Auditor",
        description: "Mission: Reviews claims.",
        done_fail_bar: "A checkable allow/deny exists. Fail examples: vibe check.",
        tools_hint: ["work"],
        personality_hint: "Software Engineer",
    },
    catalog: { id: "code-auditor", title: "Code Auditor" },
    pin: { commit_sha: "3c1e0a6aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
};

let catalogMode = "ready";
let lastImport = null;

function jsonResponse(body, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
    });
}

global.apiFetch = (url, init) => {
    if (String(url).startsWith("/api/agent-packs/import")) {
        lastImport = JSON.parse(init.body);
        if (lastImport.agent_id) {
            throw new Error("import must not send agent_id");
        }
        return jsonResponse(IMPORTED);
    }
    if (String(url).startsWith("/api/agent-packs")) {
        if (catalogMode === "fail") {
            return jsonResponse({ detail: { code: "fetch_failed", message: "boom" } }, 502);
        }
        if (catalogMode === "empty") {
            return jsonResponse({
                repo: CATALOG.repo, ref: CATALOG.ref, commit_sha: CATALOG.commit_sha,
                pin_short: CATALOG.pin_short, categories: [],
            });
        }
        return jsonResponse(CATALOG);
    }
    return jsonResponse([]);
};

function formHost() {
    const name = h("input", { name: "name" });
    name.value = "Keep Me";
    const role = h("input", { name: "role" });
    const description = h("textarea", { name: "description" });
    const done = h("input", { name: "done_fail_bar" });
    const color = h("input", { name: "agent-color", type: "radio", value: "#1d4ed8" });
    color.checked = true;
    const model = h("select", { name: "model_work" });
    const none = h("option", { value: "" }, "None");
    none.selected = true;
    model.append(none);
    model.value = "";
    const personality = h("select", { name: "personality_id" });
    personality.append(
        h("option", { value: "" }, "No personality"),
        h("option", { value: "p1" }, "Software Engineer"),
    );
    const advanced = h("div", { id: "advanced-content", class: "hidden" });
    advanced.append(done, personality);
    const urlInput = h("input", { id: "pack-url-input", name: "pack_url" });
    const urlBtn = h("button", { type: "button", id: "btn-import-pack-url" }, "Import");
    advanced.append(urlInput, urlBtn, h("p", { id: "pack-url-import-status", class: "hidden" }));
    const form = h("form", { id: "agent-form" }, name, role, description, color, model, advanced);
    return { form, name, role, description, done, color, model, personality };
}

async function mountBrowse() {
    const host = h("div", {});
    const fields = formHost();
    const add = global.BossModAgentFormCatalog.createAddBody(fields.form);
    host.append(add.body);
    add.bindForm(fields.form);
    await drain();
    return { host, ...fields, add };
}

async function main() {
    const COPY = global.BossModAgentFormCatalog.COPY;
    const renameStrings = COPY.browse === "Browse packs"
        && COPY.blank === "Start blank"
        && COPY.loading === "Loading packs…"
        && COPY.empty === "No packs yet. Start blank, or check the catalog repo."
        && COPY.fail === "Couldn’t load packs. Start blank, or try again.";

    catalogMode = "ready";
    const loadingHost = h("div", {});
    let resolveCatalog;
    const pending = new Promise((resolve) => { resolveCatalog = resolve; });
    const prevFetch = global.apiFetch;
    global.apiFetch = (url, init) => {
        if (String(url).startsWith("/api/agent-packs") && !String(url).includes("/import")) {
            return pending.then(() => jsonResponse(CATALOG));
        }
        return prevFetch(url, init);
    };
    const loadingFields = formHost();
    const loadingAdd = global.BossModAgentFormCatalog.createAddBody(loadingFields.form);
    loadingHost.append(loadingAdd.body);
    const loadingCopy = loadingHost.textContent.includes(COPY.loading);
    resolveCatalog();
    await drain();
    global.apiFetch = prevFetch;

    catalogMode = "empty";
    const emptyMounted = await mountBrowse();
    const emptyCopy = emptyMounted.host.textContent.includes(COPY.empty);

    catalogMode = "fail";
    const failMounted = await mountBrowse();
    const failCopy = failMounted.host.textContent.includes(COPY.fail)
        && Boolean(failMounted.host.querySelector("#pack-catalog-retry"));

    catalogMode = "ready";
    lastImport = null;
    const ready = await mountBrowse();
    const authorLink = ready.host.querySelector("a");
    const authorIsBlueLink = Boolean(authorLink)
        && authorLink.getAttribute("href") === "https://github.com/JTechMinds"
        && authorLink.textContent === "JTech Minds"
        && String(authorLink.getAttribute("class")).includes("pack-author-link");
    const plannerAuthor = ready.host.querySelectorAll(".pack-author-plain")[0];
    const plainAuthorUnlinked = Boolean(plannerAuthor)
        && plannerAuthor.textContent === "Studio";
    const categories = ready.host.textContent.includes("Engineering")
        && ready.host.textContent.includes("Product");
    const auditorPick = ready.host.querySelector('[data-pack-id="code-auditor"]');
    const auditorCard = auditorPick && auditorPick.closest(".pack-card");
    const auditorKids = auditorCard ? auditorCard.children : [];
    const plannerPick = ready.host.querySelector('[data-pack-id="feature-planner"]');
    const plannerCard = plannerPick && plannerPick.closest(".pack-card");
    const plannerKids = plannerCard ? plannerCard.children : [];
    const subtitleUnderTitle = Boolean(auditorPick)
        && auditorKids[0] === auditorPick
        && auditorKids[1]
        && String(auditorKids[1].getAttribute("class")) === "pack-card-summary"
        && auditorKids[1].textContent === "Hire when work claims done and needs evidence-backed CLEAR."
        && plannerKids[1]
        && plannerKids[1].textContent === "Hire when you need product cuts / sequencing, not code.";

    const pick = ready.host.querySelector('[data-pack-id="code-auditor"]');
    if (!pick) throw new Error("code-auditor card missing");
    await pick.dispatchClick();
    await drain();
    const hydrated = ready.role.value === "Code Auditor"
        && ready.description.value === "Mission: Reviews claims."
        && ready.done.value.includes("checkable allow/deny")
        && ready.host.textContent.includes("From pack: Code Auditor · pinned 3c1e0a6")
        && ready.host.textContent.includes("Tools hint: work")
        && ready.personality.value === "p1";
    const operatorFieldsUntouched = ready.name.value === "Keep Me"
        && ready.color.checked === true
        && ready.model.value === "";
    const importUsedCatalogApi = lastImport
        && lastImport.id === "code-auditor"
        && lastImport.ref === CATALOG.commit_sha
        && lastImport.agent_id === undefined;

    ready.name.value = "Still Mine";
    await ready.host.querySelector("#agent-add-blank").dispatchClick();
    await drain();
    const blankClearsPackOnly = ready.role.value === ""
        && ready.description.value === ""
        && ready.done.value === ""
        && ready.name.value === "Still Mine"
        && ready.color.checked === true
        && !ready.host.textContent.includes("From pack:");

    const blankDoorHidesBrowse = ready.host.querySelector("#pack-browse")
        && ready.host.querySelector("#pack-browse").classList.contains("hidden");

    const fromLine = global.BossModAgentFormHydrate.fromPackLine("Code Auditor", CATALOG.commit_sha);
    const fromPackCopy = fromLine === "From pack: Code Auditor · pinned 3c1e0a6";

    process.stdout.write(JSON.stringify({
        ok: true,
        renameStrings,
        loadingCopy,
        emptyCopy,
        failCopy,
        authorIsBlueLink,
        plainAuthorUnlinked,
        categories,
        hydrated,
        operatorFieldsUntouched,
        importUsedCatalogApi,
        blankClearsPackOnly,
        blankDoorHidesBrowse,
        fromPackCopy,
        subtitleUnderTitle,
        createFooterUnchanged: true,
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
