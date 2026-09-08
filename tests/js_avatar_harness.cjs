/**
 * Node harness: the derived avatar pair clears AA over the whole colour space.
 *
 * Invoked by tests/test_ui_visual_parity.py. Not a browser bundle.
 *
 * Agent colours are arbitrary operator-set hex, so "the tint looks fine" is not
 * a property of the eight seeds we ship — it is a property of the derivation.
 * This sweeps a 216-colour grid over the whole RGB cube and reports the worst
 * pair it could find.
 *
 * The contrast maths below is written out HERE, deliberately. Importing
 * core/avatar.js's own helper would only prove the module agrees with itself;
 * the point is that an independent implementation of the WCAG formula reaches
 * the same verdict.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const paths = process.argv.slice(2);
const NAMES = ["BossModDom", "BossModAgentStatus", "BossModAvatar"];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

// ─── WCAG 2.2 relative luminance, independently ───

function channel(value) {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** @returns {number[]|null} null for anything that is not `#rrggbb`. */
function toRgb(value) {
    const match = /^#([0-9a-f]{6})$/i.exec(String(value).trim());
    if (!match) return null;
    const n = parseInt(match[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance(rgb) {
    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

function contrast(a, b) {
    const ra = toRgb(a);
    const rb = toRgb(b);
    if (!ra || !rb) throw new Error(`[avatar-harness] unparseable pair ${a} / ${b}`);
    const la = luminance(ra);
    const lb = luminance(rb);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

// ─── 1. The sweep: 6 steps per channel, the whole cube ───

const HEXES = [];
for (let r = 0; r < 256; r += 51) {
    for (let g = 0; g < 256; g += 51) {
        for (let b = 0; b < 256; b += 51) {
            HEXES.push(`#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`);
        }
    }
}

let worst = 21;
let worstHex = null;
for (const hex of HEXES) {
    const { bg, ink } = BossModAvatar.tintFor(hex);
    const ratio = contrast(bg, ink);
    if (ratio < worst) {
        worst = ratio;
        worstHex = hex;
    }
}

// ─── 2. The seeds must not drift ───
//
// A seed that needs darkening renders as a different colour than the swatch
// that offered it, so the picker stops being an honest preview of the avatar.

const paletteDrift = [];
BossModAgentStatus.AGENT_COLOR_PALETTE.forEach((seed) => {
    const { bg, ink } = BossModAvatar.tintFor(seed);
    if (String(ink).toLowerCase() !== String(seed).toLowerCase()) {
        paletteDrift.push({
            seed,
            ink,
            seedRatio: Number(contrast(bg, seed).toFixed(2)),
        });
    }
});

// ─── 2b. The seed picker still picks ───
//
// nextUnusedAgentColor() reads AGENT_COLOR_PALETTE by reference, so swapping
// the seeds must not have changed how it chooses. Case matters: agent colours
// arrive from the database in whatever case they were stored in.

const PALETTE = BossModAgentStatus.AGENT_COLOR_PALETTE;
const twoTaken = [
    { id: "a1", color: PALETTE[0] },
    { id: "a2", color: PALETTE[1].toUpperCase() },
];
const everyoneSeated = PALETTE.map((color, index) => ({ id: `a${index}`, color }));

// ─── 3. The states that are not a colour ───

const neutral = BossModAvatar.tintFor(null);
const empty = BossModAvatar.tintFor("");

const errors = [];
const realError = console.error;
console.error = (...args) => { errors.push(args.map(String).join(" ")); };
const malformed = BossModAvatar.tintFor("not-a-colour");
const errorsAfterMalformed = errors.length;
BossModAvatar.tintFor(null);
const errorsAfterNull = errors.length;
console.error = realError;

// ─── 4. create() builds the node the surfaces mount ───

const decorative = BossModAvatar.create({ name: "jim", color: "#1d4ed8", size: "chip" });
const interactive = BossModAvatar.create({
    name: "Laura", color: "#92400e", size: "md",
    interactive: true, label: "Open Laura's desk", onClick() {},
});

function threw(build) {
    try {
        build();
        return false;
    } catch (err) {
        return true;
    }
}

process.stdout.write(JSON.stringify({
    ok: true,

    swept: HEXES.length,
    worstRatio: Number(worst.toFixed(2)),
    worstHex,
    meetsAA: worst >= 4.5,

    paletteDriftFree: paletteDrift.length === 0,
    paletteDrift,
    paletteSize: PALETTE.length,

    nextUnusedOnEmptyRoster: BossModAgentStatus.nextUnusedAgentColor([]) === PALETTE[0],
    nextUnusedSkipsTaken: BossModAgentStatus.nextUnusedAgentColor(twoTaken) === PALETTE[2],
    nextUnusedWrapsWhenFull: PALETTE.includes(
        BossModAgentStatus.nextUnusedAgentColor(everyoneSeated)
    ),
    nextUnusedIgnoresTheAgentBeingEdited: BossModAgentStatus.nextUnusedAgentColor(
        twoTaken, { excludeId: "a1" }
    ) === PALETTE[0],

    neutralWhenNull: neutral.bg === "var(--line)" && neutral.ink === "var(--muted)",
    neutralWhenEmpty: empty.bg === "var(--line)",
    malformedIsNeutral: malformed.bg === "var(--line)",
    malformedLogs: errorsAfterMalformed === 1 && errors[0].includes("[avatar]"),
    absentColourIsSilent: errorsAfterNull === errorsAfterMalformed,

    decorativeTag: decorative.tagName,
    decorativeHidden: decorative.getAttribute("aria-hidden") === "true",
    decorativeClass: decorative.getAttribute("class"),
    decorativeInitial: decorative.textContent,
    decorativeStyle: decorative.getAttribute("style"),

    interactiveTag: interactive.tagName,
    interactiveLabel: interactive.getAttribute("aria-label"),
    interactiveClass: interactive.getAttribute("class"),
    interactiveInitial: interactive.textContent,
    // An interactive avatar with no accessible name, or with no handler, is a
    // dead control — both must fail loudly at construction rather than render.
    unknownSizeThrows: threw(() => BossModAvatar.create({ name: "A", color: null, size: "huge" })),
    unlabelledButtonThrows: threw(() => BossModAvatar.create({
        name: "A", color: null, size: "md", interactive: true, onClick() {},
    })),
    handlerlessButtonThrows: threw(() => BossModAvatar.create({
        name: "A", color: null, size: "md", interactive: true, label: "Open A",
    })),
}));
