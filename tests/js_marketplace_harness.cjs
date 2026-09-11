/**
 * Node harness: the agent marketplace — loading, failure, card state, install,
 * uninstall, the detail view that replaces the grid, and the two inline strips
 * that must never become a second modal.
 *
 * Invoked by tests/test_marketplace.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModOverlayFocus", "BossModOverlays",
    "BossModAgentApi", "BossModAgentTemplatesApi",
    "BossModMarketplaceWithheld", "BossModMarketplaceItems",
    // The card anatomy and the filter rail the takeover shares with the Add
    // agent picker. They read marketplace-items.js's projection, so they load
    // after it and before the two views that spend them.
    "BossModPackCard", "BossModFilterRail",
    "BossModMarketplaceSections", "BossModMarketplaceDetail",
    "BossModMarketplaceView", "BossModMarketplace",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const PIN = "aa11bb2ccccccccccccccccccccccccccccccccc";
// The catalog title carries markup on purpose: h() must render it as TEXT.
const HOSTILE_TITLE = "<script>alert(1)</script>";

const AUDITOR_MISSION = "Reads a diff and reports what is not true, with evidence attached.";

/** The `sections` shape `describe_pack` returns and both routes now send. */
function split(mission, inScope, outOfScope, handoff, done, fail) {
    return {
        description: {
            preamble: "",
            mission,
            in_scope: inScope,
            out_of_scope: outOfScope,
            handoff,
        },
        done: { preamble: done, fail_examples: fail },
    };
}

const CATALOG = {
    repo: "JTechMinds/BossMod_AgentMP",
    ref: "aa11bb2",
    commit_sha: PIN,
    pin_short: "aa11bb2",
    // Every listed pack was read and passed the gate. The key is PRESENT and
    // empty, the way the route sends it — an empty notice for a healthy
    // catalog would be a problem invented out of nothing.
    withheld: [],
    categories: [
        {
            id: "engineering",
            packs: [
                {
                    id: "code-auditor", kind: "agent", category: "engineering",
                    title: "Code Auditor", specialty: "Reviews claims",
                    description: `Mission: ${AUDITOR_MISSION}\nIn scope: Pull requests.`,
                    what_done_looks_like: "A checkable allow/deny exists.",
                    sections: split(
                        AUDITOR_MISSION, "Pull requests and their tests.",
                        "Credential hunting.", "The operator gets a written verdict.",
                        "A checkable allow/deny exists.", "A vibe check with no evidence.",
                    ),
                    tools_hint: ["work"],
                    content_hash: "hash-auditor-v2",
                    pack_author: { name: "JTech Minds", url: "https://github.com/JTechMinds" },
                },
                {
                    id: "test-writer", kind: "agent", category: "engineering",
                    title: HOSTILE_TITLE, specialty: "Writes tests",
                    description: "Mission: Turns a bug into a failing test.",
                    what_done_looks_like: "A failing test exists first.",
                    sections: split(
                        "Turns a bug into a failing test.", "Regressions.",
                        "Shipping the fix.", "The operator gets a red test.",
                        "A failing test exists first.", "A test that never failed.",
                    ),
                    content_hash: "hash-tests-v1",
                    pack_author: { name: "Studio" },
                },
            ],
        },
        {
            id: "product-design",
            packs: [{
                // The shape the REAL catalog is full of: `specialty` is the
                // title again, so the card must print it once and spend the
                // line it was wasting on the category instead.
                id: "feature-planner", kind: "agent", category: "product-design",
                title: "Feature Planner", specialty: "Feature Planner",
                description: "Mission: Breaks a goal into steps.",
                what_done_looks_like: "A named plan exists.",
                sections: split(
                    "Breaks a goal into steps.", "Goals.", "Writing the code.",
                    "The operator gets a plan.", "A named plan exists.", "A wish list.",
                ),
                content_hash: "hash-planner-v1",
            }],
        },
    ],
};

// Two lines on purpose. The route has ONE slot for a When-to-hire line and
// fills it with the preamble's FIRST line; the card and the detail have room
// for the whole lead-in. A pack whose row carries both is the only shape that
// can tell "the client re-read the preamble" apart from "the client printed
// whatever the route put in `summary`".
const INTRO_LEAD_FIRST = "Read this before hiring: it reviews, it never edits.";
const INTRO_LEAD = `${INTRO_LEAD_FIRST}\nIt reads a diff and reports what is not true.`;
const INTRO_MISSION = "Reads a release and writes the notes for it.";
// The catalog INDEX row's one-line When-to-hire, for a pack whose own
// description opens straight on a heading and so has no lead-in of its own.
const SUMMARY_LINE = "Hire when a release needs cutting, not writing.";
const SUMMARY_MISSION = "Cuts a release and tags it.";

/** A description carrying prose BEFORE its first heading: preamble AND mission,
 *  which is what `extract_labeled_sections` returns for
 *  "Read this…\n\nMission: …". The two are not alternatives. */
function withIntro(preamble, mission) {
    return {
        description: {
            preamble, mission, in_scope: null, out_of_scope: null, handoff: null,
        },
        done: { preamble: null, fail_examples: null },
    };
}

// The absences the API really produces, in one catalog. There is no row here
// with no `sections`: a pack that fails to parse is WITHHELD at the source and
// never reaches this payload as a card at all, and an installed row derives its
// sections from two NOT NULL columns. `thin-pack` parsed but never went through
// the quality gate, so most of its sections are null and it lists no tools at
// all — `tools_hint` is absent, not `[]`.
//
// `intro-pack` is not an absence but the shape that exposed the content loss:
// a description with prose before its first heading carries a preamble AND a
// mission, and the surface printed only the mission, so the author's lead-in
// rendered nowhere at all. Every other pack here and in CATALOG carries the
// empty preamble `describe_pack` returns when the text starts on a heading,
// which is what proves no empty lead-in node is drawn for it.
//
// `summary-pack` is the third case, and the two around it are what make it a
// case at all: it has no preamble, exactly like `thin-pack`, but its catalog
// INDEX row names the occasion to hire it. `thin-pack` proves an absent
// lead-in draws nothing; this proves the index line is read where the pack
// itself has nothing to say, and NOT read over a pack that does — that is
// `intro-pack`, whose own lead-in wins with no `summary` on the row to lose to.
const ABSENCE_CATALOG = {
    repo: "JTechMinds/BossMod_AgentMP",
    ref: "aa11bb2",
    commit_sha: PIN,
    pin_short: "aa11bb2",
    categories: [{
        id: "engineering",
        packs: [
            {
                id: "intro-pack", kind: "agent", category: "engineering",
                title: "Intro Pack", specialty: "Writes release notes",
                description: `${INTRO_LEAD}\n\nMission: ${INTRO_MISSION}`,
                sections: withIntro(INTRO_LEAD, INTRO_MISSION),
                // What `list_catalog` sends for a pack that HAS a preamble:
                // its first line. The card must still draw the whole lead-in.
                summary: INTRO_LEAD_FIRST,
                content_hash: "hash-intro-v1",
            },
            {
                id: "thin-pack", kind: "agent", category: "engineering",
                title: "Thin Pack", specialty: "Does one thing",
                description: "Mission: Ships the smallest correct change.",
                what_done_looks_like: "A named diff exists.",
                sections: split(
                    "Ships the smallest correct change.", null, null, null,
                    "A named diff exists.", null,
                ),
                content_hash: "hash-thin-v1",
            },
            {
                id: "summary-pack", kind: "agent", category: "engineering",
                title: "Summary Pack", specialty: "Cuts releases",
                description: `Mission: ${SUMMARY_MISSION}`,
                what_done_looks_like: "A tagged release exists.",
                // What the route sends for a row whose pack has no preamble:
                // `list_catalog` falls back to the index row's own summary.
                summary: SUMMARY_LINE,
                sections: split(
                    SUMMARY_MISSION, null, null, null,
                    "A tagged release exists.", null,
                ),
                content_hash: "hash-summary-v1",
            },
        ],
    }],
};

// A catalog at a pin where two rows never became cards, for two DIFFERENT
// reasons. `code-auditor` was READ and refused, so the maintainer has a file to
// fix and the message names what is wrong with it. `feature-planner` could not
// be read at all, so nothing whatever is known about its content and the only
// honest offer is to try again. One good card stays in the grid, because a
// withheld row is not a failed read of the catalog and must not look like one.
const REFUSED_MESSAGE = "Pack description is missing required sections: Mission.";
const UNAVAILABLE_MESSAGE = "GitHub pack fetch failed.";
const WITHHELD_CATALOG = {
    repo: "JTechMinds/BossMod_AgentMP",
    ref: "aa11bb2",
    commit_sha: PIN,
    pin_short: "aa11bb2",
    categories: [{
        id: "engineering",
        packs: [{
            id: "test-writer", kind: "agent", category: "engineering",
            title: "Test Writer", specialty: "Writes tests",
            description: "Mission: Turns a bug into a failing test.",
            what_done_looks_like: "A failing test exists first.",
            sections: split(
                "Turns a bug into a failing test.", "Regressions.",
                "Shipping the fix.", "The operator gets a red test.",
                "A failing test exists first.", "A test that never failed.",
            ),
            content_hash: "hash-tests-v1",
        }],
    }],
    withheld: [
        {
            id: "code-auditor", path: "packs/engineering/code-auditor.agent.yaml",
            category: "engineering", title: "Code Auditor", kind: "refused",
            code: "pack_quality", message: REFUSED_MESSAGE,
        },
        {
            id: "feature-planner", path: "packs/product/feature-planner.agent.yaml",
            category: "product", title: "Feature Planner", kind: "unavailable",
            code: "fetch_failed", message: UNAVAILABLE_MESSAGE,
        },
    ],
};

function template(overrides) {
    const row = Object.assign({
        id: "t", source: "catalog", pack_id: null, source_url: null,
        category: "engineering", title: "T", specialty: "S", description: "D",
        what_done_looks_like: "A checkable allow/deny exists.",
        personality_hint: null, tools_hint: [], author_name: null, author_url: null,
        commit_sha: PIN, content_hash: "h",
        installed_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
    }, overrides);
    // The server derives this on read from the two stored hire strings. The
    // fake derives it too rather than letting a row exist without it: a row
    // with no `sections` is a shape the route cannot produce.
    if (!row.sections) {
        row.sections = split(
            row.description, null, null, null, row.what_done_looks_like, null,
        );
    }
    return row;
}

let installed = [];
let lastInstall = null;
let lastDelete = null;
let catalogMode = "ready";
let holdCatalog = null;

function resetServer() {
    installed = [
        // Installed at an OLDER content hash than the catalog card: stale.
        template({
            id: "t-auditor", pack_id: "code-auditor", title: "Code Auditor",
            specialty: "Reviews claims", content_hash: "hash-auditor-v1",
            tools_hint: ["work"], author_name: "JTech Minds",
            author_url: "https://github.com/JTechMinds",
        }),
        // Same hash as the card: current.
        template({
            id: "t-planner", pack_id: "feature-planner", category: "product-design",
            title: "Feature Planner", content_hash: "hash-planner-v1",
        }),
        // A URL install: no catalog card, so only Installed can show it.
        template({
            id: "t-url", source: "url", pack_id: null, category: "imported",
            source_url: "github.com/acme/packs/notes.yaml", title: "Release Notes Writer",
            specialty: "Writes release notes", content_hash: "hash-url-v1",
            tools_hint: ["work", "read"],
        }),
    ];
    lastInstall = null;
    lastDelete = null;
    catalogMode = "ready";
    holdCatalog = null;
}

function jsonResponse(body, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
    });
}

function packCard(id) {
    for (const group of CATALOG.categories) {
        const found = (group.packs || []).find((pack) => pack.id === id);
        if (found) return found;
    }
    return null;
}

global.apiFetch = (url, init) => {
    const method = (init && init.method) || "GET";
    const path = String(url);
    if (path.startsWith("/api/agent-packs")) {
        if (catalogMode === "fail") {
            return jsonResponse({ detail: { code: "fetch_failed", message: "GitHub said no." } }, 502);
        }
        if (catalogMode === "empty") {
            return jsonResponse(Object.assign({}, CATALOG, { categories: [] }));
        }
        if (catalogMode === "absent") return jsonResponse(ABSENCE_CATALOG);
        if (catalogMode === "withheld") return jsonResponse(WITHHELD_CATALOG);
        if (holdCatalog) return holdCatalog.then(() => jsonResponse(CATALOG));
        return jsonResponse(CATALOG);
    }
    if (path.startsWith("/api/agent-templates")) {
        if (method === "GET") return jsonResponse(installed);
        if (method === "DELETE") {
            lastDelete = decodeURIComponent(path.split("/").pop());
            const before = installed.length;
            installed = installed.filter((row) => row.id !== lastDelete);
            if (installed.length === before) {
                return jsonResponse({ detail: "Agent template not found" }, 404);
            }
            return jsonResponse({}, 204);
        }
        lastInstall = JSON.parse(init.body);
        if (lastInstall.agent_id !== undefined) throw new Error("install must not send agent_id");
        if (lastInstall.url) {
            if (!lastInstall.confirm) {
                return jsonResponse({
                    detail: {
                        code: "trust_required",
                        message: "Non-allowlisted pack URL requires confirm=true.",
                    },
                }, 403);
            }
            const row = template({
                id: "t-imported", source: "url", pack_id: null, category: "imported",
                source_url: "github.com/acme/packs/new.yaml", title: "Imported Agent",
                content_hash: "hash-imported-v1",
            });
            installed = installed.concat([row]);
            return jsonResponse(row);
        }
        const card = packCard(lastInstall.id);
        if (!card) return jsonResponse({ detail: { code: "not_found", message: "no such pack" } }, 404);
        const row = template({
            id: `t-${card.id}`, pack_id: card.id, category: card.category,
            title: card.title, specialty: card.specialty, description: card.description,
            what_done_looks_like: card.what_done_looks_like, sections: card.sections,
            content_hash: card.content_hash, commit_sha: lastInstall.ref,
        });
        installed = installed.filter((entry) => entry.pack_id !== card.id).concat([row]);
        return jsonResponse(row);
    }
    return jsonResponse([]);
};

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 12; i += 1) await settle(); };

function host() {
    return global.document.body.querySelector(".market-host");
}

function panels() {
    return global.document.body.querySelectorAll(".modal-panel").length;
}

/** The modal's footer row. createModal always builds it; a takeover leaves it
 *  empty, and an empty one is what "no footer action row" means. */
function footerButtons() {
    const row = global.document.body.querySelector(".modal-actions");
    if (!row) throw new Error("the modal built no action row at all");
    return row.querySelectorAll("button").length;
}

/** Nothing to Tab to is a keyboard dead end, and removing the footer removed
 *  createModal's fallback of focusing the last action button. So every state
 *  the takeover can open in has to answer this for itself. */
function focusableStops() {
    const panel = global.document.body.querySelector(".modal-panel");
    return panel ? panel.querySelectorAll(global.BossModOverlayFocus.FOCUSABLE).length : 0;
}

function focusIsInThePanel() {
    const panel = global.document.body.querySelector(".modal-panel");
    const active = global.document.activeElement;
    return Boolean(panel && active && panel.contains(active));
}

function texts(selector) {
    return host().querySelectorAll(selector).map((el) => el.textContent);
}

function count(selector) {
    return host().querySelectorAll(selector).length;
}

function railRow(label) {
    return host().querySelectorAll(".market-rail-item")
        .find((el) => el.querySelector(".market-rail-label").textContent === label) || null;
}

function cardFor(packId) {
    return host().querySelector(`[data-pack-id="${packId}"]`);
}

/** The chip, or null — an uninstalled card is not supposed to carry one. */
function cardState(packId) {
    const card = cardFor(packId);
    if (!card) return null;
    const chip = card.querySelector(".market-card-state");
    return chip ? chip.textContent : null;
}

/** Real time, not ticks: the hover intent is a setTimeout and has to elapse. */
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Fire one listener type the way a browser would, with the node as target. */
async function fire(el, type, extra) {
    if (!el) throw new Error(`fired ${type} at a node that is not there`);
    const event = Object.assign({
        preventDefault() {}, stopPropagation() {}, target: el, key: "",
    }, extra || {});
    for (const fn of [...(el.listeners[type] || [])]) await fn(event);
    await drain();
}

const fireKey = (el, key) => fire(el, "keydown", { key });

/** The section tabs as they stand NOW: every selection rebuilds the tree. */
function sectionTabs() {
    return host().querySelectorAll(".market-section-tab");
}

function sectionPanel() {
    return host().querySelector(".market-section-panel");
}

/** Drive an `input` listener the way a browser would. */
async function typeInto(el, value) {
    el.value = value;
    for (const fn of [...(el.listeners.input || [])]) await fn({ target: el });
    await drain();
}

async function click(el) {
    if (!el) throw new Error("clicked a node that is not there");
    await el.dispatchClick();
    await drain();
}

async function openMarket() {
    resetServer();
    const handle = global.BossModMarketplace.open({});
    await drain();
    return handle;
}

async function main() {
    const verdict = {};

    // ── Loading: a status, never an empty grid pretending to be an empty catalog.
    resetServer();
    let release;
    holdCatalog = new Promise((resolve) => { release = resolve; });
    let handle = global.BossModMarketplace.open({});
    verdict.loadingCopy = host().textContent.includes(global.BossModMarketplaceView.COPY.loading)
        && count(".market-card") === 0;
    verdict.isTakeover = global.document.body.querySelector(".modal-panel")
        .getAttribute("data-size") === "takeover";
    // No footer action row, in the state the takeover OPENS in — and because
    // createModal's fallback is to focus the last action button when the body
    // has nothing focusable, an empty row means the body has to answer.
    verdict.noFooterActionRow = footerButtons() === 0;
    verdict.loadingCanBeTabbed = focusableStops() > 0 && focusIsInThePanel();
    release();
    await drain();
    verdict.loadingClears = count(".market-card") === 3;
    handle.close();

    // ── Failure is not emptiness: an alert and a retry, and cards after it.
    resetServer();
    catalogMode = "fail";
    handle = global.BossModMarketplace.open({});
    await drain();
    const failed = host().querySelector(".market-failed");
    verdict.failedIsAlert = Boolean(failed) && failed.getAttribute("role") === "alert"
        && failed.textContent.includes("GitHub said no.")
        && count(".market-card") === 0;
    verdict.failedCanBeTabbed = footerButtons() === 0 && focusIsInThePanel()
        && Boolean(host().querySelector("#market-retry"))
        && Boolean(host().querySelector("#market-close"));
    catalogMode = "ready";
    host().querySelector("#market-retry").focus();
    await click(host().querySelector("#market-retry"));
    verdict.retryRecovers = count(".market-card") === 3;
    // A retry that WORKS deletes the very button that was pressed, and this one
    // took the keyboard to <body> with it every time. It lands on the filter
    // box, which is in the browse view whatever the read did — the same landing
    // the withheld notice's retry already took.
    verdict.gridRetryKeepsTheKeyboard =
        global.document.activeElement === host().querySelector("#market-find");
    handle.close();

    // ── Ready.
    handle = await openMarket();
    verdict.railCounts = railRow("All").textContent === "All3"
        && railRow("Installed").textContent === "Installed3"
        && railRow("Engineering").textContent === "Engineering2"
        && railRow("Product Design").textContent === "Product Design1";
    verdict.railIsAList = host().querySelector(".market-rail-list").tagName === "UL"
        && host().querySelector(".market-rail").tagName === "NAV";
    // Two GROUPS, not one flat list: `All` and `Installed` are scopes, the rest
    // are the catalog's categories, and run together they read as four buckets
    // one of which is the operator's own library. Each list is named by the
    // heading over it, which is what makes the split real structure rather
    // than a gap — the treatment the roster rail gives PEOPLE and THREADS.
    const railTitles = host().querySelectorAll(".market-rail-title");
    const railLists = host().querySelectorAll(".market-rail-list");
    verdict.railGroupsScopesApartFromCategories =
        host().querySelectorAll(".market-rail-group").length === 2
        && railTitles.map((title) => title.textContent).join("|") === "Show|Categories"
        && railLists.length === 2
        && railLists.every((list, at) => list.tagName === "UL"
            && list.getAttribute("aria-labelledby") === railTitles[at].id)
        && railTitles.every((title) => title.tagName === "H3" && Boolean(title.id))
        && railLists[0].querySelectorAll(".market-rail-item").length === 2
        && railLists[1].querySelectorAll(".market-rail-item").length === 2
        // And the counts survived the regrouping.
        && railRow("All").textContent === "All3"
        && railRow("Product Design").textContent === "Product Design1";
    // Every row is still a plain button, so the rail is as many tab stops as it
    // has rows and Tab crosses BOTH groups. The ids stay numbered across the
    // whole rail rather than per group: render() hands focus back by id after
    // the click that destroyed the button, and a per-group number would name
    // two different rows.
    verdict.railKeyboardCrossesBothGroups = (() => {
        const rows = host().querySelectorAll(".market-rail-item");
        const stops = global.document.body.querySelector(".modal-panel")
            .querySelectorAll(global.BossModOverlayFocus.FOCUSABLE);
        return rows.length === 4
            && rows.every((row) => row.tagName === "BUTTON" && stops.includes(row))
            && rows.map((row) => row.id).join("|")
                === "market-rail-0|market-rail-1|market-rail-2|market-rail-3";
    })();
    // `withheld: []` is the healthy answer, and it draws NOTHING. A notice
    // reporting zero missing packs invents a problem out of a good catalog.
    verdict.noWithheldDrawsNoNotice = count(".market-withheld") === 0;

    // A rail click rebuilds the row that was clicked, so render() has to hand
    // focus back to its replacement. This fake never focuses on click, so the
    // only way activeElement can be the row is the module putting it there —
    // which is the whole point: WebKit does not focus a clicked button either.
    await click(railRow("Engineering"));
    verdict.railClickKeepsFocus = global.document.activeElement === railRow("Engineering")
        && count(".market-card") === 2;
    await click(railRow("All"));
    verdict.railReturnsToAllWithFocus = global.document.activeElement === railRow("All")
        && count(".market-card") === 3;

    // Card state is the CONTENT HASH, never the commit SHA: all three cards
    // carry the same repo-wide pin and only one of them is stale. An
    // UNINSTALLED card carries no chip — there is nothing to say and nothing
    // to press, and `Install` on it invited a click that did nothing.
    verdict.cardStateFromContentHash = cardState("code-auditor") === "Update available"
        && cardState("feature-planner") === "Installed"
        && cardState("test-writer") === null;
    const uninstalled = cardFor("test-writer");
    verdict.uninstalledCardOffersNoControl = uninstalled.tagName === "BUTTON"
        && uninstalled.querySelectorAll(".market-card-state").length === 0
        && uninstalled.querySelectorAll("button").length === 0;

    // The card's body is the pack's MISSION, not the raw description string it
    // was cut from — "Mission: …" on a card is a parser's output, not a summary.
    verdict.cardBodyIsTheMission =
        cardFor("code-auditor").querySelector(".market-card-desc").textContent === AUDITOR_MISSION;

    // The line under the name used to be the specialty, and on a pack whose
    // specialty IS its title that made the card open "Feature Planner /
    // Feature Planner". The echo is dropped and the category — the one fact the
    // rail sorts by that the card never showed — takes the row, title-cased by
    // the same helper the rail's own rows are.
    const planner = cardFor("feature-planner");
    verdict.cardShowsTheCategoryNotTheTitleTwice =
        planner.querySelector(".market-card-category").textContent === "Product Design"
        && planner.querySelectorAll(".market-card-specialty").length === 0
        && planner.textContent.startsWith("PDFeature PlannerProduct Design");
    // A specialty that says something the title did not is still worth the room.
    const auditorCard = cardFor("code-auditor");
    verdict.cardKeepsASpecialtyThatSaysSomethingElse =
        auditorCard.querySelector(".market-card-category").textContent === "Engineering"
        && auditorCard.querySelector(".market-card-specialty").textContent === "Reviews claims";
    verdict.everyCardCarriesItsCategory =
        count(".market-card-category") === count(".market-card");

    // ── The bubble that says which FAMILY a pack belongs to. It LEADS the card
    //    the way it leads the detail hero, and it does not replace the chip:
    //    a coloured `E` is a scanning aid, not the word "Engineering", and
    //    colour may not be the only carrier of a fact (SC 1.4.1).
    const cardMark = (packId) => cardFor(packId).querySelector(".avatar");
    const auditorMark = cardMark("code-auditor");
    const plannerMark = cardMark("feature-planner");
    const auditorMarkStyle = auditorMark.getAttribute("style");
    verdict.everyCardLeadsWithItsCategoryMark = host().querySelectorAll(".market-card")
        .every((card) => {
            const head = card.querySelector(".market-card-head");
            if (!head) return false;
            const mark = head.querySelector(".avatar");
            return Boolean(mark)
                && Boolean(head.querySelector(".market-card-title"))
                // First in the band, so the grid has a left edge to scan.
                && head.children[0] === mark
                // Decorative: the chip under it already says the word, and a
                // second announcement of one fact is noise.
                && mark.getAttribute("aria-hidden") === "true"
                && mark.tagName === "SPAN";
        });
    verdict.aOneWordCategoryIsOneLetter = auditorMark.textContent === "E"
        && !auditorMark.getAttribute("class").includes("avatar-duo");
    verdict.aTwoWordCategoryIsTwoLetters = plannerMark.textContent === "PD"
        // The pair gets the smaller type size, in the same circle.
        && plannerMark.getAttribute("class").includes("avatar-duo");
    verdict.twoCategoriesAreToldApart =
        auditorMark.getAttribute("style") !== plannerMark.getAttribute("style")
        && auditorMark.textContent !== plannerMark.textContent;
    // Straight from the shared builder, not from a fixture: which categories a
    // catalog carries is the CATALOG'S business, and these are rules about the
    // derivation rather than about the packs this harness happens to ship.
    const MARK = global.BossModMarketplaceDetail.categoryMark;
    verdict.aThreeWordCategoryStillTakesTwoLetters =
        MARK("data-science-ops", "md").textContent === "DS";
    // No category at all: stated, and on the pair the module already keeps for
    // a thing with no colour. Never an empty circle nobody can read.
    verdict.anAbsentCategoryIsStatedNotBlank = (() => {
        const blank = MARK("", "md");
        return blank.textContent === "?"
            && blank.getAttribute("style") === "background:var(--line);color:var(--muted)";
    })();
    // Deterministic across renders, which is what makes it an identifier.
    verdict.theSameCategoryIsTheSameColourTwice =
        MARK("engineering", "md").getAttribute("style") === auditorMarkStyle;

    // Remote titles are TEXT. h() built a text node, so nothing was parsed.
    const hostileTitle = cardFor("test-writer").querySelector(".market-card-title");
    verdict.remoteTitleStaysText = hostileTitle.textContent === HOSTILE_TITLE
        // Every child is a TEXT node: nothing in the title was parsed as markup.
        && hostileTitle.children.every((child) => child.nodeType === 3);

    verdict.cardsAreButtons = host().querySelectorAll(".market-card")
        .every((card) => card.tagName === "BUTTON" && card.getAttribute("type") === "button");
    const find = host().querySelector("#market-find");
    verdict.findHasALabel = Boolean(host().querySelectorAll("label")
        .find((label) => label.getAttribute("for") === "market-find"));
    // The footer `Close` is gone, so the browse view carries its own ✕ — a
    // real button with a real name, reachable by keyboard like any other.
    const browseCloser = host().querySelector("#market-close");
    verdict.browseCarriesTheExit = Boolean(browseCloser)
        && browseCloser.tagName === "BUTTON"
        && browseCloser.getAttribute("type") === "button"
        && browseCloser.getAttribute("aria-label") === "Close the marketplace"
        && footerButtons() === 0 && focusableStops() > 0;

    // ── The detail is a TAKEOVER of the takeover, and `‹ Templates` undoes it
    //    exactly — same category, same scroll offset, keyboard back on the card.
    host().querySelector(".market-body").scrollTop = 120;
    await click(cardFor("code-auditor"));
    verdict.detailReplacesTheBrowseView = count(".market-card") === 0
        && count(".market-rail") === 0 && count(".market-head") === 0
        && Boolean(host().querySelector("#market-detail"));
    verdict.detailSectionsInOrder = texts(".market-section-name").join("|")
        === "In scope|Out of scope|Handoff|Done looks like|Fail examples|Tools";
    verdict.detailLeadIsTheMission =
        host().querySelector(".market-detail-lead").textContent === AUDITOR_MISSION;

    // ── The hero's mark is the CARD'S mark: same builder, same bubble, same
    //    colour, so a pack is identified the same way in the grid and in the
    //    read. It used to be the first letter of the title, which said what the
    //    line beside it already said.
    const heroMark = host().querySelector(".market-detail-ident").querySelector(".avatar");
    verdict.theHeroCarriesTheCardsMark = Boolean(heroMark)
        && heroMark.textContent === "E"
        && heroMark.getAttribute("style") === auditorMarkStyle
        && heroMark.getAttribute("class") === "avatar avatar-lg"
        && heroMark.getAttribute("aria-hidden") === "true";
    // ...and it may only be decorative because the byline NAMES the category.
    // The hero drew a mark and named the family nowhere before this.
    verdict.theHeroNamesTheCategoryItMarks = host()
        .querySelector(".market-detail-by").textContent
        .startsWith("Engineering · ");

    // ── The read: description on top, ONE rule, then the sections a section at
    //    a time. The stack of rules between every block is what the operator
    //    objected to, so there is exactly one line on the whole view.
    verdict.detailDrawsExactlyOneRule = count(".market-detail-rule") === 1
        && host().querySelectorAll("hr").length === 1;

    const list = host().querySelector(".market-section-list");
    verdict.sectionsAreAVerticalTabList = list.getAttribute("role") === "tablist"
        && list.getAttribute("aria-orientation") === "vertical"
        && list.getAttribute("aria-label") === "Pack sections"
        && sectionTabs().every((tab) => tab.tagName === "BUTTON"
            && tab.getAttribute("type") === "button"
            && tab.getAttribute("role") === "tab");
    // A name and a line saying what reading it will tell you, on every entry.
    verdict.everySectionCarriesItsSubtitle =
        texts(".market-section-sub").join("|")
            === "What it takes on|What it won’t do|Who gets the result|"
            + "The bar it must clear|What doesn’t count|What it expects to use";

    // ONE tab stop for the list, and the panel is named by the tab that opened
    // it. `aria-controls` is published by the selected tab alone: the others
    // have no panel in the document to point at.
    const panel = sectionPanel();
    verdict.oneTabStopAndOneSelection = sectionTabs()
        .filter((tab) => tab.getAttribute("tabindex") === "0").length === 1
        && sectionTabs()[0].getAttribute("tabindex") === "0"
        && sectionTabs().filter((tab) => tab.getAttribute("aria-selected") === "true")
            .length === 1;
    verdict.panelIsLabelledByItsTab = panel.getAttribute("role") === "tabpanel"
        && panel.getAttribute("aria-labelledby") === sectionTabs()[0].id
        && sectionTabs()[0].getAttribute("aria-controls") === panel.id
        && sectionTabs()[1].getAttribute("aria-controls") === null
        // It scrolls and holds no control of its own, so it is a tab stop.
        && panel.getAttribute("tabindex") === "0"
        && panel.textContent === "Pull requests and their tests.";

    // Down moves the selection AND the keyboard: the panel is already built, so
    // there is nothing to wait for and nothing to activate separately.
    await fireKey(sectionTabs()[0], "ArrowDown");
    verdict.arrowMovesTheSelectionAndThePanel =
        sectionPanel().textContent === "Credential hunting."
        && global.document.activeElement === sectionTabs()[1]
        && sectionTabs()[1].getAttribute("aria-selected") === "true"
        && sectionTabs()[1].getAttribute("tabindex") === "0"
        && sectionTabs()[0].getAttribute("aria-selected") === "false"
        && sectionTabs()[0].getAttribute("tabindex") === "-1"
        && sectionPanel().getAttribute("aria-labelledby") === sectionTabs()[1].id;
    await fireKey(sectionTabs()[1], "End");
    verdict.endGoesToTheLastSection =
        sectionTabs()[5].getAttribute("aria-selected") === "true"
        && sectionPanel().querySelectorAll(".market-detail-tools").length === 1;
    await fireKey(sectionTabs()[5], "Home");
    verdict.homeGoesBackToTheFirst =
        sectionTabs()[0].getAttribute("aria-selected") === "true"
        && global.document.activeElement === sectionTabs()[0]
        && sectionPanel().textContent === "Pull requests and their tests.";
    // Up from the top wraps rather than falling out of a list of six.
    await fireKey(sectionTabs()[0], "ArrowUp");
    verdict.arrowUpWrapsToTheEnd =
        sectionTabs()[5].getAttribute("aria-selected") === "true"
        && global.document.activeElement === sectionTabs()[5];

    // Click is the third way in, and it is the one that takes the keyboard with
    // it — a pointer that presses a control expects to have pressed it.
    await click(sectionTabs()[2]);
    verdict.clickSelectsAndTakesTheKeyboard =
        sectionPanel().textContent === "The operator gets a written verdict."
        && global.document.activeElement === sectionTabs()[2];

    // ── Hover, layered on top: it selects only after the pointer RESTS, so
    //    dragging down the list on the way to the panel does not strobe it.
    const DELAY = global.BossModMarketplaceSections.HOVER_DELAY_MS;
    await fire(sectionTabs()[0], "mouseenter");
    await wait(Math.round(DELAY / 2));
    verdict.hoverWaitsForTheIntentDelay =
        sectionPanel().textContent === "The operator gets a written verdict.";
    await wait(DELAY + 60);
    verdict.hoverSelectsOnceTheIntentIsClear =
        sectionPanel().textContent === "Pull requests and their tests."
        && sectionTabs()[0].getAttribute("aria-selected") === "true"
        // And it did NOT take the keyboard: it is still on the tab that was
        // clicked, which is where the operator left it.
        && global.document.activeElement.id === "market-section-2";
    // Crossing an entry is not resting on it.
    await fire(sectionTabs()[3], "mouseenter");
    await fire(sectionTabs()[3], "mouseleave");
    await wait(DELAY + 60);
    verdict.leavingBeforeTheDelaySelectsNothing =
        sectionPanel().textContent === "Pull requests and their tests.";
    // The pointer resting on the section already up must arm nothing at all:
    // re-selecting it rebuilds the node the pointer is standing on, and the
    // browser answers that with a fresh mouseenter. Node identity is the proof
    // — a rebuild replaces the panel object.
    const held = sectionPanel();
    await fire(sectionTabs()[0], "mouseenter");
    await wait(DELAY + 60);
    verdict.hoveringTheOpenSectionRebuildsNothing = sectionPanel() === held;
    // Left on Handoff, which is nobody's first section: the next pack opened
    // has to prove it starts at the top of its OWN contract.
    await click(sectionTabs()[2]);
    // The visible word is gone and the announced name is not: the bar holds two
    // glyph-only controls, `‹` and `✕`, and `Back` beside the chevron said less
    // than the label already does. Still a real button, still named, and the
    // mark is aria-hidden so it cannot be announced as punctuation.
    const back = host().querySelector("#market-back");
    verdict.backIsAGlyphThatStillSaysWhereItGoes = back.tagName === "BUTTON"
        && back.textContent === "‹"
        && !back.textContent.includes("Back")
        && back.getAttribute("aria-label") === "Back to the marketplace"
        && back.querySelector(".market-detail-back-mark")
            .getAttribute("aria-hidden") === "true";
    // A stale install: `Update` is the actionable thing, so it keeps the slot.
    const lead = host().querySelector("#market-install");
    verdict.updateKeepsThePrimary = lead.textContent === "Update"
        && lead.classList.contains("market-action-lead")
        && lead.classList.contains("primary")
        && Boolean(host().querySelector("#market-uninstall"))
        && !host().querySelector("#market-uninstall")
            .classList.contains("market-action-lead");
    verdict.detailAuthorLinkIsSafe = (() => {
        const link = host().querySelector(".market-detail-author");
        return Boolean(link) && link.getAttribute("rel") === "noopener noreferrer"
            && link.getAttribute("href") === "https://github.com/JTechMinds";
    })();
    await click(host().querySelector("#market-back"));
    verdict.backRestoresTheBrowseView = count(".market-card") === 3
        && host().querySelector(".market-body").scrollTop === 120
        && railRow("All").getAttribute("aria-current") === "true";
    verdict.backReturnsFocusToTheCard =
        global.document.activeElement === cardFor("code-auditor")
        && cardFor("code-auditor").getAttribute("aria-current") === "true";

    // ── Filter.
    await typeInto(find, "planner");
    verdict.filterNarrows = count(".market-card") === 1 && Boolean(cardFor("feature-planner"));
    await typeInto(host().querySelector("#market-find"), "zzz");
    verdict.noMatchCopy = host().textContent
        .includes(global.BossModMarketplaceView.COPY.noMatch);
    await typeInto(host().querySelector("#market-find"), "");

    // ── Select, then install at the pin that was displayed.
    await click(cardFor("test-writer"));
    verdict.selectFocusesDetail = global.document.activeElement
        === host().querySelector("#market-detail");
    // A section id held across a change of pack would name a section the next
    // one may not carry — and, when it does carry it, would drop the operator
    // into the middle of a contract they have not read the top of.
    verdict.aNewPackOpensOnItsFirstSection =
        sectionTabs()[0].getAttribute("aria-selected") === "true"
        && sectionPanel().textContent === "Regressions.";
    verdict.detailShowsInstall = host().querySelector("#market-install").textContent === "Install";
    await click(host().querySelector("#market-install"));
    verdict.installUsedDisplayedPin = lastInstall.id === "test-writer"
        && lastInstall.ref === PIN
        && lastInstall.agent_id === undefined;
    verdict.installAnnounces = Boolean(host().querySelector(".market-notice"))
        && host().querySelector(".market-notice").getAttribute("role") === "status";
    // Installed and current: nothing to press, so the primary slot is EMPTY
    // and the state reads on the metadata line beside the author and the pin.
    // `Uninstall` stays a quiet secondary — it is the destructive action and
    // must not become the most prominent thing on the screen.
    const byline = host().querySelector(".market-detail-by");
    verdict.installedIsStatedNotOffered = count("#market-install") === 0
        && count(".market-detail-state") === 0
        && Boolean(byline.querySelector(".market-detail-installed"))
        && byline.textContent
            === "Engineering · By Studio · pinned aa11bb2 · Installed";
    verdict.uninstallStaysQuiet = (() => {
        const remove = host().querySelector("#market-uninstall");
        return Boolean(remove) && !remove.classList.contains("market-action-lead")
            && !remove.classList.contains("primary")
            && !remove.classList.contains("danger");
    })();
    await click(host().querySelector("#market-back"));
    verdict.installFlipsCardState = cardState("test-writer") === "Installed";

    // ── Installed rail row lists the URL install the catalog cannot show.
    await click(railRow("Installed"));
    const installedTitles = texts(".market-card-title");
    verdict.extrasOnlyUnderInstalled = count(".market-card") === 4
        && installedTitles.includes("Release Notes Writer")
        && installedTitles.includes("Feature Planner");

    const extra = host().querySelectorAll(".market-card").find((card) => {
        const name = card.querySelector(".market-card-title");
        return Boolean(name) && name.textContent === "Release Notes Writer";
    });
    await click(extra);
    // It carries no scope, no handoff and no fail examples, so the two tabs it
    // does carry are the only two drawn — and the first of them is what opens.
    // Tools is a tab now rather than a block down the page: one section is on
    // screen at a time, and the list says what else there is to read.
    verdict.detailShowsTheDoneBarAndTools =
        texts(".market-section-name").join("|") === "Done looks like|Tools"
        && sectionPanel().textContent === "A checkable allow/deny exists."
        && count(".market-detail-tools") === 0;
    await click(sectionTabs()[1]);
    verdict.toolsAreATabOfTheirOwn = count(".market-detail-tools") === 1
        && sectionPanel().textContent === "workread";

    // ── Uninstall asks inline, in the detail. No second dialog.
    await click(host().querySelector("#market-uninstall"));
    verdict.uninstallAsksInline = Boolean(host().querySelector("#market-uninstall-confirm"))
        && panels() === 1;
    await click(host().querySelector("#market-uninstall-confirm"));
    verdict.uninstallRemoved = lastDelete === "t-url"
        && !texts(".market-card-title").includes("Release Notes Writer");
    // The row it was reading is gone, so it cannot still be in the detail.
    verdict.uninstallLeavesTheDetail = count(".market-card") === 3
        && count("#market-detail") === 0;

    // A catalog-backed row leaves its card behind, so the detail stays up —
    // but the grid it would go back to has changed shape, so the card selector
    // it was holding is dropped rather than followed to whatever now sits at
    // that position.
    await click(cardFor("code-auditor"));
    await click(host().querySelector("#market-uninstall"));
    await click(host().querySelector("#market-uninstall-confirm"));
    verdict.uninstallKeepsACatalogCardsDetail = Boolean(host().querySelector("#market-detail"))
        && host().querySelector("#market-install").textContent === "Install";
    await click(host().querySelector("#market-back"));
    verdict.backAfterUninstallLandsSomewhereReal = count(".market-card") === 2
        && global.document.activeElement === host().querySelector("#market-find");

    // ── Install from URL: an inline row, then an inline trust strip, both in
    //    the browse view where the question was asked.
    await click(host().querySelector("#market-url-toggle"));
    verdict.urlRowIsInline = Boolean(host().querySelector(".market-url-row")) && panels() === 1;
    verdict.urlFocused = global.document.activeElement === host().querySelector("#market-url");
    await typeInto(host().querySelector("#market-url"), "https://github.com/acme/packs/new.yaml");
    await click(host().querySelector("#market-url-install"));
    const trust = host().querySelector("#market-trust-confirm");
    verdict.trustStripIsInline = Boolean(trust) && panels() === 1
        && host().querySelector(".market-confirm").getAttribute("role") === "alert"
        && count(".market-head") === 1;
    verdict.trustNotYetInstalled = lastInstall.confirm === undefined;
    await click(trust);
    verdict.trustReissuesWithConfirm = lastInstall.confirm === true
        && lastInstall.url === "https://github.com/acme/packs/new.yaml";
    await click(host().querySelector("#market-back"));
    verdict.urlInstallLands = texts(".market-card-title").includes("Imported Agent");

    // ── The detail's own ✕ dismisses the takeover, and it is the ONLY dismiss
    //    control the view has: there is no footer under it any more.
    await click(cardFor("feature-planner"));
    const closer = host().querySelector("#market-close");
    verdict.detailCloseIsNamed = closer.getAttribute("aria-label") === "Close the marketplace";
    verdict.detailHasOneDismissControl = count("#market-close") === 1
        && footerButtons() === 0;
    await click(closer);
    verdict.detailCloseDismissesTheTakeover = panels() === 0;

    // And the browse view's own ✕ does the same errand from the other view.
    handle = await openMarket();
    verdict.browseCloseDismissesTheTakeover = await (async () => {
        await click(host().querySelector("#market-close"));
        return panels() === 0;
    })();

    // ── The absence rules that are still real: a null section renders no
    //    heading, absent tools render no block. Nothing here is invented.
    resetServer();
    installed = [];
    catalogMode = "absent";
    handle = global.BossModMarketplace.open({});
    await drain();
    await click(cardFor("thin-pack"));
    // A section the pack does not carry gets no tab — and, because the list is
    // the only way into the panel, no empty panel either. What is left is one
    // entry and the body behind it.
    verdict.nullSectionsRenderNoTab = texts(".market-section-name").join("|")
        === "Done looks like"
        && count(".market-section-tab") === 1
        && sectionPanel().textContent === "A named diff exists.";
    verdict.absentToolsRenderNoBlock = count(".market-detail-tools") === 0
        && !texts(".market-section-name").includes("Tools");
    verdict.thinPackKeepsItsSpecialty =
        host().querySelector(".market-detail-specialty").textContent === "Does one thing";

    // A row with no `sections` is a shape NEITHER route can produce any more:
    // a pack that fails to parse is withheld at the source and never becomes a
    // card, and an installed row derives its sections from two NOT NULL
    // columns. It used to render as a bare card with a title and nothing else.
    // It is a contract break, so the projection names it instead of drawing a
    // blank card over it — and the name says which module and which row.
    verdict.aSectionlessRowIsANamedFailure = (() => {
        try {
            global.BossModMarketplaceItems.allItems({
                categories: [{
                    id: "engineering",
                    packs: [{
                        id: "broken-pack", kind: "agent", category: "engineering",
                        path: "packs/broken.yaml", title: "Unparsed Pack",
                    }],
                }],
                installedByPackId: {}, installedExtras: [],
                withheldByPackId: {}, pin: PIN,
            });
            return false;
        } catch (err) {
            const text = String((err && err.message) || err);
            return text.includes("[marketplace-items]") && text.includes("broken-pack");
        }
    })();

    // A description whose text starts on its first heading has an EMPTY
    // preamble, and an empty preamble draws no node — not an empty one.
    const emptyPreambleDetail = count(".market-detail-intro") === 0;

    // ── The lead-in the surface used to drop on the floor. Prose before the
    //    first heading comes back as `description.preamble` ALONGSIDE the
    //    mission — the two are not alternatives — and only the mission was
    //    ever printed. Both are drawn now, in the order they are read.
    await click(host().querySelector("#market-back"));
    verdict.emptyPreambleDrawsNoIntro = emptyPreambleDetail
        && cardFor("thin-pack").querySelectorAll(".market-card-intro").length === 0;
    const introCard = cardFor("intro-pack")
        .querySelectorAll(".market-card-intro, .market-card-desc");
    verdict.cardDrawsTheIntroAboveTheMission = introCard.length === 2
        && introCard[0].textContent === INTRO_LEAD
        && introCard[1].textContent === INTRO_MISSION;
    await click(cardFor("intro-pack"));
    // Decided once in the projection, so the card and the detail open on the
    // same two paragraphs in the same order.
    const introDetail = host()
        .querySelectorAll(".market-detail-intro, .market-detail-lead");
    verdict.detailDrawsTheIntroAboveTheMission = introDetail.length === 2
        && introDetail[0].textContent === INTRO_LEAD
        && introDetail[1].textContent === INTRO_MISSION;

    // ── The catalog INDEX row's When-to-hire line, which only stands in where
    //    the pack itself opens on a heading and has no lead-in to read. The
    //    pack is the file that gets installed, so its own words outrank the
    //    row's: `intro-pack` carries BOTH and its preamble wins — in full,
    //    not cut to the one line the route had room to send.
    await click(host().querySelector("#market-back"));
    const summaryCard = cardFor("summary-pack")
        .querySelectorAll(".market-card-intro, .market-card-desc");
    verdict.cardReadsTheIndexSummaryWhereThePackIsSilent = summaryCard.length === 2
        && summaryCard[0].textContent === SUMMARY_LINE
        && summaryCard[1].textContent === SUMMARY_MISSION;
    verdict.thePacksOwnLeadInOutranksTheIndexRow = introCard.length === 2
        && introCard[0].textContent === INTRO_LEAD
        && introCard[0].textContent !== INTRO_LEAD_FIRST;
    await click(cardFor("summary-pack"));
    const summaryDetail = host()
        .querySelectorAll(".market-detail-intro, .market-detail-lead");
    verdict.detailReadsTheIndexSummaryWhereThePackIsSilent = summaryDetail.length === 2
        && summaryDetail[0].textContent === SUMMARY_LINE
        && summaryDetail[1].textContent === SUMMARY_MISSION;
    handle.close();

    // ── The rows the grid would not show. Hiding the CARD is right — install
    //    runs the same gate the browse list does — but hiding the FACT leaves a
    //    maintainer merging a PR into their own catalog and watching the pack
    //    never appear, with no reason given anywhere.
    resetServer();
    catalogMode = "withheld";
    installed = [
        // Installed from a pack the catalog STILL LISTS and the app now refuses.
        template({ id: "t-auditor", pack_id: "code-auditor", title: "Code Auditor" }),
        // Installed from a pack that has LEFT the repo: no card, no withheld row.
        template({ id: "t-ghost", pack_id: "ghost-pack", title: "Ghost Pack" }),
        // A URL install, which was never in this catalog at all.
        template({
            id: "t-url", source: "url", pack_id: null, category: "imported",
            source_url: "github.com/acme/packs/notes.yaml", title: "Release Notes Writer",
        }),
    ];
    handle = global.BossModMarketplace.open({});
    await drain();
    const notice = host().querySelector(".market-withheld");
    verdict.withheldNoticeNamesBothKinds = Boolean(notice)
        && count(".market-withheld-row") === 2
        && notice.textContent.includes("Code Auditor")
        && notice.textContent.includes("packs/engineering/code-auditor.agent.yaml")
        && notice.textContent.includes(REFUSED_MESSAGE)
        && notice.textContent.includes("Feature Planner")
        && notice.textContent.includes("packs/product/feature-planner.agent.yaml")
        && notice.textContent.includes(UNAVAILABLE_MESSAGE);
    // The flat list is grouped by KIND, so the category each row came from is
    // the one thing a reader could not otherwise derive. It is on every row.
    verdict.withheldRowsCarryTheirCategory =
        texts(".market-withheld-where").join("|") === "engineering|product";
    // The grid beside it loaded FINE. One card, no failure state, no alert.
    verdict.withheldIsNotAFailedRead = count(".market-card") === 1
        && count(".market-failed") === 0;

    // Announced, politely, and scoped to one sentence — the browse view is
    // rebuilt on every keystroke in the filter box. Never `role="alert"`: this
    // is not an error, and an assertive interruption for something that is not
    // one is how operators learn to ignore alerts.
    const summary = host().querySelector(".market-withheld-sum");
    verdict.withheldIsAnnouncedAndNotAnAlert = summary.getAttribute("role") === "status"
        && notice.getAttribute("role") === null
        && notice.querySelectorAll('[role="alert"]').length === 0
        && summary.textContent
            === "2 packs in this catalog are not shown here: 1 refused, 1 unavailable.";
    // Announced is not the same as taken. Focus stays where the takeover put it.
    verdict.withheldDoesNotStealFocus = !notice.contains(global.document.activeElement)
        && focusIsInThePanel();

    // A refusal names the reason and offers nothing to press — the fix is in the
    // catalog repo, not in this app. An unavailability knows nothing about the
    // pack, so the only honest offer is to read the catalog again.
    const groups = host().querySelectorAll(".market-withheld-group");
    verdict.onlyTheUnreadableKindOffersARetry = groups.length === 2
        && groups[0].querySelectorAll("button").length === 0
        && Boolean(groups[1].querySelector("#market-withheld-retry"));

    // ── The one place withholding hides something from someone ALREADY
    //    affected: they are running agents built from that pack. A pack that
    //    left the repo and a pack the catalog still lists but the app refuses
    //    both fall out of the cards, and used to render identically.
    await click(railRow("Installed"));
    // By the TITLE node, not by the card's text stream: the stream now opens
    // with the category mark's letters.
    const noteFor = (title) => {
        const card = host().querySelectorAll(".market-card").find((el) => {
            const name = el.querySelector(".market-card-title");
            return Boolean(name) && name.textContent === title;
        });
        if (!card) throw new Error(`no installed card for ${title}`);
        const note = card.querySelector(".market-card-note");
        return note ? note.textContent : null;
    };
    const refusedNote = noteFor("Code Auditor");
    const goneNote = noteFor("Ghost Pack");
    verdict.aRefusedInstallIsNotAVanishedOne = Boolean(refusedNote) && Boolean(goneNote)
        && refusedNote !== goneNote
        && refusedNote.includes("still lists") && goneNote.includes("no longer")
        // A URL install was never in this catalog, so there is nothing to warn.
        && noteFor("Release Notes Writer") === null;
    await click(cardFor("code-auditor"));
    verdict.theRefusalFollowsIntoTheDetail = count(".market-detail-note") === 1
        && host().querySelector(".market-detail-note").textContent === refusedNote;
    await click(host().querySelector("#market-back"));
    await click(railRow("All"));

    // ── A read that FAILS drops the catalog it just lost. It did not: the rail
    //    went on offering a row per category out of a catalog the app no longer
    //    held, each one counting packs it could not show.
    catalogMode = "fail";
    await click(host().querySelector("#market-withheld-retry"));
    verdict.aFailedReadDropsTheStaleCatalog = count(".market-failed") === 1
        && railRow("Engineering") === null
        && railRow("All").textContent === "All0"
        && count(".market-withheld") === 0;
    // And the group goes with the rows: a `CATEGORIES` heading over nothing
    // claims a set the catalog does not have. The scopes group stays, because
    // `All` and `Installed` are answerable with no catalog at all.
    verdict.aFailedReadDrawsNoEmptyCategoryGroup =
        count(".market-rail-group") === 1
        && texts(".market-rail-title").join("|") === "Show";
    // And back, through the grid's own retry.
    catalogMode = "withheld";
    await click(host().querySelector("#market-retry"));
    verdict.aRecoveredReadPutsTheRailBack = railRow("Engineering").textContent
        === "Engineering1" && count(".market-card") === 1;

    // The retry is the action the unavailable kind offers, and a reload that
    // works deletes the button that was pressed — so it lands the keyboard
    // somewhere that is always there rather than dropping it on <body>.
    catalogMode = "ready";
    await click(host().querySelector("#market-withheld-retry"));
    verdict.withheldRetryReloadsAndKeepsTheKeyboard = count(".market-withheld") === 0
        && count(".market-card") === 3
        && global.document.activeElement === host().querySelector("#market-find");
    handle.close();

    // ── Empty catalog reads differently from a failed one.
    resetServer();
    catalogMode = "empty";
    installed = [];
    handle = global.BossModMarketplace.open({});
    await drain();
    verdict.emptyCopy = host().textContent.includes(global.BossModMarketplaceView.COPY.empty)
        && !host().querySelector(".market-failed");
    // An empty catalog draws no cards, so the head is all there is — and it
    // still has to hold a keyboard now that the footer holds nothing.
    verdict.emptyCanBeTabbed = footerButtons() === 0 && focusableStops() > 0
        && focusIsInThePanel() && Boolean(host().querySelector("#market-close"));
    handle.close();
    verdict.closesCleanly = panels() === 0;

    verdict.ok = true;
    process.stdout.write(JSON.stringify(verdict));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
