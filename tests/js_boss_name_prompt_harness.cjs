/**
 * Node harness: the one-time "What should your team call you?" dialog
 * (shell/boss-name-prompt.js).
 *
 * Checks what opens and what each way out sends, which the source text
 * cannot show: the dialog opens only while the name is unset and the prompt
 * unanswered; Save stores the trimmed name and then the prompted flag; Not
 * now, ✕ and Esc store only the flag; a 400 is shown inline and keeps the
 * dialog open with the draft; a failed or incomplete settings read rejects
 * instead of opening (or not opening) silently.
 *
 * Invoked by tests/test_ui_boss_name_prompt.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// The shared fake's document listeners are no-ops; keep them, so Esc reaches
// the modal's own handler (core/overlay-focus.js) as it would in a browser.
documentStub.listeners = {};
documentStub.addEventListener = (type, fn) => { (documentStub.listeners[type] ||= []).push(fn); };
documentStub.removeEventListener = (type, fn) => {
    documentStub.listeners[type] = (documentStub.listeners[type] || []).filter((item) => item !== fn);
};
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const NAMES = [
    "BossModDom", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays",
    null, "BossModBossNamePrompt",
];
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
paths.forEach((path, index) => {
    const name = NAMES[index];
    // api-client.js installs itself on window rather than declaring a const.
    const tail = name ? `\n;global.${name} = ${name};\n` : "\n";
    eval(`${fs.readFileSync(path, "utf8")}${tail}`);
});
global.BossModApi = global.window.BossModApi;

const settled = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settled(); };

function response(status, body) {
    const text = JSON.stringify(body);
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(JSON.parse(text)),
        text: () => Promise.resolve(text),
    };
}

/** A fake server: the profile rows it reads back, and every PUT it was sent. */
function server({ name = "", prompted = "false", readStatus = 200, rows = null, refuseName = null } = {}) {
    const puts = [];
    async function api(url, init) {
        const method = (init && init.method) || "GET";
        if (method === "GET" && url === "/api/settings?category=profile") {
            if (readStatus !== 200) return response(readStatus, { detail: "boom" });
            return response(200, rows || [
                { key: "boss_name", value: name, category: "profile" },
                { key: "boss_name_prompted", value: prompted, category: "profile" },
            ]);
        }
        if (method === "PUT" && url.startsWith("/api/settings/")) {
            const parsed = new URL(url, "http://local");
            const key = decodeURIComponent(parsed.pathname.split("/").pop());
            const value = parsed.searchParams.get("value");
            puts.push({ key, value, category: parsed.searchParams.get("category") });
            if (key === "boss_name" && refuseName && value === refuseName.value) {
                return response(400, { detail: refuseName.detail });
            }
            return response(200, { key, value, category: "profile" });
        }
        throw new Error(`unexpected request ${method} ${url}`);
    }
    return { api, puts };
}

const panels = () => documentStub.body.querySelectorAll(".modal-panel");
const actionNamed = (panel, label) => panel.querySelectorAll(".modal-action")
    .find((button) => button.textLabel === label);

function keydown(el, key) {
    const event = {
        key,
        target: el,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
    };
    (el.listeners.keydown || []).forEach((fn) => fn(event));
    if (!event.propagationStopped && documentStub.listeners) {
        (documentStub.listeners.keydown || []).slice().forEach((fn) => fn(event));
    }
    return event;
}

(async () => {
    const verdict = {};
    const Prompt = global.BossModBossNamePrompt;

    // ── opensOnlyWhenUnsetAndUnprompted ─────────────────────────────────
    const named = server({ name: "Jordan" });
    const answered = server({ prompted: "true" });
    const skippedNamed = await Prompt.maybeAsk({ api: named.api });
    const skippedAnswered = await Prompt.maybeAsk({ api: answered.api });
    const fresh = server();
    const opened = await Prompt.maybeAsk({ api: fresh.api });
    const panel = panels()[0];
    const input = documentStub.querySelector("#boss-name-input");
    const label = panel && panel.querySelector("label");
    verdict.opensOnlyWhenUnsetAndUnprompted = skippedNamed === false && skippedAnswered === false
        && opened === true && panels().length === 1
        && panel.getAttribute("aria-label") === Prompt.TITLE
        && Boolean(input) && Boolean(label) && label.getAttribute("for") === "boss-name-input"
        && named.puts.length === 0 && answered.puts.length === 0;

    // ── saveStoresTheTrimmedNameThenTheFlag ─────────────────────────────
    input.value = "  Jordan  ";
    await actionNamed(panel, "Save").dispatchClick();
    await drain();
    verdict.saveStoresTheTrimmedNameThenTheFlag = panels().length === 0
        && JSON.stringify(fresh.puts) === JSON.stringify([
            { key: "boss_name", value: "Jordan", category: "profile" },
            { key: "boss_name_prompted", value: "true", category: "profile" },
        ]);

    // ── notNowStoresOnlyTheFlag ─────────────────────────────────────────
    const later = server();
    await Prompt.maybeAsk({ api: later.api });
    await actionNamed(panels()[0], "Not now").dispatchClick();
    await drain();
    verdict.notNowStoresOnlyTheFlag = panels().length === 0
        && JSON.stringify(later.puts) === JSON.stringify([
            { key: "boss_name_prompted", value: "true", category: "profile" },
        ]);

    // ── closeStoresOnlyTheFlag ──────────────────────────────────────────
    const closed = server();
    await Prompt.maybeAsk({ api: closed.api });
    await panels()[0].querySelector(".modal-close").dispatchClick();
    await drain();
    verdict.closeStoresOnlyTheFlag = panels().length === 0
        && JSON.stringify(closed.puts) === JSON.stringify([
            { key: "boss_name_prompted", value: "true", category: "profile" },
        ]);

    // ── aRefusalIsShownInlineAndKeepsTheDraft ───────────────────────────
    const refusing = server({ refuseName: { value: "Ada", detail: 'An agent is already named "Ada". Pick another name.' } });
    await Prompt.maybeAsk({ api: refusing.api });
    const refusedPanel = panels()[0];
    const refusedInput = documentStub.querySelector("#boss-name-input");
    refusedInput.value = "Ada";
    await actionNamed(refusedPanel, "Save").dispatchClick();
    await drain();
    const alert = refusedPanel.querySelector('[role="alert"]');
    const submit = refusedPanel.querySelector("#boss-name-submit");
    verdict.aRefusalIsShownInlineAndKeepsTheDraft = panels().length === 1
        && alert && alert.textContent === 'An agent is already named "Ada". Pick another name.'
        && refusedInput.value === "Ada"
        && !submit.disabled && submit.textContent === "Save"
        && JSON.stringify(refusing.puts) === JSON.stringify([
            { key: "boss_name", value: "Ada", category: "profile" },
        ]);

    // ── escAfterARefusalStoresTheFlag ───────────────────────────────────
    keydown(refusedInput, "Escape");
    await drain();
    verdict.escAfterARefusalStoresTheFlag = panels().length === 0
        && refusing.puts.length === 2
        && refusing.puts[1].key === "boss_name_prompted" && refusing.puts[1].value === "true";

    // ── aFailedReadRejects ──────────────────────────────────────────────
    const broken = server({ readStatus: 500 });
    let readError = null;
    try {
        await Prompt.maybeAsk({ api: broken.api });
    } catch (err) {
        readError = err;
    }
    const partial = server({ rows: [{ key: "boss_name", value: "", category: "profile" }] });
    let rowError = null;
    try {
        await Prompt.maybeAsk({ api: partial.api });
    } catch (err) {
        rowError = err;
    }
    verdict.aFailedReadRejects = Boolean(readError) && /HTTP 500/.test(readError.message)
        && Boolean(rowError) && /boss_name_prompted/.test(rowError.message)
        && panels().length === 0 && broken.puts.length === 0 && partial.puts.length === 0;

    verdict.ok = Object.values(verdict).every(Boolean);
    process.stdout.write(`${JSON.stringify(verdict)}\n`);
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
