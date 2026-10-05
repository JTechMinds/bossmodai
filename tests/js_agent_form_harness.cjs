/**
 * Node harness: the agent form's two halves, EXECUTED.
 *
 * Invoked by tests/test_ui_visual_parity.py. Not a browser bundle.
 *
 * The suite already reads context/agent-form-fields.js as source
 * (test_hire_ui_poke.py, test_role_contracts.py). That is why an arity bug once
 * sat in the connections section undetected: a builder called with too few
 * arguments threw for every operator who had at least one connection
 * configured, i.e. every real user. A source grep cannot see that. So this
 * harness CALLS the builders and reads what comes back.
 *
 * It also drives context/agent-submit.js, which is where the seed-legibility
 * clamp has to bite: the check is worth nothing unless the save path actually
 * refuses, so the refusal is proven by calling buildSubmitData() rather than by
 * grepping for the guard.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

const documentStub = installDom();
installIconsStub();

// BossModFormat.escapeHtml round-trips a string through textContent and reads
// `innerHTML` back off the scratch node. The shared fake element has no such
// accessor, so give the elements THIS harness creates one — an own property, so
// no other harness's element behaviour changes. The three characters below are
// exactly what a browser's serialiser escapes in a text node.
const createElement = documentStub.createElement.bind(documentStub);
documentStub.createElement = (tag) => {
    const el = createElement(tag);
    Object.defineProperty(el, "innerHTML", {
        get() {
            return String(this.textContent)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");
        },
        set(value) { this.textContent = value; },
        configurable: true,
    });
    return el;
};

const paths = process.argv.slice(2);
// The dropdowns are BossModMenuSelects the bindings mount after the markup,
// so the menu and its panel load too, and the two modules that mount them.
const NAMES = [
    "BossModDom", "BossModFormat", "BossModAgentStatus", "BossModAvatar",
    "BossModCommunication",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays",
    "BossModMenu", "BossModMenuSelect",
    "BossModAgentFields", "BossModAgentFormFields", "BossModAgentFormAdvanced",
    "BossModAgentFormChoices", "BossModAgentFormConnections", "BossModAgentFormBindings",
    "BossModAgentSubmit",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

// ─── An independent WCAG implementation, for the clamp's derivation ───

function channel(value) {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex) {
    const match = /^#([0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!match) throw new Error(`[agent-form-harness] unparseable colour ${hex}`);
    const n = parseInt(match[1], 16);
    return 0.2126 * channel((n >> 16) & 255)
        + 0.7152 * channel((n >> 8) & 255)
        + 0.0722 * channel(n & 255);
}

/** --office-transit, the lightest tile a sprite is ever drawn on. */
const TRANSIT = "#f3f4f6";

function ratioAgainstTransit(hexLuminance) {
    return (luminance(TRANSIT) + 0.05) / (hexLuminance + 0.05);
}

// ─── 1. The seed clamp ───

const PALETTE = BossModAgentStatus.AGENT_COLOR_PALETTE;
const illegibleSeeds = PALETTE.filter((seed) => !BossModAvatar.isSeedLegible(seed));
const bound = BossModAvatar.SEED_MAX_LUMINANCE;

// ─── 2. The save path refuses a seed nobody could see ───

/**
 * A stand-in for the real form. `buildSubmitData` reads it only through
 * FormData.get(), so a values map is the whole of the contract it uses.
 */
function makeForm(values) {
    return { values };
}

global.FormData = class {
    constructor(form) { this.values = (form && form.values) || {}; }

    get(key) {
        return Object.prototype.hasOwnProperty.call(this.values, key)
            ? this.values[key]
            : null;
    }
};

// Only Cloud defines thinking levels, so what a control offers visibly
// follows the connection.
const CONNECTIONS = [
    { id: "c1", name: "Local llama", model: "llama3.1:8b", api_base_url: "http://x/v1", thinking_levels: null },
    {
        id: "c2", name: "Cloud", model: "gpt-4o-mini", api_base_url: "http://y/v1",
        thinking_levels: { off: { thinking: { type: "disabled" } }, high: { thinking: { type: "high" } } },
    },
];

async function submitting(values) {
    try {
        const out = await BossModAgentSubmit.buildSubmitData(makeForm(values));
        return { threw: false, data: out.agentData };
    } catch (err) {
        return { threw: true, message: String((err && err.message) || err) };
    }
}

// ─── 3. The AI Connection section, built rather than read ───

const THINKING_MODES = BossModAgentFields.THINKING_MODES;
const CONN = BossModAgentFormConnections;
// Linked to Cloud, Work at a level Cloud offers.
const AGENT = {
    id: "a1", name: "Nadia", connection_id: "c2", thinking_social: "default", thinking_work: "high",
};
// Linked to Local, which offers no levels, with a level stored from before.
const STALE = {
    id: "a2", name: "Bo", connection_id: "c1", thinking_social: "low", thinking_work: "default",
};
// What the upgrade leaves when it cannot tell which connection an agent used.
const UNLINKED = {
    id: "a3", name: "Cy", connection_id: null, thinking_social: "default", thinking_work: "default",
};

/**
 * A form holding an empty node for every mount point `markup` declares, as
 * the real form does once the markup is in the DOM. The ids come FROM the
 * markup, so a builder that stopped rendering one leaves its control nowhere
 * to mount and the binding below throws.
 */
function formWithMounts(markup) {
    const form = documentStub.createElement("form");
    for (const match of markup.matchAll(/id="([A-Za-z0-9_-]+)"/g)) {
        if (!/^agent-ai-mount-|-mount$/.test(match[1])) continue;
        const point = documentStub.createElement("div");
        point.setAttribute("id", match[1]);
        form.append(point);
    }
    return form;
}

/**
 * The AI section for `values`, rendered and then bound the way
 * context/agent-form.js binds it: the dropdowns mounted, each carrying the
 * hidden input the submit reads.
 */
function boundSection(values, connections) {
    const form = formWithMounts(CONN.connectionsSection(values, connections));
    BossModAgentFormBindings.bindAiConnection(form, connections, values);
    return form;
}

/** The form value named `name` — a dropdown's hidden input — or null. */
function formValue(form, name) {
    const node = form.querySelector(`input[name="${name}"]`);
    return node ? node.value : null;
}

/** How many form values are named `name`. */
function formValueCount(form, name) {
    return form.querySelectorAll(`input[name="${name}"]`).length;
}

const labels = (options) => options.map((option) => option.label).join("|");

async function main() {
    const pale = await submitting({ name: "Pale", "agent-color": "#ffe066" });
    const seeded = await submitting({ name: "Fine", "agent-color": PALETTE[0] });

    let sectionError = null;
    let markup = "";
    let stale = "";
    let unlinked = "";
    let blank = "";
    // The same four, bound: what the dropdowns hold once mounted.
    let bound4 = null;
    try {
        markup = CONN.connectionsSection(AGENT, CONNECTIONS);
        stale = CONN.connectionsSection(STALE, CONNECTIONS);
        unlinked = CONN.connectionsSection(UNLINKED, CONNECTIONS);
        blank = CONN.connectionsSection(null, CONNECTIONS);
        bound4 = {
            agent: boundSection(AGENT, CONNECTIONS),
            stale: boundSection(STALE, CONNECTIONS),
            unlinked: boundSection(UNLINKED, CONNECTIONS),
            blank: boundSection(null, CONNECTIONS),
        };
    } catch (err) {
        sectionError = String((err && err.message) || err);
    }
    const empty = CONN.connectionsSection(AGENT, []);
    const boundEmpty = boundSection(AGENT, []);
    const picked = await submitting({
        name: "Picked", "agent-color": PALETTE[0],
        connection_id: "c2", thinking_social: "off", thinking_work: "default",
    });
    const unpicked = await submitting({
        name: "Unpicked", "agent-color": PALETTE[0],
        connection_id: "", thinking_social: "default", thinking_work: "default",
    });

    // ── An agent hired before the palette changed ──
    // The round trip that must not lose a colour: render the form for an agent
    // whose stored seed no longer appears in the palette, and confirm a radio
    // carrying that exact value comes back checked. If none is, the form
    // submits nothing for agent-color and the agent is silently recoloured.
    const LEGACY = "#f59e0b";
    const legacyCard = BossModAgentFormFields.roleContractCard(
        { id: "a9", name: "Jim", color: LEGACY }, [],
    );
    const legacyRadio = new RegExp(
        `<input type="radio" name="agent-color" value="${LEGACY}"\\s+checked`,
    );
    const paletteCard = BossModAgentFormFields.roleContractCard(
        { id: "a8", name: "Ada", color: PALETTE[2] }, [],
    );
    const countRadios = (m) => (m.match(/name="agent-color"/g) || []).length;

    // ── The field inventory ──
    //
    // Moving the form into a dialog is a CONTAINER change, so the thing that
    // could go wrong is a field quietly not being built any more. Read off the
    // markup the six builders produce, which is what a container receives —
    // and produced for real rather than grepped, because a builder that throws
    // renders nothing and a grep cannot tell the difference.
    const editAgent = {
        id: "a1", name: "Nadia", role: "Writer", description: "Drafts things.",
        color: PALETTE[1], status: "idle", currentActivityKind: null,
        desk_x: null, desk_y: null, connection_id: "c2",
        thinking_social: "default", thinking_work: "default",
    };
    const advanced = BossModAgentFormAdvanced.advancedSection(editAgent, {
        roster: [],
        promptHistoryPolicy: BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY,
    });
    const editMarkup = [
        BossModAgentFormFields.nameField(editAgent),
        BossModAgentFormFields.roleContractCard(editAgent, []),
        BossModAgentFormConnections.connectionsSection(editAgent, CONNECTIONS),
        advanced,
        BossModAgentFormFields.statusAndRecovery(editAgent),
        BossModAgentFormFields.actionsRow(editAgent),
    ].join("\n");
    // The edit form once its dropdowns are mounted, as context/agent-form.js
    // mounts them: the Advanced choices, then the AI section.
    const editForm = formWithMounts(editMarkup);
    BossModAgentFormChoices.mount(editForm, { roster: [], values: editAgent });
    BossModAgentFormBindings.bindAiConnection(editForm, CONNECTIONS, editAgent);
    const hireMarkup = [
        BossModAgentFormFields.nameField(null),
        BossModAgentFormFields.roleContractCard(null, []),
        BossModAgentFormFields.actionsRow(null),
    ].join("\n");

    const formFields = {
        name: /<input type="text" name="name"/.test(editMarkup),
        role: /<input type="text" name="role"/.test(editMarkup),
        description: /<textarea name="description"/.test(editMarkup),
        color: /name="agent-color"/.test(editMarkup),
        desk: formValueCount(editForm, "desk") === 1
            && editMarkup.includes("Desk Assignment"),
        // The connection and every routed activation's thinking level, not
        // merely the word "connection".
        connections: formValueCount(editForm, "connection_id") === 1
            && THINKING_MODES.every((mode) => formValueCount(editForm, mode.key) === 1),
        prompt_history: ["prompt_history_last_n", "prompt_history_max_tokens",
            "prompt_history_earliest_ts", "prompt_history_include_notifications"]
            .every((name) => editMarkup.includes(`name="${name}"`)),
    };
    // The recovery tools and the runtime pill are edit-only and travel with it.
    //
    // Round four pinned `#agent-form-submit` in the DIALOG's action row, so it
    // is no longer part of this markup and cannot be looked for here. The
    // property it carried — the flow still has a way to save — moved with it
    // and is proven on built nodes by tests/test_ui_polish_round_four.py's
    // `submitIdCount` and `pinnedPrimarySubmitsTheForm`. What is still this
    // markup's to answer is that Delete stayed behind, in the form body and
    // away from the primary.
    const editFlowStillOffersRemove = editMarkup.includes('id="btn-delete-agent"')
        && editMarkup.includes('id="btn-clear-chat-history"')
        && editMarkup.includes('id="btn-reset-runtime"')
        && !editMarkup.includes('id="agent-form-submit"');
    // ...and hiring offers no Delete, because there is nothing to delete yet.
    const hireFlowOffersNoRemove = !hireMarkup.includes('id="btn-delete-agent"')
        && !hireMarkup.includes('id="agent-form-submit"');

    process.stdout.write(JSON.stringify({
        ok: true,
        formFields,
        editFlowStillOffersRemove,
        hireFlowOffersNoRemove,
        // The same refusal `submitRefusesAPaleColour` reports, under the name
        // the move asks about: the clamp is a save-path guard, so moving the
        // form to a dialog must not have taken it off the path.
        colourClampStillEnforced: pale.threw === true && seeded.threw === false,

        // ── The clamp ──
        // Derived from the token, not typed: the bound is whatever luminance
        // measures exactly 3:1 against --office-transit.
        seedMaxLuminance: Number(bound.toFixed(6)),
        boundIsDerivedFromTheTransitTile:
            Math.abs(bound - ((luminance(TRANSIT) + 0.05) / 3 - 0.05)) < 1e-9,
        // ...and the bound is not rounded the wrong way: a seed sitting exactly
        // on it must still measure 3:1 or better (SC 1.4.11).
        boundClearsNonTextContrast: ratioAgainstTransit(bound) >= 3,
        allSeedsLegible: illegibleSeeds.length === 0,
        illegibleSeeds,
        rejectsPaleSeed: BossModAvatar.isSeedLegible("#ffe066") === false,
        acceptsPaletteSeed: BossModAvatar.isSeedLegible(PALETTE[0]) === true,
        // An unparseable or absent colour is not a legible one either: the
        // clamp must never wave through what it could not measure.
        rejectsUnparseableSeed: BossModAvatar.isSeedLegible("periwinkle") === false,
        rejectsAbsentSeed: BossModAvatar.isSeedLegible(null) === false,

        // ── The save path ──
        submitRefusesAPaleColour: pale.threw === true,
        submitSaysWhy: pale.threw
            && pale.message.includes("too light to see on the office floor"),
        submitAcceptsAPaletteColour: seeded.threw === false
            && seeded.data.color === PALETTE[0],

        // ── The AI Connection section ──
        sectionError,
        // Never a native <select>: the controls are BossModMenuSelect,
        // mounted by context/agent-form-bindings.js.
        sectionHasNoNativeSelect: markup.length > 0 && !/<select\b/.test(markup),
        // One connection and one thinking level per ROUTED activation — and
        // nothing per-mode about the model.
        sectionCoversEveryRoutedMode: Boolean(bound4)
            && formValueCount(bound4.agent, "connection_id") === 1
            && THINKING_MODES.length === 2
            && THINKING_MODES.every((mode) => formValueCount(bound4.agent, mode.key) === 1)
            && !markup.includes('name="model_')
            && bound4.agent.querySelectorAll("input").every((node) => !node.getAttribute("name").startsWith("model_")),
        // The stored choice comes back in the inputs the save reads.
        sectionCarriesTheStoredChoice: Boolean(bound4)
            && formValue(bound4.agent, "connection_id") === "c2"
            && formValue(bound4.agent, "thinking_work") === "high"
            && formValue(bound4.agent, "thinking_social") === "default"
            && !markup.includes('id="agent-connection-missing"'),
        // A level the connection no longer offers cannot be shown, so it falls
        // back to Server default, and the note names what was stored.
        anUnofferedLevelIsResetAndNamed: Boolean(bound4)
            && formValue(bound4.stale, "thinking_social") === "default"
            && stale.includes("Social: thinking “low” — this connection doesn't offer it; pick one."),
        anUnlinkedAgentIsToldToChoose: Boolean(bound4)
            && formValue(bound4.unlinked, "connection_id") === ""
            && unlinked.includes("This agent has no AI connection — choose one."),
        // A blank form has nothing stored, so nothing to report.
        aBlankFormSaysNothing: Boolean(bound4)
            && formValue(bound4.blank, "connection_id") === ""
            && !blank.includes('id="agent-connection-missing"'),
        // What each control offers.
        thinkingOptionsFollowTheConnection:
            labels(CONN.thinkingOptions(CONNECTIONS[1])) === "Server default|Off|High"
            && labels(CONN.thinkingOptions(CONNECTIONS[0])) === "Server default"
            && labels(CONN.thinkingOptions(null)) === "Server default",
        connectionOptionsNameTheModel:
            labels(CONN.connectionOptions(CONNECTIONS, ""))
                === "Choose a connection|Local llama (llama3.1:8b)|Cloud (gpt-4o-mini)"
            && labels(CONN.connectionOptions(CONNECTIONS, "c1"))
                === "Local llama (llama3.1:8b)|Cloud (gpt-4o-mini)",
        // Every control is named by visible text, and each thinking control
        // says which turns it is for.
        everyControlIsNamedOnScreen: markup.includes(">Connection<")
            && THINKING_MODES.every((mode) => markup.includes(`>${mode.label} thinking<`)
                && markup.includes(mode.hint)),
        // The connection spans the column; the two thinking controls share
        // the grid under it.
        thinkingIsAGridOfTwo: (() => {
            const grid = markup.indexOf("connection-grid");
            const connection = markup.indexOf("agent-ai-mount-connection_id");
            return grid !== -1 && connection !== -1 && connection < grid
                && THINKING_MODES.every((mode) => markup.indexOf(`agent-ai-mount-${mode.key}`) > grid);
        })(),
        // The empty case still offers the way out rather than a blank section.
        emptySectionLinksToSettings: empty.includes("btn-goto-connections")
            && !empty.includes("agent-ai-mount-connection_id")
            && formValue(boundEmpty, "connection_id") === null,
        // What the save sends: the id and the levels, nothing a connection owns.
        submitSendsTheConnectionAndLevels: picked.threw === false
            && picked.data.connection_id === "c2"
            && picked.data.thinking_social === "off"
            && picked.data.thinking_work === "default"
            && !Object.keys(picked.data).some((key) => key.startsWith("model_")
                || key === "api_base_url" || key === "extra_body" || key === "api_key"),
        submitSendsNullWhileUnchosen: unpicked.threw === false && unpicked.data.connection_id === null,

        // ── The legacy-colour round trip ──
        legacyColourIsOffered: legacyRadio.test(legacyCard),
        legacyColourAddsExactlyOneSwatch: countRadios(legacyCard) === PALETTE.length + 1,
        // ...and an agent already on a palette seed gains no ninth swatch.
        paletteColourAddsNoSwatch: countRadios(paletteCard) === PALETTE.length,
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
