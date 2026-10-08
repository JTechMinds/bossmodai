/**
 * Node harness: BossModConsentActivity.settledCards, the pure mapping from an
 * `activity` broadcast to the consent or approval cards it settles. Invoked by
 * tests/test_ui_conversation.py with the module's path. Not a browser bundle.
 */
const fs = require("fs");

const [modulePath] = process.argv.slice(2);
eval(`${fs.readFileSync(modulePath, "utf8")}\n;global.BossModConsentActivity = BossModConsentActivity;\n`);

const { settledCards } = global.BossModConsentActivity;
const same = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);

const consent = { id: "shell-a", kind: "shell_executor", status: "enabled", grant_root: "cli_shell_enabled" };
const shellWithConsent = settledCards({
    event: "shell_executor_enabled", host_path_consent: consent, detail: "ignored",
});
const shellWithoutConsent = settledCards({ event: "shell_executor_enabled", detail: "Enabled from chat" });

const approval = (event, extra) => settledCards({
    event, command: "git push", cwd: "/repo", decision_note: "ok", ...extra,
});
const expectedApproval = (status) => [{
    kind: "cli_approval", status, command: "git push", cwd: "/repo", decision_note: "ok",
}];

console.log(JSON.stringify({
    ok: true,
    shellUsesHostPathConsent: same(shellWithConsent, [consent]),
    shellBuildsCardWithoutConsent: same(shellWithoutConsent, [{
        status: "enabled",
        kind: "shell_executor",
        grant_root: "cli_shell_enabled",
        decision_note: "Enabled from chat",
    }]),
    approvedTakesEntryStatus: same(approval("cli_approval_approved", { status: "approved_always" }),
        expectedApproval("approved_always")),
    approvedDefaultsToApproved: same(approval("cli_approval_approved"), expectedApproval("approved")),
    rejectedIsAlwaysRejected: same(approval("cli_approval_rejected", { status: "approved" }),
        expectedApproval("rejected")),
    resolvedTakesEntryStatus: same(approval("cli_approval_resolved", { status: "denied" }),
        expectedApproval("denied")),
    resolvedDefaultsToApproved: same(approval("cli_approval_resolved"), expectedApproval("approved")),
    approvalFieldsDefaultToEmpty: same(settledCards({ event: "cli_approval_resolved" }), [{
        kind: "cli_approval", status: "approved", command: "", cwd: "", decision_note: "",
    }]),
    unrelatedEventSettlesNothing: same(settledCards({ event: "status_changed", title: "Jim blocked" }), []),
    missingEntrySettlesNothing: same(settledCards(null), []) && same(settledCards(undefined), []),
}));
