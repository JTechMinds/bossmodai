/**
 * Node harness: settings-context-panel.js on its own. layout(budget) is read
 * as markup (its slots, numbers, bars, starting points, Custom chip and
 * escaping); wire() runs against fake buttons: a click POSTs the preset and
 * calls onApplied, a refusal writes the alert line and gives the buttons
 * back. argv: format.js, settings-context-panel.js, then a JSON object of
 * budget payloads {medium, custom, measured, recovery} from the real route
 * (measured: one agent's instructions recorded, so the Model card names it;
 * recovery: settings that break two rules, so the tab is the recovery card).
 * Invoked by tests/test_context_budget.py. Not a browser bundle.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");

installDom();

// BossModFormat.escapeHtml serialises a text node through innerHTML.
Object.defineProperty(FakeEl.prototype, "innerHTML", {
    configurable: true,
    get() {
        return String(this._text ?? this.textContent ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    },
});

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModContextPanel = BossModContextPanel;\n`);
const budgets = JSON.parse(process.argv[4]);

function all(markup, re) {
    return Array.from(markup.matchAll(re), (match) => match[1]);
}

/** Markup as the text a reader gets: tags dropped, entities decoded, spaces collapsed. */
function text(markup) {
    return markup.replace(/<[^>]+>/g, "")
        .replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">")
        .replace(/&amp;/g, "&")
        .replace(/\s+/g, " ").trim();
}

function read(markup) {
    return {
        slots: all(markup, /data-setting-slot="([^"]+)"/g),
        derived: all(markup, /data-derived="[^"]+">([^<]+)</g),
        bars: all(markup, /role="img" aria-label="([^"]+)"/g),
        pressed: all(markup, /data-context-preset="[^"]+"\s+aria-pressed="([^"]+)"/g),
        custom: (markup.match(/class="ctx-budget-chip">Custom</g) || []).length,
        details: (markup.match(/<details /g) || []).length,
        summaries: all(markup, /<summary>([^<]+)</g),
        alert: (markup.match(/role="alert"[^>]*data-ctx-preset-error/g) || []).length,
        reserved: all(markup, /<p class="ctx-budget-reserved">([\s\S]*?)<\/p>/g).map(text),
        usable: all(markup, /<p class="ctx-budget-usable">([\s\S]*?)<\/p>/g).map(text),
        legend: all(markup, /<dt>([\s\S]*?)<\/dt>/g).map(text),
        titles: all(markup, /<h4[^>]*>([^<]+)</g),
        problem: all(markup, /<p role="alert" class="ctx-budget-problem">([\s\S]*?)<\/p>/g).map(text),
        modes: (markup.match(/class="ctx-budget-card ctx-budget-mode"/g) || []).length,
    };
}

function button(id) {
    const el = new FakeEl("button");
    el.setAttribute("data-context-preset", id);
    return el;
}

async function settle() {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

async function clickLarge(answer, budget) {
    const root = new FakeEl("div");
    const error = new FakeEl("p");
    error.setAttribute("data-ctx-preset-error", "");
    const buttons = ["small", "medium", "large"].map(button);
    root.append(error, ...buttons);
    const calls = [];
    let applied = 0;
    BossModContextPanel.wire(root, {
        budget,
        api: async (url, init) => {
            calls.push({ url, method: init && init.method });
            return answer;
        },
        onApplied: () => { applied += 1; },
    });
    await buttons[2].dispatchClick();
    await settle();
    return {
        calls,
        applied,
        error: error.textContent,
        disabled: buttons.map((el) => el.disabled === true),
    };
}

async function main() {
    const hostile = {
        ...budgets.measured,
        reserved_by: { ...budgets.measured.reserved_by, agent_name: "<b>Mine</b>" },
        presets: [...budgets.measured.presets, { id: "x\" onclick=\"boom", label: "<b>Mine</b>" }],
    };
    // The plan's example: a measurement three minutes old.
    const threeMinutesAgo = {
        ...budgets.measured,
        reserved_by: {
            ...budgets.measured.reserved_by,
            measured_at: new Date(Date.now() - 3 * 60 * 1000 - 5 * 1000).toISOString(),
        },
    };
    const hostileRecovery = { ...budgets.recovery, problems: ["<b>Mine</b>"] };
    const accepted = await clickLarge({ ok: true, status: 200, json: async () => budgets.medium }, budgets.medium);
    const refused = await clickLarge({
        ok: false,
        status: 500,
        json: async () => ({ detail: "The Large (256K) preset breaks the context rules: Reply must be a whole number from 1 to 99." }),
    }, budgets.medium);
    const noBody = await clickLarge({ ok: false, status: 502, json: async () => { throw new Error("not json"); } }, budgets.medium);
    // From the recovery card: the presets come from the problems payload.
    const recoveryAccepted = await clickLarge({ ok: true, status: 200, json: async () => budgets.medium }, budgets.recovery);
    const hostileMarkup = BossModContextPanel.layout(hostile);
    const hostileRecoveryMarkup = BossModContextPanel.layout(hostileRecovery);
    process.stdout.write(JSON.stringify({
        ok: true,
        medium: read(BossModContextPanel.layout(budgets.medium)),
        custom: read(BossModContextPanel.layout(budgets.custom)),
        measured: read(BossModContextPanel.layout(budgets.measured)),
        threeMinutesAgo: read(BossModContextPanel.layout(threeMinutesAgo)),
        recovery: read(BossModContextPanel.layout(budgets.recovery)),
        hostile: {
            rawQuote: hostileMarkup.includes("x\" onclick"),
            rawTag: hostileMarkup.includes("<b>Mine</b>"),
            escapedTag: hostileMarkup.includes("&lt;b&gt;Mine&lt;/b&gt;"),
        },
        hostileRecovery: {
            rawTag: hostileRecoveryMarkup.includes("<b>Mine</b>"),
            escapedTag: hostileRecoveryMarkup.includes("&lt;b&gt;Mine&lt;/b&gt;"),
        },
        accepted,
        recoveryAccepted,
        refused,
        noBody,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
