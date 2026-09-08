/**
 * Node harness: the agent form's two halves, EXECUTED.
 *
 * Invoked by tests/test_ui_visual_parity.py. Not a browser bundle.
 *
 * The suite already reads context/agent-form-fields.js as source
 * (test_hire_ui_poke.py, test_role_contracts.py). That is why an arity bug sat
 * in the connections matrix undetected: `connectionSelect(t.key, value)` called
 * a three-parameter function with two, so `connections.map` was not a function
 * and the whole fieldset threw — but only for an operator who had at least one
 * connection configured, i.e. every real user. A source grep cannot see that.
 * So this harness CALLS the builders and reads what comes back.
 *
 * It also drives context/agent-submit.js, which is where the seed-legibility
 * clamp has to bite: the check is worth nothing unless the save path actually
 * refuses, so the refusal is proven by calling buildSubmitData() rather than by
 * grepping for the guard.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();

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
const NAMES = [
    "BossModDom", "BossModFormat", "BossModAgentStatus", "BossModAvatar",
    "BossModAgentFields", "BossModAgentFormFields", "BossModAgentSubmit",
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

const CONNECTIONS = [
    { id: "c1", name: "Local llama", model: "llama3.1:8b", api_base_url: "http://x/v1" },
    { id: "c2", name: "Cloud", model: "gpt-4o-mini", api_base_url: "http://y/v1" },
];

async function submitting(values) {
    try {
        const out = await BossModAgentSubmit.buildSubmitData(makeForm(values), CONNECTIONS);
        return { threw: false, data: out.agentData };
    } catch (err) {
        return { threw: true, message: String((err && err.message) || err) };
    }
}

// ─── 3. The connections matrix, built rather than read ───

const MODEL_TYPES = BossModAgentFields.MODEL_TYPES;
const AGENT = { id: "a1", name: "Nadia", model_social: "gpt-4o-mini" };

function selectNames(markup) {
    return (markup.match(/<select\s+name="([^"]+)"/g) || [])
        .map((tag) => tag.match(/name="([^"]+)"/)[1]);
}

async function main() {
    const pale = await submitting({ name: "Pale", "agent-color": "#ffe066" });
    const seeded = await submitting({ name: "Fine", "agent-color": PALETTE[0] });

    let matrixError = null;
    let markup = "";
    try {
        markup = BossModAgentFormFields.connectionsSection(AGENT, CONNECTIONS);
    } catch (err) {
        matrixError = String((err && err.message) || err);
    }
    const names = selectNames(markup);
    const empty = BossModAgentFormFields.connectionsSection(AGENT, []);

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

    process.stdout.write(JSON.stringify({
        ok: true,

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

        // ── The connections matrix ──
        matrixError,
        matrixSelectNames: names,
        // "Set All" plus one dropdown per activation type, in that order.
        matrixCoversEveryModelType:
            names.join(",") === ["model_all"].concat(MODEL_TYPES.map((t) => t.key)).join(","),
        // Proof the connection LIST reached the builder rather than the model
        // key string: every select carries both connections as options.
        matrixRendersConnectionOptions: names.length > 0
            && (markup.match(/value="c1"/g) || []).length === MODEL_TYPES.length + 1
            && (markup.match(/value="c2"/g) || []).length === MODEL_TYPES.length + 1,
        // ...and the value already stored on the agent comes back selected.
        matrixPreselectsTheStoredModel:
            /<option value="c2" selected>/.test(markup),
        // The empty case still offers the way out rather than a blank matrix.
        emptyMatrixLinksToSettings: empty.includes("btn-goto-connections")
            && selectNames(empty).length === 0,

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
