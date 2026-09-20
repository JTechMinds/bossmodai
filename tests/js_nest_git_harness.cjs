/**
 * Node harness: nest git card token/SSH form + Settings probe-fail stays Off.
 * Invoked by tests/test_nest_git_auth.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.BossModIcons = { paint() {} };
global.BossModFormat = {
    escapeHtml: (value) => String(value ?? ""),
    escapeAttribute: (value) => String(value ?? "").replace(/"/g, "&quot;"),
};

const [domPath, nestPath, consentPath, settingsPath] = process.argv.slice(2);
eval(`${fs.readFileSync(domPath, "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(nestPath, "utf8")}\n;global.BossModNestGitCard = BossModNestGitCard;\n`);
eval(`${fs.readFileSync(consentPath, "utf8")}\n;global.BossModConsentCard = BossModConsentCard;\n`);
eval(`${fs.readFileSync(settingsPath, "utf8")}\n;global.NestGitSection = NestGitSection;\n`);

const { h } = global.BossModDom;
const Card = global.BossModConsentCard;

const LOCK_TITLE = "Jim needs permission to push to GitHub";
const LOCK_BODY = "Your computer’s GitHub login isn’t shared with agents. "
    + "Paste a GitHub access token (a special password from GitHub → Settings → Developer settings), "
    + "or an SSH key if you use those. Saved once under Settings → Nest git. "
    + "Approving a command once doesn’t skip this.";
const ENABLE_LABEL = "Use this computer’s Git login";
const ADD_LABEL = "Add a GitHub access token or SSH key";

function pendingNestCard(id, command) {
    return {
        id,
        kind: "nest_git",
        grant_root: "nest_git_host_enabled",
        status: "pending",
        agent_name: "Jim",
        title: LOCK_TITLE,
        command,
        path: command,
        enable_label: ENABLE_LABEL,
        add_label: ADD_LABEL,
        enable_hint: "Saved once under Settings → Nest git. Approving a command once doesn’t skip this.",
        body: LOCK_BODY,
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

const bounceCard = pendingNestCard("nest-bounce", "git push origin main");
bounceCard.error = "GitHub rejected this token. "
    + "Or this token may not have access to this repo — grant it under the token’s repository access. "
    + "A fine-grained token belongs to one Resource owner — you, or one organization, not both. "
    + "For an organization repo, Resource owner = the org that owns the repo.";
const bounceEl = paintCard(list, bounceCard);
const bounceStatus = bounceEl.querySelector(".hpc-status");
const bounceText = bounceStatus ? bounceStatus.textContent : "";
const cardShowsAuthBounce = Boolean(bounceStatus)
    && bounceText.includes("GitHub rejected this token")
    && bounceText.includes("this token may not have access to this repo")
    && bounceText.includes("grant it under the token’s repository access.")
    && bounceText.includes("Resource owner = the org that owns the repo");
if (!cardShowsAuthBounce) {
    throw new Error(`auth bounce error missing on card: ${JSON.stringify(bounceText)}`);
}

const origin = pendingNestCard("nest-a", "git push origin main");
const sibling = pendingNestCard("nest-b", "git fetch");
const originEl = paintCard(list, origin);
const siblingEl = paintCard(list, sibling);
const titleEl = originEl.querySelector(".hpc-title");
const reasonEl = originEl.querySelector(".hpc-reason");
if (!titleEl || titleEl.textContent !== LOCK_TITLE) {
    throw new Error(`card title was ${JSON.stringify(titleEl && titleEl.textContent)}`);
}
if (!reasonEl || reasonEl.textContent !== LOCK_BODY) {
    throw new Error(`card body was ${JSON.stringify(reasonEl && reasonEl.textContent)}`);
}
const labels = actionButtons(originEl).map((btn) => btn.textContent);
const cardShowsEnableAndAdd = labels.includes(ENABLE_LABEL)
    && labels.includes(ADD_LABEL);
if (!cardShowsEnableAndAdd) throw new Error(`card buttons were ${JSON.stringify(labels)}`);
if (labels.includes("Enable host git for nest") || labels.includes("Add PAT/SSH")) {
    throw new Error(`old jargon buttons still visible: ${JSON.stringify(labels)}`);
}

const credApi = async () => ({ ok: true, async json() { return origin; } });
originEl.replaceChildren();
Card.renderHostPathConsentCard(originEl, origin, credApi);
const addBtn = actionButtons(originEl).find((btn) => btn.textContent === ADD_LABEL);
if (!addBtn) throw new Error("Add token/SSH button missing");
await addBtn.dispatchClick();
const tokenField = originEl.querySelector("input");
const sshField = originEl.querySelector("textarea");
const formLabels = actionButtons(originEl).map((btn) => btn.textContent);
const cardShowsSaveAndSettings = Boolean(tokenField)
    && tokenField.placeholder === "GitHub access token"
    && Boolean(sshField)
    && sshField.placeholder === "SSH key (optional)"
    && formLabels.includes("Save")
    && formLabels.includes("Open Nest git settings");
if (!cardShowsSaveAndSettings) {
    throw new Error(`credentials form was placeholders=${Boolean(tokenField)}/${Boolean(sshField)} buttons=${JSON.stringify(formLabels)}`);
}
if (formLabels.includes("Save to Settings") || formLabels.includes("Settings → Nest git")) {
    throw new Error(`old jargon form buttons still visible: ${JSON.stringify(formLabels)}`);
}

const enabled = {
    ...origin,
    status: "enabled",
    decision_note: "GitHub permission saved under Settings → Nest git.",
};
const apiEnable = async (url) => {
    if (!String(url).includes("/api/nest-git/") || !String(url).endsWith("/enable")) {
        throw new Error(`unexpected enable URL ${url}`);
    }
    return { ok: true, async json() { return enabled; } };
};
originEl.replaceChildren();
Card.renderHostPathConsentCard(originEl, origin, apiEnable);
const enableBtn = actionButtons(originEl).find((btn) => btn.textContent === ENABLE_LABEL);
if (!enableBtn) throw new Error("Enable button missing");
await enableBtn.dispatchClick();
const enableCollapsesSibling = originEl.classList.contains("is-resolved")
    && siblingEl.classList.contains("is-resolved")
    && actionButtons(siblingEl).length === 0;
if (!enableCollapsesSibling) throw new Error("Enable must collapse sibling nest_git cards");

const settingsRoot = h("div", { id: "settings-content" });
Object.defineProperty(settingsRoot, "innerHTML", {
    configurable: true,
    get() { return this._html || ""; },
    set(html) {
        this._html = String(html);
        this.replaceChildren();
        for (const match of String(html).matchAll(/id="([^"]+)"/g)) {
            const id = match[1];
            const before = String(html).slice(0, match.index);
            const lastTextarea = before.lastIndexOf("<textarea");
            const lastInput = before.lastIndexOf("<input");
            const lastButton = before.lastIndexOf("<button");
            const lastDiv = before.lastIndexOf("<div");
            const tag = lastTextarea > lastInput && lastTextarea > lastButton && lastTextarea > lastDiv
                ? "textarea"
                : lastInput > lastButton && lastInput > lastDiv
                    ? "input"
                    : lastButton > lastDiv
                        ? "button"
                        : "div";
            const node = h(tag, { id });
            if (id === "btn-toggle-nest-git") {
                node.setAttribute("role", "switch");
                node.setAttribute("aria-checked", /aria-checked="true"/.test(html) ? "true" : "false");
            }
            if (id === "nest-git-pat") node.setAttribute("data-focus", "pat");
            this.append(node);
        }
    },
});
documentStub.body.append(settingsRoot);
let hostEnabled = false;
const secret = "ghp_harness-must-not-linger";
global.apiFetch = async (url, init) => {
    const path = String(url);
    if (path === "/api/nest-git/status") {
        return {
            ok: true,
            async json() {
                return {
                    host_enabled: hostEnabled,
                    has_pat: false,
                    pat_last4: null,
                    has_ssh: false,
                    ssh_last4: null,
                    probe_ok: false,
                    probe_via: null,
                    probe_why: "Host git is not visible to Shell",
                    how_to: "Configure a git credential helper the Shell can see.",
                };
            },
        };
    }
    if (path.includes("nest_git_host_enabled") && init && init.method === "PUT") {
        return {
            ok: false,
            async json() { return { detail: "Host git is not visible to Shell. Configure a helper." }; },
        };
    }
    if (path === "/api/nest-git/credentials") {
        const body = JSON.parse(init.body || "{}");
        if (body.pat && body.pat.includes("linger")) {
            return { ok: true, async json() { return { has_pat: true, pat_last4: "nger" }; } };
        }
        return { ok: true, async json() { return { has_pat: true }; } };
    }
    throw new Error(`unexpected settings URL ${path}`);
};

await NestGitSection.render(settingsRoot);
const settingsHtml = settingsRoot.innerHTML;
const settingsShowsBeginnerCopy = settingsHtml.includes("GitHub access token")
    && settingsHtml.includes("SSH key (optional)")
    && settingsHtml.includes("Use this computer’s Git login")
    && settingsHtml.includes("Your computer’s GitHub login isn’t shared with agents.")
    && !settingsHtml.includes("Enable host git")
    && !/>PAT</.test(settingsHtml)
    && !settingsHtml.includes("SSH private key")
    && !settingsHtml.includes("Save PAT")
    && !settingsHtml.includes("Save SSH");
if (!settingsShowsBeginnerCopy) {
    throw new Error(`Settings copy still jargon or missing LOCK fields: ${settingsHtml.slice(0, 400)}`);
}
const toggle = settingsRoot.querySelector("#btn-toggle-nest-git");
if (!toggle) throw new Error("host Enable toggle missing");
await toggle.dispatchClick();
const stillOff = toggle.getAttribute("aria-checked") === "false";
if (!stillOff) throw new Error("probe fail must not flip Enable On");

const patInput = settingsRoot.querySelector("#nest-git-pat");
patInput.value = secret;
const savePat = settingsRoot.querySelector("#nest-git-pat-save");
await savePat.dispatchClick();
const after = settingsRoot.querySelector("#nest-git-pat");
const patNotLeftInDom = !settingsRoot.innerHTML.includes(secret)
    && (!after || after.value === "");
if (!patNotLeftInDom) throw new Error("token lingered in the Settings DOM");

process.stdout.write(JSON.stringify({
    ok: true,
    cardShowsEnableAndAdd: true,
    cardShowsLockCopy: true,
    cardShowsSaveAndSettings: true,
    enableCollapsesSibling: true,
    probeFailLeavesToggleOff: true,
    patNotLeftInDom: true,
    settingsShowsBeginnerCopy: true,
    cardShowsAuthBounce: true,
}));
})().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
