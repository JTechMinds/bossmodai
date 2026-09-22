/**
 * Node harness: nest Always allow, twin coalesce, and stale Dismiss.
 * Invoked by tests/test_ui_cli_approval_card.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const [consentPath] = process.argv.slice(2);
eval(`${fs.readFileSync(consentPath, "utf8")}\n;global.BossModConsentCard = BossModConsentCard;\n`);

const { BossModConsentCard } = global;

function labels(root) {
    return root.querySelectorAll(".hpc-action")
        .filter((node) => !node.hidden)
        .map((node) => node.textContent);
}

function nestCard(id, extras) {
    return Object.assign({
        id,
        agent_id: "debra",
        kind: "cli_approval",
        title: "Approve this command?",
        command: "sed -i s/True/False/ tests/test_ok.py",
        cwd: "/me/host-work/llm_helper",
        status: "pending",
        always_allow: true,
    }, extras || {});
}

function deskCard(id) {
    return {
        id,
        agent_id: "hugh",
        kind: "cli_approval",
        title: "Approve this command?",
        command: 'pip install -e ".[dev]"',
        cwd: "/me",
        status: "pending",
        always_allow: false,
    };
}

(async () => {
    const calls = [];
    const api = async (url, init) => {
        calls.push({ url, method: (init && init.method) || "GET" });
        if (url.includes("/always-allow") && url.includes("appr-nest")) {
            return {
                ok: true,
                status: 200,
                async text() {
                    return JSON.stringify({
                        id: "appr-nest",
                        kind: "cli_approval",
                        command: "sed -i s/True/False/ tests/test_ok.py",
                        cwd: "/me/host-work/llm_helper",
                        status: "approved",
                        decision_note: "Always allowed",
                    });
                },
                async json() {
                    return JSON.parse(await this.text());
                },
            };
        }
        if (url.includes("appr-dead") || url.includes("appr-twin")) {
            return {
                ok: false,
                status: 404,
                async text() {
                    return JSON.stringify({
                        detail: "Approval request not found or already resolved",
                    });
                },
            };
        }
        throw new Error(`unhandled ${url}`);
    };

    const nest = documentStub.createElement("div");
    nest.className = "host-path-consent-card";
    documentStub.body.append(nest);
    BossModConsentCard.renderCliApprovalCard(nest, nestCard("appr-nest"), api);
    const nestLabels = labels(nest);
    if (!nestLabels.includes("Approve") || !nestLabels.includes("Reject")
        || !nestLabels.includes("Always allow")) {
        throw new Error(`nest card must offer Always allow, got ${nestLabels.join(",")}`);
    }
    const nestOffersAlways = true;

    const desk = documentStub.createElement("div");
    desk.className = "host-path-consent-card";
    documentStub.body.append(desk);
    BossModConsentCard.renderCliApprovalCard(desk, deskCard("appr-desk"), api);
    const deskLabels = labels(desk);
    if (!deskLabels.includes("Approve") || !deskLabels.includes("Reject")) {
        throw new Error(`desk card must keep Approve/Reject, got ${deskLabels.join(",")}`);
    }
    if (deskLabels.includes("Always allow")) {
        throw new Error("desk card must not offer Always allow");
    }
    if (desk.textContent.includes("System AI")) {
        throw new Error("a card with no review note must stay quiet");
    }
    const deskHidesAlways = true;
    const quietWithoutNote = true;

    const explained = documentStub.createElement("div");
    explained.className = "host-path-consent-card";
    documentStub.body.append(explained);
    const reviewWhy = "System AI unsure / refused: deletes more than one file";
    BossModConsentCard.renderCliApprovalCard(
        explained,
        nestCard("appr-why", { review_note: reviewWhy }),
        api,
    );
    if (!explained.textContent.includes(reviewWhy)) {
        throw new Error(`unsure review must show on the card, got ${explained.textContent}`);
    }
    const explainedLabels = labels(explained);
    if (!explainedLabels.includes("Approve") || !explainedLabels.includes("Reject")) {
        throw new Error(`explained card must still offer Approve, got ${explainedLabels.join(",")}`);
    }
    const showsReviewWhy = true;

    const alwaysBtn = nest.querySelectorAll(".hpc-action")
        .find((node) => node.textContent === "Always allow");
    await alwaysBtn.dispatchClick();
    if (!nest.textContent.includes("Always allowed")) {
        throw new Error(`Always allow must resolve the card, got ${nest.textContent}`);
    }
    if (labels(nest).includes("Approve")) {
        throw new Error("resolved Always allow must drop live Approve");
    }
    const alwaysAllowResolves = true;

    const transcript = documentStub.createElement("div");
    transcript.setAttribute("data-transcript", "");
    const live = documentStub.createElement("div");
    live.className = "host-path-consent-card";
    const twin = documentStub.createElement("div");
    twin.className = "host-path-consent-card";
    transcript.append(live);
    transcript.append(twin);
    documentStub.body.append(transcript);
    BossModConsentCard.renderCliApprovalCard(live, nestCard("appr-dead"), api);
    BossModConsentCard.renderCliApprovalCard(twin, nestCard("appr-twin"), api);
    const approve = live.querySelectorAll(".hpc-action")
        .find((node) => node.textContent === "Approve");
    await approve.dispatchClick();
    if (labels(live).includes("Approve") || labels(live).includes("Reject")
        || labels(live).includes("Always allow")) {
        throw new Error("stale 404 must not leave Approve/Reject/Always allow live");
    }
    if (!live.textContent.includes("gone or already resolved")) {
        throw new Error(`stale card must explain the miss, got ${live.textContent}`);
    }
    if (!labels(live).includes("Dismiss")) {
        throw new Error("stale card must offer Dismiss");
    }
    if (!labels(twin).includes("Dismiss") && !twin.textContent.includes("gone or already resolved")) {
        throw new Error("coalesced sibling must morph to gone/Dismiss");
    }
    const staleMorphsToDismiss = true;

    const beforeCalls = calls.length;
    const dismiss = live.querySelectorAll(".hpc-action")
        .find((node) => node.textContent === "Dismiss");
    await dismiss.dispatchClick();
    if (calls.length !== beforeCalls) {
        throw new Error("Dismiss must not call the API or wake the agent");
    }
    if (!live.hidden || !twin.hidden) {
        throw new Error("Dismiss must collapse this card and coalesced siblings");
    }
    const dismissIsLocalOnly = true;

    process.stdout.write(JSON.stringify({
        ok: true,
        nestOffersAlways,
        deskHidesAlways,
        quietWithoutNote,
        showsReviewWhy,
        alwaysAllowResolves,
        staleMorphsToDismiss,
        dismissIsLocalOnly,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
