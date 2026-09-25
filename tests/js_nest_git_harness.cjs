/**
 * Node harness: nest git card token/SSH form + Settings probe-fail stays Off.
 * Invoked by tests/test_nest_git_auth.py. Not a browser bundle.
 */
const fs = require("fs");
const path = require("path");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.BossModIcons = { paint() {} };
global.BossModFormat = {
    escapeHtml: (value) => String(value ?? ""),
    escapeAttribute: (value) => String(value ?? "").replace(/"/g, "&quot;"),
};

const [domPath, nestPath, consentPath, settingsPath] = process.argv.slice(2);
const secretPath = path.join(path.dirname(nestPath), "secret-field.js");
const credentialFormPath = path.join(path.dirname(nestPath), "nest-git-credential-form.js");
eval(`${fs.readFileSync(domPath, "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(secretPath, "utf8")}\n;global.BossModSecretField = BossModSecretField;\n`);
eval(`${fs.readFileSync(credentialFormPath, "utf8")}\n;global.BossModNestGitCredentialForm = BossModNestGitCredentialForm;\n`);
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
    + "Personal and organization repos cannot share one fine-grained token. "
    + "For an organization repo, Resource owner = the org that owns the repo → select that repo → "
    + "Contents Read and write. Need one key for everything? Use a classic repo token. "
    + "If the organization uses SAML, open Configure SSO on the token.";
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

const pickCard = pendingNestCard("nest-pick", "git push origin main");
pickCard.credentials = [{ id: "acme", label: "Acme", match: "github.com/Acme/*" }];
pickCard.error = "No Nest git credential matches this remote. Add a credential for this remote, or pick which saved one to use.";
const pickEl = paintCard(list, pickCard);
const pickLabels = actionButtons(pickEl).map((btn) => btn.textContent);
const cardShowsPickSaved = pickLabels.includes("Use Acme");
if (!cardShowsPickSaved) {
    throw new Error(`pick buttons missing: ${JSON.stringify(pickLabels)}`);
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
const tokenField = Array.from(originEl.querySelectorAll("input")).find((node) => node.placeholder === "GitHub access token");
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
let enableInit = null;
const apiEnable = async (url, init) => {
    enableInit = init || {};
    if (!String(url).includes("/api/nest-git/") || !String(url).endsWith("/enable")) {
        throw new Error(`unexpected enable URL ${url}`);
    }
    const payload = JSON.stringify(enabled);
    return { ok: true, async text() { return payload; }, async json() { return enabled; } };
};
originEl.replaceChildren();
Card.renderHostPathConsentCard(originEl, origin, apiEnable);
const enableBtn = actionButtons(originEl).find((btn) => btn.textContent === ENABLE_LABEL);
if (!enableBtn) throw new Error("Enable button missing");
await enableBtn.dispatchClick();
const enablePostsBody = Boolean(enableInit)
    && enableInit.method === "POST"
    && String(enableInit.body || "") === "{}";
if (!enablePostsBody) {
    throw new Error(`enable must POST an empty JSON body, got ${JSON.stringify(enableInit)}`);
}
const enableCollapsesSibling = originEl.classList.contains("is-resolved")
    && siblingEl.classList.contains("is-resolved")
    && actionButtons(siblingEl).length === 0;
if (!enableCollapsesSibling) throw new Error("Enable must collapse sibling nest_git cards");

const staleEl = paintCard(list, pendingNestCard("nest-stale", "git push origin main"));
const api422 = async () => ({
    ok: false,
    status: 422,
    async text() {
        return JSON.stringify({
            detail: [{ type: "missing", loc: ["body"], msg: "Field required", input: null }],
        });
    },
});
staleEl.replaceChildren();
Card.renderHostPathConsentCard(staleEl, pendingNestCard("nest-stale", "git push origin main"), api422);
const staleEnable = actionButtons(staleEl).find((btn) => btn.textContent === ENABLE_LABEL);
if (!staleEnable) throw new Error("stale Enable button missing");
await staleEnable.dispatchClick();
const staleLabels = actionButtons(staleEl).map((btn) => btn.textContent);
const staleText = staleEl.textContent || "";
const schemaMismatchDismisses = staleLabels.length === 1
    && staleLabels[0] === "Dismiss"
    && !staleText.includes("Field required")
    && !staleText.includes("\"detail\"");
if (!schemaMismatchDismisses) {
    throw new Error(`schema mismatch must collapse to Dismiss, got labels=${JSON.stringify(staleLabels)} text=${JSON.stringify(staleText)}`);
}

const settingsRoot = h("div", { id: "settings-content" });
const VOID_TAGS = new Set(["input"]);
const TREE_TAGS = /<\/?(input|textarea|button|div)\b/g;

function attrsFrom(raw) {
    const attrs = {};
    const attrRe = /([:@\w-]+)(?:="([^"]*)")?/g;
    let attr;
    while ((attr = attrRe.exec(raw || ""))) {
        attrs[attr[1]] = attr[2] === undefined ? "" : attr[2];
    }
    return attrs;
}

Object.defineProperty(settingsRoot, "innerHTML", {
    configurable: true,
    get() { return this._html || ""; },
    set(html) {
        const source = String(html);
        this._html = source;
        this.replaceChildren();
        const stack = [this];
        TREE_TAGS.lastIndex = 0;
        let match;
        while ((match = TREE_TAGS.exec(source))) {
            const fullStart = match.index;
            const closing = source[fullStart + 1] === "/";
            const tag = match[1];
            if (closing) {
                const top = stack[stack.length - 1];
                if (stack.length > 1 && top.tagName === tag.toUpperCase()) stack.pop();
                continue;
            }
            const openEnd = source.indexOf(">", fullStart);
            const rawAttrs = source.slice(fullStart + match[0].length, openEnd);
            const attrs = attrsFrom(rawAttrs);
            const node = document.createElement(tag);
            for (const [name, value] of Object.entries(attrs)) {
                node.setAttribute(name, value);
            }
            if (attrs.type) node.type = attrs.type;
            if (attrs.placeholder) node.placeholder = attrs.placeholder;
            if (Object.prototype.hasOwnProperty.call(attrs, "checked")) node.checked = true;
            if (tag === "button") {
                const text = source.slice(openEnd + 1).split("<")[0];
                if (text.trim()) node.textContent = text.trim();
            }
            stack[stack.length - 1].append(node);
            const selfClose = source[openEnd - 1] === "/" || VOID_TAGS.has(tag);
            if (!selfClose) stack.push(node);
            TREE_TAGS.lastIndex = openEnd + 1;
        }
    },
});
documentStub.body.append(settingsRoot);
let hostEnabled = false;
let statusCredentials = [];
const credentialPosts = [];
const secret = "ghp_harness-must-not-linger";
global.apiFetch = async (url, init) => {
    const requestPath = String(url);
    if (requestPath === "/api/nest-git/status") {
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
                    credentials: statusCredentials,
                    default_id: (statusCredentials.find((item) => item.is_default) || {}).id || null,
                    match_how_to: "Add a credential for this remote, or pick which saved one to use.",
                };
            },
        };
    }
    if (requestPath.includes("nest_git_host_enabled") && init && init.method === "PUT") {
        return {
            ok: false,
            async json() { return { detail: "Host git is not visible to Shell. Configure a helper." }; },
        };
    }
    if (requestPath === "/api/nest-git/credentials" || requestPath.startsWith("/api/nest-git/items")) {
        const body = JSON.parse(init.body || "{}");
        credentialPosts.push({ url: requestPath, method: init && init.method, body });
        if (body.pat && body.pat.includes("linger")) {
            return { ok: true, async json() { return { has_pat: true, pat_last4: "nger", credentials: statusCredentials }; } };
        }
        return { ok: true, async json() { return { has_pat: true, credentials: statusCredentials }; } };
    }
    throw new Error(`unexpected settings URL ${requestPath}`);
};

function dropLiveValue(input) {
    const token = String(input.value || "");
    let stored = token;
    let dropped = false;
    Object.defineProperty(input, "value", {
        configurable: true,
        get() { return dropped ? "" : stored; },
        set(next) { stored = String(next == null ? "" : next); },
    });
    input.dispatchEvent({ type: "input" });
    dropped = true;
    return token;
}

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
if (!patInput) throw new Error("token field missing");
if (patInput.type === "password" || settingsRoot.innerHTML.includes('type="password"')) {
    throw new Error("token field is a password control; the mask must not be the value");
}
if (!patInput.classList.contains("bm-secret-masked")) {
    throw new Error("token field is not masked");
}
patInput.value = secret;
const reveal = settingsRoot.querySelector("#nest-git-pat-toggle");
if (!reveal) throw new Error("show/hide toggle missing");
await reveal.dispatchClick();
const showHideKeepsToken = reveal.textContent === "Hide"
    && reveal.getAttribute("aria-pressed") === "true"
    && !patInput.classList.contains("bm-secret-masked")
    && patInput.value === secret;
if (!showHideKeepsToken) {
    throw new Error(`show/hide changed the token: text=${reveal.textContent} value=${JSON.stringify(patInput.value)}`);
}
await reveal.dispatchClick();
if (!patInput.classList.contains("bm-secret-masked") || patInput.value !== secret) {
    throw new Error("hiding the token cleared the paste");
}
dropLiveValue(patInput);
if (patInput.value !== "") throw new Error("live value was not dropped");
const saveBtn = settingsRoot.querySelector("#nest-git-save");
if (!saveBtn || !saveBtn.classList.contains("btn-primary")) {
    throw new Error("Add form must have one primary Save button");
}
const saveButtons = settingsRoot.querySelectorAll("#nest-git-save, [data-save-field]");
if (saveButtons.length !== 1) {
    throw new Error(`expected one Save on Add, got ${saveButtons.length}`);
}
await saveBtn.dispatchClick();
const maskedPost = credentialPosts[credentialPosts.length - 1];
const maskedPastePersists = Boolean(maskedPost)
    && maskedPost.body.pat === secret
    && !maskedPost.body.ssh_key;
if (!maskedPastePersists) {
    throw new Error(`masked paste was not saved: ${JSON.stringify(maskedPost)}`);
}
const after = settingsRoot.querySelector("#nest-git-pat");
const patNotLeftInDom = !settingsRoot.innerHTML.includes(secret)
    && (!after || after.value === "");
if (!patNotLeftInDom) throw new Error("token lingered in the Settings DOM");

await NestGitSection.render(settingsRoot);
const tokenOnly = "ghp_token-only-save";
settingsRoot.querySelector("#nest-git-pat").value = tokenOnly;
settingsRoot.querySelector("#nest-git-ssh").value = "";
await settingsRoot.querySelector("#nest-git-save").dispatchClick();
const tokenOnlyPost = credentialPosts[credentialPosts.length - 1];
const tokenOnlySaves = Boolean(tokenOnlyPost)
    && tokenOnlyPost.body.pat === tokenOnly
    && !tokenOnlyPost.body.ssh_key;
if (!tokenOnlySaves) {
    throw new Error(`token-only save failed: ${JSON.stringify(tokenOnlyPost)}`);
}

await NestGitSection.render(settingsRoot);
settingsRoot.querySelector("#nest-git-pat").value = "";
settingsRoot.querySelector("#nest-git-ssh").value = "";
const postsBeforeEmpty = credentialPosts.length;
await settingsRoot.querySelector("#nest-git-save").dispatchClick();
const emptyError = settingsRoot.querySelector('[data-credential-field-error="pat"]');
const emptyRefusesFieldError = credentialPosts.length === postsBeforeEmpty
    && Boolean(emptyError)
    && emptyError.textContent.includes("empty field");
if (!emptyRefusesFieldError) {
    throw new Error("empty Add must field-error, not banner");
}

await NestGitSection.render(settingsRoot);
settingsRoot.querySelector("#nest-git-pat").value = "ghp_with-bad-ssh";
settingsRoot.querySelector("#nest-git-ssh").value = "not-a-key";
const postsBeforePartial = credentialPosts.length;
await settingsRoot.querySelector("#nest-git-save").dispatchClick();
const sshFieldError = settingsRoot.querySelector('[data-credential-field-error="ssh"]');
const tokenPersistsBadSsh = credentialPosts.length === postsBeforePartial + 1
    && credentialPosts[credentialPosts.length - 1].body.pat === "ghp_with-bad-ssh"
    && Boolean(sshFieldError);
if (!tokenPersistsBadSsh) {
    throw new Error(`bad ssh must field-error while saving token: ${JSON.stringify(credentialPosts[credentialPosts.length - 1])}`);
}

await NestGitSection.render(settingsRoot);
settingsRoot.querySelector("#nest-git-ssh").value = "-----BEGIN OPENSSH PRIVATE KEY-----\nline\n-----END OPENSSH PRIVATE KEY-----\n";
await settingsRoot.querySelector("#nest-git-save").dispatchClick();
const sshOnlyPost = credentialPosts[credentialPosts.length - 1];
const sshOnlySaves = Boolean(sshOnlyPost)
    && sshOnlyPost.body.ssh_key
    && !sshOnlyPost.body.pat;
if (!sshOnlySaves) {
    throw new Error(`ssh-only save failed: ${JSON.stringify(sshOnlyPost)}`);
}

statusCredentials = [
    { id: "a", label: "A", match: "", is_default: true, has_pat: true, pat_last4: "abcd", has_ssh: false },
    { id: "b", label: "B", match: "github.com/B/*", is_default: false, has_pat: true, pat_last4: "efgh", has_ssh: false },
];
await NestGitSection.render(settingsRoot);
const boxes = [...settingsRoot.querySelectorAll("[data-nest-default]")];
if (boxes.filter((box) => box.checked).length !== 1 || !boxes[0].checked) {
    throw new Error(`expected one default, got ${boxes.map((box) => box.checked).join(",")}`);
}
boxes[1].checked = true;
boxes[1].dispatchEvent({ type: "change" });
const oneDefaultToggle = boxes.filter((box) => box.checked).length === 1 && boxes[1].checked && !boxes[0].checked;
if (!oneDefaultToggle) {
    throw new Error(`second default did not clear the first: ${boxes.map((box) => box.checked).join(",")}`);
}
const editPat = settingsRoot.querySelector(".nest-edit-pat");
editPat.value = "ghp_edit-masked-paste";
dropLiveValue(editPat);
await settingsRoot.querySelector(".nest-git-save-edit").dispatchClick();
const editPost = credentialPosts[credentialPosts.length - 1];
if (!editPost || editPost.body.pat !== "ghp_edit-masked-paste" || !editPost.url.includes("/api/nest-git/items/a")) {
    throw new Error(`edit masked paste missed: ${JSON.stringify(editPost)}`);
}

const maskedCard = pendingNestCard("nest-mask", "git push origin main");
const maskedEl = paintCard(list, maskedCard);
let cardBody = null;
maskedEl.replaceChildren();
Card.renderHostPathConsentCard(maskedEl, maskedCard, async (url, init) => {
    cardBody = JSON.parse((init && init.body) || "{}");
    if (!String(url).endsWith("/credentials")) throw new Error(`unexpected card URL ${url}`);
    return { ok: true, async text() { return "{}"; } };
});
const maskAdd = actionButtons(maskedEl).find((btn) => btn.textContent === ADD_LABEL);
if (!maskAdd) throw new Error("card add button missing");
await maskAdd.dispatchClick();
const cardToken = [...maskedEl.querySelectorAll("input")].find((node) => node.placeholder === "GitHub access token");
const cardShow = actionButtons(maskedEl).find((btn) => btn.textContent === "Show");
if (!cardToken || cardToken.type === "password" || !cardShow) {
    throw new Error("card token field has no show/hide");
}
cardToken.value = "ghp_card-masked-paste";
await cardShow.dispatchClick();
if (cardShow.textContent !== "Hide" || cardToken.value !== "ghp_card-masked-paste") {
    throw new Error("card show/hide changed the token");
}
dropLiveValue(cardToken);
const cardSave = actionButtons(maskedEl).find((btn) => btn.textContent === "Save");
await cardSave.dispatchClick();
const cardMaskedPastePersists = Boolean(cardBody)
    && cardBody.pat === "ghp_card-masked-paste"
    && !cardBody.ssh_key;
if (!cardMaskedPastePersists) {
    throw new Error(`card masked paste was not saved: ${JSON.stringify(cardBody)}`);
}

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
    cardShowsPickSaved: true,
    enablePostsBody: true,
    schemaMismatchDismisses: true,
    maskedPastePersists: true,
    showHideKeepsToken: true,
    onePrimarySaveOnAdd: true,
    tokenOnlySaves: true,
    emptyRefusesFieldError: true,
    tokenPersistsBadSsh: true,
    sshOnlySaves: true,
    oneDefaultToggle: true,
    cardMaskedPastePersists: true,
}));
})().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
