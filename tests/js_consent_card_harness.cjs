/**
 * Node harness: Shell Executor Enable collapses pending cards; Deny does not.
 * Invoked by tests/test_shell_executor_consent.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.BossModIcons = { paint() {} };

const [domPath, consentPath] = process.argv.slice(2);
eval(`${fs.readFileSync(domPath, "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(consentPath, "utf8")}\n;global.BossModConsentCard = BossModConsentCard;\n`);

const { h } = global.BossModDom;
const Card = global.BossModConsentCard;

function pendingShellCard(id, command) {
    return {
        id,
        kind: "shell_executor",
        grant_root: "cli_shell_enabled",
        status: "pending",
        title: "Enable Shell Executor?",
        command,
        path: command,
        enable_label: "Turn on Shell Executor (company-wide)",
        deny_label: "Deny — Shell Executor stays off",
        enable_hint: "same as Settings. CLI policy still applies after.",
        body: "Turns on Shell Executor for the company.",
    };
}

function paintCard(scope, card) {
    const wrapper = h("div", {
        class: "msg host-path-consent-card",
        id: `host-path-consent-${card.id}`,
    });
    Card.renderHostPathConsentCard(wrapper, card, async () => ({ ok: true, async json() { return card; } }));
    scope.append(wrapper);
    return wrapper;
}

function actionButtons(el) {
    return el.querySelectorAll(".hpc-action");
}

(async () => {
const transcript = h("div", { class: "transcript", "data-transcript": true });
const list = h("div", { class: "transcript-list" });
transcript.append(list);
documentStub.body.append(transcript);

const origin = pendingShellCard("shell-a", "git add -A");
const sibling = pendingShellCard("shell-b", "pytest -q");
const originEl = paintCard(list, origin);
const siblingEl = paintCard(list, sibling);

if (actionButtons(originEl).length !== 2) {
    throw new Error("origin pending card should show Enable/Deny");
}
if (actionButtons(siblingEl).length !== 2) {
    throw new Error("sibling pending card should show Enable/Deny");
}

const enabled = {
    ...origin,
    status: "enabled",
    decision_note: "Shell Executor on (company-wide). CLI policy still applies.",
};

const apiEnable = async (url) => {
    if (!String(url).includes("/enable")) {
        throw new Error(`unexpected enable URL ${url}`);
    }
    return { ok: true, async json() { return enabled; } };
};
originEl.replaceChildren();
Card.renderHostPathConsentCard(originEl, origin, apiEnable);
const enableBtn = actionButtons(originEl).find((btn) => /Turn on Shell Executor/.test(btn.textContent));
if (!enableBtn) throw new Error("Enable button missing");
await enableBtn.dispatchClick();

const originResolved = originEl.classList.contains("is-resolved")
    && actionButtons(originEl).length === 0
    && /Shell Executor on \(company-wide\)/.test(originEl.textContent);
const siblingCollapsed = siblingEl.classList.contains("is-resolved")
    && actionButtons(siblingEl).length === 0
    && /Shell Executor on \(company-wide\)/.test(siblingEl.textContent);
if (!originResolved) throw new Error("Enable must resolve the origin card");
if (!siblingCollapsed) throw new Error("Enable must collapse sibling shell_executor cards");

const denyTranscript = h("div", { class: "transcript", "data-transcript": true });
const denyList = h("div", { class: "transcript-list" });
denyTranscript.append(denyList);
documentStub.body.append(denyTranscript);
const denyOrigin = pendingShellCard("shell-deny-a", "git add -A");
const denySibling = pendingShellCard("shell-deny-b", "pytest -q");
const denyOriginEl = paintCard(denyList, denyOrigin);
const denySiblingEl = paintCard(denyList, denySibling);
const denied = {
    ...denyOrigin,
    status: "denied",
    decision_note: "Shell Executor stays off. Validate-on-clone was denied.",
};
const apiDeny = async (url) => {
    if (!String(url).includes("/deny")) {
        throw new Error(`unexpected deny URL ${url}`);
    }
    return { ok: true, async json() { return denied; } };
};
denyOriginEl.replaceChildren();
Card.renderHostPathConsentCard(denyOriginEl, denyOrigin, apiDeny);
const denyBtn = actionButtons(denyOriginEl).find((btn) => /Deny/.test(btn.textContent));
if (!denyBtn) throw new Error("Deny button missing");
await denyBtn.dispatchClick();

const denyOriginResolved = denyOriginEl.classList.contains("is-resolved")
    && actionButtons(denyOriginEl).length === 0;
const denySiblingUntouched = !denySiblingEl.classList.contains("is-resolved")
    && actionButtons(denySiblingEl).length === 2;
if (!denyOriginResolved) throw new Error("Deny must resolve the origin card");
if (!denySiblingUntouched) throw new Error("Deny must leave sibling pending cards with buttons");

const broadcastTranscript = h("div", { class: "transcript", "data-transcript": true });
const broadcastList = h("div", { class: "transcript-list" });
broadcastTranscript.append(broadcastList);
documentStub.body.append(broadcastTranscript);
const leftover = pendingShellCard("shell-left", "git add -A");
const leftoverEl = paintCard(broadcastList, leftover);
Card.collapseGrantedConsentCards(enabled, broadcastTranscript);
const leftoverCollapsed = leftoverEl.classList.contains("is-resolved")
    && actionButtons(leftoverEl).length === 0;
if (!leftoverCollapsed) {
    throw new Error("activity Enable must collapse pending shell_executor cards without a click");
}

process.stdout.write(JSON.stringify({
    ok: true,
    enableCollapsesSibling: true,
    denyLeavesSiblingPending: true,
    activityCollapsesPending: true,
}));
})().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});

