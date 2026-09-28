/**
 * Node harness: Settings → System → AI Output renders the compaction knobs,
 * and a change saves through the existing settings PUT. Context Window
 * renders the standing-prefs limits. Threads renders the routing and
 * idle-check settings, with a BossModSwitch for the idle-check flag, and a
 * refused save shows the server's message on its row. System AI lives
 * under AI Connections. Invoked by tests/test_system_ai_compaction_settings.py.
 * Not a browser bundle.
 */
const fs = require("fs");
const { FakeEl, installDom } = require("./js_fake_dom.cjs");

installDom();

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
    throw new Error(`unexpected fetch ${url}`);
};

global.apiFetchOk = async (url, init) => {
    saves.push({ url, method: init && init.method });
    const key = decodeURIComponent(url.split("?")[0].split("/").pop());
    if (world.reject[key]) throw new Error(world.reject[key]);
    return { ok: true };
};

global.BossModOperatorInvalidate = { notifyLocal(surfaces) { invalidations.push(surfaces); } };

// settings-system.js registers a repaint hook on render; the harness drives
// renders directly, so the hook is a no-op.
global.SettingsView = { bindRepaint() {} };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModSwitch = BossModSwitch;\n`);
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModSystemSettingsMeta = BossModSystemSettingsMeta;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.SystemSection = SystemSection;\n`);

function snapshot(root) {
    return root.querySelectorAll(".setting-input").map((control) => {
        const card = control.parent;
        const paragraphs = card.querySelectorAll("p").map((node) => node.textContent.trim());
        const label = card.querySelector("label");
        const options = control.tagName === "SELECT"
            ? control.querySelectorAll("option").map((option) => ({
                value: option.getAttribute("value"),
                label: option.textContent.trim(),
                title: option.getAttribute("title"),
                selected: option.hasAttribute("selected"),
            }))
            : null;
        return {
            key: control.dataset.settingKey,
            category: control.dataset.settingCategory,
            value: control.value,
            label: label ? label.textContent.trim() : "",
            paragraphs,
            options,
        };
    });
}

/** Keys in DOM order across text inputs, selects, and switch mounts. */
function orderedKeys(root) {
    return root.querySelectorAll("[data-setting-key], [data-setting-switch]")
        .map((node) => node.dataset.settingKey || node.dataset.settingSwitch);
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
    const openedOnSimulation = snapshot(root).some((row) => row.key === "tick_interval")
        && snapshot(root).every((row) => row.key !== "system_ai_connection");
    const savesBeforeOpen = saves.length;

    await openAiOutput(root);
    const fresh = snapshot(root);
    const heading = root.querySelector("h3") ? root.querySelector("h3").textContent.trim() : "";
    const intro = root.querySelectorAll("p").map((node) => node.textContent.trim());

    const mode = root.querySelectorAll(".setting-input")
        .find((control) => control.dataset.settingKey === "compaction_mode");
    mode.value = "off";
    await dispatchChange(mode);

    world.settings = world.settings.map((row) => {
        if (row.key === "compaction_mode") return { ...row, value: "custom_mode" };
        return row;
    });
    await openAiOutput(root);
    const degraded = snapshot(root);

    await openCategory(root, "context");
    const context = snapshot(root);
    // `saves` in the output is the AI Output / Context story; Threads saves are reported on their own.
    const outputSaves = saves.slice();

    await openCategory(root, "threads");
    const threadsHeading = root.querySelector("h3") ? root.querySelector("h3").textContent.trim() : "";
    const threadsOrder = orderedKeys(root);
    const threadsInputs = snapshot(root);
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

    process.stdout.write(JSON.stringify({
        ok: true,
        openedOnSimulation,
        savesBeforeOpen,
        heading,
        intro,
        fetches,
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
        invalidations,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
