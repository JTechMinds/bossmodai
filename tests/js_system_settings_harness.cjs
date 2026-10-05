/**
 * Node harness: Settings → System → AI Output renders the compaction knobs,
 * and a change saves through the existing settings PUT. Context Window
 * renders the standing-prefs limits. Threads renders the routing and
 * idle-check settings, with a BossModSwitch for the idle-check flag, and a
 * refused save shows the server's message on its row. System AI lives
 * under AI Connections. Advanced renders the Global auto-approve switch,
 * which asks before it turns on and never before it turns off. Invoked by
 * tests/test_system_ai_compaction_settings.py. Not a browser bundle.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

const documentStub = installDom();
installIconsStub();

const VOID = new Set(["INPUT", "BR", "HR", "IMG"]);

function decode(text) {
    return String(text).replace(/&(?:amp|lt|gt|quot);/g, (entity) => ({
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
    }[entity]));
}

function parseAttrs(raw) {
    const attrs = {};
    const re = /([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g;
    let match;
    while ((match = re.exec(raw))) {
        if (match[2] !== undefined) attrs[match[1]] = decode(match[2]);
        else if (match[3] !== undefined) attrs[match[1]] = decode(match[3]);
        else attrs[match[1]] = "";
    }
    return attrs;
}

function parseFragment(html) {
    const root = new FakeEl("div");
    const stack = [root];
    const re = /<!--[\s\S]*?-->|<\/([A-Za-z][A-Za-z0-9]*)\s*>|<([A-Za-z][A-Za-z0-9]*)\b([^>]*)>/g;
    let last = 0;
    let match;
    while ((match = re.exec(html))) {
        const text = html.slice(last, match.index);
        if (text) stack[stack.length - 1].append(decode(text));
        last = re.lastIndex;
        if (match[0].startsWith("<!--")) continue;
        if (match[1]) {
            const tag = match[1].toUpperCase();
            while (stack.length > 1 && stack[stack.length - 1].tagName !== tag) stack.pop();
            if (stack.length > 1) stack.pop();
            continue;
        }
        const el = new FakeEl(match[2]);
        for (const [name, value] of Object.entries(parseAttrs(match[3] || ""))) {
            el.setAttribute(name, value);
        }
        if (el.tagName === "INPUT") el.value = el.getAttribute("value") || "";
        stack[stack.length - 1].append(el);
        const selfClosed = /\/\s*$/.test(match[3] || "");
        if (!VOID.has(el.tagName) && !selfClosed) stack.push(el);
    }
    const tail = html.slice(last);
    if (tail) stack[stack.length - 1].append(decode(tail));
    for (const select of root.querySelectorAll("select")) {
        const options = select.querySelectorAll("option");
        const selected = options.find((option) => option.hasAttribute("selected")) || options[0];
        select.value = selected ? (selected.getAttribute("value") ?? "") : "";
    }
    return root.children;
}

Object.defineProperty(FakeEl.prototype, "innerHTML", {
    configurable: true,
    get() {
        if (this.children.length) return "";
        return String(this._text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    },
    set(value) {
        this.replaceChildren(...parseFragment(String(value)));
    },
});

const fetches = [];
const saves = [];
const world = {
    settings: [],
    // Setting key → the 400 detail the fake PUT refuses it with.
    reject: {},
};
const invalidations = [];

global.apiFetch = async (url) => {
    fetches.push(url);
    if (url === "/api/settings") {
        return { ok: true, status: 200, json: async () => world.settings };
    }
    if (url === "/api/settings?category=advanced") {
        return { ok: true, status: 200, json: async () => world.advanced };
    }
    if (url === "/api/settings/desktop-open-folder-options") {
        return { ok: true, status: 200, json: async () => ({ current: null, options: [] }) };
    }
    throw new Error(`unexpected fetch ${url}`);
};

global.apiFetchOk = async (url, init) => {
    saves.push({ url, method: init && init.method });
    const key = decodeURIComponent(url.split("?")[0].split("/").pop());
    if (world.reject[key]) throw new Error(world.reject[key]);
    return { ok: true };
};

global.BossModOperatorInvalidate = { notifyLocal(surfaces) { invalidations.push(surfaces); } };

// settings-advanced.js asks through window.confirm before Global auto-approve
// turns on. Each answer is scripted; every question is kept.
const confirms = [];
let confirmAnswer = false;
global.confirm = (text) => { confirms.push(text); return confirmAnswer; };

// settings-system.js registers a repaint hook on render; the harness drives
// renders directly, so the hook is a no-op.
global.SettingsView = { bindRepaint() {} };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModSwitch = BossModSwitch;\n`);
// A select-type setting is the app's dropdown (core/menu-select.js) and its
// panel (core/menu.js over core/overlays.js), passed after the six above.
["BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays",
    "BossModMenu", "BossModMenuSelect"].forEach((name, index) => {
    eval(`${fs.readFileSync(process.argv[8 + index], "utf8")}\n;global.${name} = ${name};\n`);
});
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModSystemSettingsMeta = BossModSystemSettingsMeta;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.SystemSection = SystemSection;\n`);
eval(`${fs.readFileSync(process.argv[7], "utf8")}\n;global.AdvancedSystemSection = AdvancedSystemSection;\n`);

/**
 * A select-type setting's dropdown, read the way an operator reads it: the
 * rows its panel lists, which one is marked, and the value the control holds.
 * The panel is opened to read the rows and closed again.
 */
async function dropdownState(mount) {
    const trigger = mount.querySelector(".menu-select-trigger");
    const menu = BossModMenuSelect.instanceFor(trigger);
    await trigger.dispatchClick();
    const rows = mount.querySelector(".menu").querySelectorAll(".menu-select-option");
    const values = menu.getOptions().map((option) => option.value);
    const options = rows.map((row, index) => ({
        value: values[index],
        label: row.querySelector(".menu-select-label").textContent.trim(),
        title: row.getAttribute("title"),
        selected: row.getAttribute("aria-pressed") === "true",
    }));
    await trigger.dispatchClick();
    return { value: menu.getValue(), options };
}

/** Pick a dropdown's row by its label, as an operator clicks it. */
async function pickDropdown(mount, label) {
    await mount.querySelector(".menu-select-trigger").dispatchClick();
    const row = mount.querySelector(".menu").querySelectorAll(".menu-select-option")
        .find((node) => node.querySelector(".menu-select-label").textContent.trim() === label);
    if (!row) throw new Error(`the dropdown offers no "${label}" row`);
    await row.dispatchClick();
    await settle();
}

async function snapshot(root) {
    const rows = [];
    for (const control of root.querySelectorAll(".setting-input, [data-setting-select]")) {
        const card = control.parent;
        const paragraphs = card.querySelectorAll("p").map((node) => node.textContent.trim());
        const label = card.querySelector("label");
        if (control.dataset.settingSelect) {
            const state = await dropdownState(control);
            rows.push({
                key: control.dataset.settingSelect,
                category: control.dataset.settingCategory,
                value: state.value,
                label: label ? label.textContent.trim() : "",
                paragraphs,
                options: state.options,
            });
            continue;
        }
        rows.push({
            key: control.dataset.settingKey,
            category: control.dataset.settingCategory,
            value: control.value,
            label: label ? label.textContent.trim() : "",
            paragraphs,
            options: null,
        });
    }
    return rows;
}

/** Keys in DOM order across text inputs, dropdown mounts, and switch mounts. */
function orderedKeys(root) {
    return root.querySelectorAll("[data-setting-key], [data-setting-select], [data-setting-switch]")
        .map((node) => node.dataset.settingKey || node.dataset.settingSelect || node.dataset.settingSwitch);
}

function errorText(root, key) {
    const line = root.querySelectorAll("[data-setting-error]")
        .find((node) => node.dataset.settingError === key);
    return line ? { text: line.textContent.trim(), role: line.getAttribute("role") } : null;
}

function idleSwitch(root) {
    const mount = root.querySelectorAll("[data-setting-switch]")
        .find((node) => node.dataset.settingSwitch === "channel_idle_check_enabled");
    const button = mount ? mount.querySelector("[role=\"switch\"]") : null;
    const card = mount ? mount.parent : null;
    return {
        button,
        role: button ? button.getAttribute("role") : null,
        checked: button ? button.getAttribute("aria-checked") : null,
        name: button ? button.textContent.trim() : "",
        cardLabels: card ? card.querySelectorAll("label").length : -1,
        paragraphs: card ? card.querySelectorAll("p").map((node) => node.textContent.trim()) : [],
    };
}

async function settle() {
    for (let i = 0; i < 8; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

async function openCategory(root, key) {
    const tab = root.querySelectorAll("[data-system-category]")
        .find((button) => button.dataset.systemCategory === key);
    if (!tab) throw new Error(`${key} tab missing`);
    await tab.dispatchClick();
    await settle();
}

async function openAiOutput(root) {
    await openCategory(root, "llm");
}

async function dispatchChange(control) {
    const event = { target: control };
    for (const fn of [...(control.listeners.change || [])]) await fn(event);
}

function setting(key, value, category) {
    return { key, value, category, updated_at: "2026-01-01T00:00:00Z" };
}

async function main() {
    world.settings = [
        setting("tick_interval", "0.25", "simulation"),
        setting("decision_repair_attempts", "6", "llm"),
        setting("max_concurrent_agent_turns", "2", "llm"),
        setting("system_ai_connection", "", "llm"),
        setting("system_ai_max_tokens", "6144", "llm"),
        setting("system_ai_timeout_seconds", "180", "llm"),
        setting("compaction_mode", "pressure_only", "llm"),
        setting("compaction_task_budget_headroom_percent", "25", "llm"),
        setting("compaction_chat_budget_headroom_percent", "35", "llm"),
        setting("compaction_min_turns_between_runs", "8", "llm"),
        setting("compaction_cooldown_minutes", "10", "llm"),
        setting("max_concurrent_llm_calls", "5", "llm"),
        setting("context_recent_work_artifacts", "5", "context"),
        setting("context_recent_completed_tasks", "3", "context"),
        setting("standing_prefs_line_max_chars", "400", "context"),
        setting("standing_prefs_section_max_chars", "4000", "context"),
        setting("channel_response_round_cap", "64", "llm"),
        setting("channel_router_transcript_messages", "10", "llm"),
        setting("channel_idle_check_enabled", "true", "llm"),
        setting("channel_idle_check_delay_seconds", "45", "llm"),
        setting("channel_idle_check_interval_seconds", "5", "llm"),
        setting("channel_idle_check_max_age_minutes", "30", "llm"),
        setting("channel_idle_check_max_wakes", "2", "llm"),
        setting("channel_idle_check_max_attempts", "3", "llm"),
    ];
    const root = new FakeEl("div");
    await SystemSection.render(root);
    const openedOnSimulation = (await snapshot(root)).some((row) => row.key === "tick_interval")
        && (await snapshot(root)).every((row) => row.key !== "system_ai_connection");
    const savesBeforeOpen = saves.length;

    await openAiOutput(root);
    const fresh = await snapshot(root);
    const heading = root.querySelector("h3") ? root.querySelector("h3").textContent.trim() : "";
    const intro = root.querySelectorAll("p").map((node) => node.textContent.trim());

    const mode = root.querySelectorAll("[data-setting-select]")
        .find((control) => control.dataset.settingSelect === "compaction_mode");
    await pickDropdown(mode, "Off");

    world.settings = world.settings.map((row) => {
        if (row.key === "compaction_mode") return { ...row, value: "custom_mode" };
        return row;
    });
    await openAiOutput(root);
    const degraded = await snapshot(root);

    await openCategory(root, "context");
    const context = await snapshot(root);
    // `saves` in the output is the AI Output / Context story; Threads saves are reported on their own.
    const outputSaves = saves.slice();

    await openCategory(root, "threads");
    const threadsHeading = root.querySelector("h3") ? root.querySelector("h3").textContent.trim() : "";
    const threadsOrder = orderedKeys(root);
    const threadsInputs = await snapshot(root);
    const idleBefore = idleSwitch(root);
    const savesBeforeToggle = saves.length;
    await idleBefore.button.dispatchClick();
    await settle();
    const toggleSaves = saves.slice(savesBeforeToggle);
    const idleAfterToggle = idleSwitch(root);

    // A refused text save shows the server's message on its row.
    const detail = "Idle check delay must be a whole number of at least 1.";
    world.reject.channel_idle_check_delay_seconds = detail;
    const delay = root.querySelectorAll(".setting-input")
        .find((control) => control.dataset.settingKey === "channel_idle_check_delay_seconds");
    delay.value = "0";
    await dispatchChange(delay);
    const delayError = errorText(root, "channel_idle_check_delay_seconds");
    const otherError = errorText(root, "channel_idle_check_max_wakes");

    // A refused switch save puts the pill back.
    world.reject.channel_idle_check_enabled = "Idle check must be true or false.";
    const beforeRefused = idleSwitch(root).checked;
    await idleSwitch(root).button.dispatchClick();
    await settle();
    const idleAfterRefused = idleSwitch(root);
    const idleError = errorText(root, "channel_idle_check_enabled");

    // The next accepted save clears the row's error line.
    delete world.reject.channel_idle_check_delay_seconds;
    delay.value = "60";
    await dispatchChange(delay);
    const delayErrorAfterFix = errorText(root, "channel_idle_check_delay_seconds");
    const systemFetches = fetches.slice();
    const systemInvalidations = invalidations.slice();

    // ─── Advanced: Global auto-approve ───
    // The section wires its buttons through document.getElementById, so it
    // renders inside the document body.
    world.advanced = [
        setting("diagnostics_enabled", "false", "advanced"),
        setting("diagnostics_retention_limit", "5000", "advanced"),
        setting("diagnostics_retention_days", "7", "advanced"),
        setting("trigger_retention_days", "7", "advanced"),
        // activity_log_retention_days left out: its field must say so.
        setting("cli_max_read_lines", "200", "advanced"),
        setting("desktop_open_folder_handler", "", "advanced"),
        setting("cli_auto_approve_global", "false", "advanced"),
    ];
    const advancedRoot = new FakeEl("div");
    documentStub.body.append(advancedRoot);
    await AdvancedSystemSection.render(advancedRoot);
    const globalSwitch = () => {
        const mount = advancedRoot.querySelectorAll("[data-setting-switch]")
            .find((node) => node.dataset.settingSwitch === "cli_auto_approve_global");
        return mount ? mount.querySelector("[role=\"switch\"]") : null;
    };
    const globalStep = async (answer) => {
        confirmAnswer = answer;
        const asked = confirms.length;
        const saved = saves.length;
        const notified = invalidations.length;
        await globalSwitch().dispatchClick();
        await settle();
        return {
            asked: confirms.slice(asked),
            saves: saves.slice(saved),
            invalidations: invalidations.slice(notified),
            checked: globalSwitch().getAttribute("aria-checked"),
        };
    };
    const globalBefore = globalSwitch()
        ? { name: globalSwitch().textContent.trim(), checked: globalSwitch().getAttribute("aria-checked") }
        : null;
    const declined = await globalStep(false);
    const accepted = await globalStep(true);
    const turnedOff = await globalStep(true);
    world.reject.cli_auto_approve_global = "Global auto-approve must be true or false.";
    const refused = await globalStep(true);
    refused.error = errorText(advancedRoot, "cli_auto_approve_global");

    // ─── Advanced: Retention ───
    // Each entry is typed into its field and committed; only a whole number
    // in the field's range is saved, anything else is reported under it.
    const retentionStep = async (id, typed) => {
        const input = documentStub.getElementById(id);
        const saved = saves.length;
        input.value = typed;
        await dispatchChange(input);
        await settle();
        return {
            saves: saves.slice(saved).map((save) => save.url),
            error: documentStub.getElementById(`${id}-error`).textContent.trim(),
            invalid: input.getAttribute("aria-invalid"),
        };
    };
    const retentionLabels = advancedRoot.querySelectorAll("label")
        .map((label) => label.textContent.trim())
        .filter((text) => /Retention/.test(text));
    const retention = {
        labels: retentionLabels,
        values: ["diag-retention-limit", "diag-retention-days", "trigger-retention-days"]
            .map((id) => documentStub.getElementById(id).value),
        zeroDays: await retentionStep("diag-retention-days", "0"),
        fraction: await retentionStep("trigger-retention-days", "1.5"),
        text: await retentionStep("trigger-retention-days", "abc"),
        belowRowMin: await retentionStep("diag-retention-limit", "50"),
        goodDays: await retentionStep("diag-retention-days", "14"),
        goodRows: await retentionStep("diag-retention-limit", "8000"),
        missing: {
            disabled: documentStub.getElementById("activity-log-retention-days").disabled,
            error: documentStub.getElementById("activity-log-retention-days-error").textContent.trim(),
        },
    };

    process.stdout.write(JSON.stringify({
        ok: true,
        openedOnSimulation,
        savesBeforeOpen,
        heading,
        intro,
        fetches: systemFetches,
        saves: outputSaves,
        fresh,
        degraded,
        context,
        threadsHeading,
        threadsOrder,
        threadsInputs,
        idle: {
            before: { role: idleBefore.role, checked: idleBefore.checked, name: idleBefore.name,
                cardLabels: idleBefore.cardLabels, paragraphs: idleBefore.paragraphs },
            afterToggle: idleAfterToggle.checked,
            toggleSaves,
            beforeRefused,
            afterRefused: idleAfterRefused.checked,
            error: idleError,
        },
        delayError,
        otherError,
        delayErrorAfterFix,
        invalidations: systemInvalidations,
        globalAutoApprove: { before: globalBefore, declined, accepted, turnedOff, refused },
        retention,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
