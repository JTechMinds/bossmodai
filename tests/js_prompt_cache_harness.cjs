/**
 * Node harness: prompt-cache shares on screen — core/format.js's
 * formatSharePercent, the Metrics Prompt Cache card's three states, the
 * token bar's note, and the Log's turn fact and trace-step note.
 *
 * Invoked by tests/test_ui_metrics.py. Not a browser bundle. The blocks are
 * built the way db/metrics.py prompt_cache_block builds them; every screen
 * only formats what the server computed (the Log's one turn share aside,
 * which a diagnostic summary does not carry).
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();
global.navigator = { clipboard: { writeText: async () => {} } };

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModFormat", "BossModFactList", "BossModMetricCards", "BossModMetricBars",
    "BossModLogShape", "BossModDiagnosticDetail",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});
const {
    BossModFormat: U, BossModMetricCards: CARDS, BossModMetricBars: BARS,
    BossModLogShape: SHAPE, BossModDiagnosticDetail: DETAIL,
} = global;

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

function threw(build) {
    try {
        build();
        return false;
    } catch (err) {
        return true;
    }
}

/** The server's block (db/metrics.py prompt_cache_block). */
function block(prompt, cached, reported) {
    return {
        prompt_tokens: prompt,
        cached_tokens: cached,
        reported_prompt_tokens: reported,
        cached_share: prompt ? cached / prompt : null,
        unreported_share: prompt ? (prompt - reported) / prompt : null,
    };
}

// ── formatSharePercent ─────────────────────────────────────────────────
const shares = [0, 0.004, 0.005, 0.41, 0.994, 0.995, 0.996, 1].map(U.formatSharePercent);
const sharesRoundHonestly = JSON.stringify(shares)
    === JSON.stringify(["0%", "<1%", "1%", "41%", "99%", ">99%", ">99%", "100%"]);
if (!sharesRoundHonestly) fail(`formatSharePercent boundaries: ${JSON.stringify(shares)}`);
const unreadableSharesThrow = [null, undefined, NaN, -0.01, 1.01, "0.4"]
    .every((value) => threw(() => U.formatSharePercent(value)));
if (!unreadableSharesThrow) fail("formatSharePercent must refuse anything that is not a number in 0..1");

// ── The Prompt Cache card ──────────────────────────────────────────────
function promptCacheCard(cache) {
    const cards = CARDS.renderStatCards({ tokens: { total: 1, all_time: 1, prompt_cache: cache } });
    const found = cards.querySelectorAll("article")
        .filter((card) => card.querySelector(".metric-card-label").textContent === "Prompt Cache");
    if (found.length !== 1) fail(`expected one Prompt Cache card, found ${found.length}`);
    const card = found[0];
    return [card.querySelector(".metric-card-value").textContent, card.querySelector(".metric-card-sub").textContent];
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const cardSaysNoData = same(promptCacheCard(block(0, 0, 0)), ["No data", "No model calls in this period"]);
if (!cardSaysNoData) fail(`no prompt tokens: ${JSON.stringify(promptCacheCard(block(0, 0, 0)))}`);
const cardSaysNotReported = same(promptCacheCard(block(1000, 0, 0)),
    ["Not reported", "Your AI connections did not report cache use"]);
if (!cardSaysNotReported) fail(`nothing reported: ${JSON.stringify(promptCacheCard(block(1000, 0, 0)))}`);
const cardShowsTheShare = same(promptCacheCard(block(1000, 410, 1000)), ["41%", "of prompt tokens were cached"]);
if (!cardShowsTheShare) fail(`all reported: ${JSON.stringify(promptCacheCard(block(1000, 410, 1000)))}`);
const cardNamesTheUnreported = same(promptCacheCard(block(1000, 410, 700)),
    ["41%", "of prompt tokens were cached · 30% not reported"]);
if (!cardNamesTheUnreported) fail(`part reported: ${JSON.stringify(promptCacheCard(block(1000, 410, 700)))}`);
const cardRefusesAMissingBlock = threw(() => CARDS.renderStatCards({ tokens: { total: 1 } }));
if (!cardRefusesAMissingBlock) fail("a dashboard without a prompt_cache block must throw, not show a guess");

// ── The token bars ─────────────────────────────────────────────────────
const usage = BARS.renderTokenUsage({
    by_agent: [
        { agent_id: "a1", agent_name: "Ada", total_tokens: 2000, api_calls: 3, prompt_cache: block(1800, 738, 1800) },
        { agent_id: "a2", agent_name: "Bo", total_tokens: 900, api_calls: 1, prompt_cache: block(800, 0, 0) },
        { agent_id: "a3", agent_name: "Cy", total_tokens: 0, api_calls: 1, prompt_cache: block(0, 0, 0) },
    ],
}, { roster: [], onOpenLog() {} });
const barValues = usage.querySelectorAll(".metric-row-value").map((node) => node.textContent);
const barsNoteTheCache = same(barValues, [
    "2K tokens · 3 calls · 41% cached",
    "900 tokens · 1 calls · cache not reported",
    "0 tokens · 1 calls",
]);
if (!barsNoteTheCache) fail(`token bar notes: ${JSON.stringify(barValues)}`);

// ── The Log ────────────────────────────────────────────────────────────
function turnFact(cached, reported, prompt) {
    const row = SHAPE.fromDiagnostic({
        id: "d1", agent_id: "a1", agent_name: "Ada", trigger_type: "human_chat", status: "success",
        prompt_tokens: prompt, cached_prompt_tokens: cached, cache_reported_prompt_tokens: reported,
        total_tokens: prompt + 10, duration_ms: 5, created_at: "2026-11-11T12:00:00.000Z",
    });
    const found = row.facts.filter((entry) => entry.label === "Prompt cache");
    return found.length ? found[0].value : null;
}
const logFactReads = turnFact(12345, 52000, 52000) === "12,345 of 52,000 tokens cached (24%)"
    && turnFact(0, 0, 52000) === "Not reported"
    && turnFact(0, 0, 0) === null;
if (!logFactReads) {
    fail(`log fact: ${JSON.stringify([turnFact(12345, 52000, 52000), turnFact(0, 0, 52000), turnFact(0, 0, 0)])}`);
}

const trace = global.BossModDom.h("div", null, ...DETAIL.detailSections({
    steps: [
        { step_index: 1, action_name: "bm_cli", prompt_tokens: 52000, completion_tokens: 10, total_tokens: 52010,
            cached_prompt_tokens: 12345, cache_reported_prompt_tokens: 52000, duration_ms: 5 },
        { step_index: 2, action_name: "reply", prompt_tokens: 900, completion_tokens: 10, total_tokens: 910,
            cached_prompt_tokens: 0, cache_reported_prompt_tokens: 0, duration_ms: 5 },
        { step_index: 3, action_name: "reply", prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
            cached_prompt_tokens: 0, cache_reported_prompt_tokens: 0, duration_ms: 5 },
    ],
}));
const metas = trace.querySelectorAll(".log-step-meta").map((node) => node.textContent);
const traceStepsNoteTheCache = same(metas, [
    "52000 / 10 / 52010 tok · 5ms · 12.3K cached",
    "900 / 10 / 910 tok · 5ms · cache not reported",
    "0 / 0 / 0 tok · 5ms",
]);
if (!traceStepsNoteTheCache) fail(`trace meta: ${JSON.stringify(metas)}`);

process.stdout.write(`${JSON.stringify({
    ok: true,
    sharesRoundHonestly,
    unreadableSharesThrow,
    cardSaysNoData,
    cardSaysNotReported,
    cardShowsTheShare,
    cardNamesTheUnreported,
    cardRefusesAMissingBlock,
    barsNoteTheCache,
    logFactReads,
    traceStepsNoteTheCache,
})}\n`);
