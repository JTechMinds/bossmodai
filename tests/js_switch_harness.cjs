/**
 * Node harness: the toggle switch is a row-sized button, not a 26x14 pill.
 *
 * Invoked by tests/test_ui_visual_parity.py. Not a browser bundle.
 *
 * "The label+control row is the target" is a claim about the DOM, and a claim
 * about the DOM that is only written in a comment stops being true the first
 * time someone rearranges the markup. So this builds the real node and reads
 * the shape back off it.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const paths = process.argv.slice(2);
const NAMES = ["BossModDom", "BossModSwitch"];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

/** Text a screen reader would announce: aria-hidden subtrees contribute nothing. */
function accessibleText(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.textContent;
    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return "";
    return (node.children || []).map(accessibleText).join("");
}

const reported = [];
const control = BossModSwitch.create({
    label: "Show subtasks",
    pressed: false,
    onChange: (pressed) => { reported.push(pressed); },
});
const row = control.element;
const pill = row.children[0];
const labelNode = row.children[1];

const startsUnchecked = row.getAttribute("aria-checked") === "false";

row.click();
const clickChecksAndReports =
    row.getAttribute("aria-checked") === "true"
    && reported.length === 1
    && reported[0] === true;

row.click();
const secondClickUnchecks =
    row.getAttribute("aria-checked") === "false"
    && reported.length === 2
    && reported[1] === false;

control.set(true);
const setUpdatesWithoutFiring =
    row.getAttribute("aria-checked") === "true" && reported.length === 2;

function threw(build) {
    try {
        build();
        return false;
    } catch (err) {
        return true;
    }
}

process.stdout.write(JSON.stringify({
    ok: true,

    rowTag: row.tagName,
    rowRole: row.getAttribute("role"),
    rowClass: row.getAttribute("class"),
    rowType: row.getAttribute("type"),

    pillTag: pill.tagName,
    pillIsDecorative:
        pill.getAttribute("class") === "switch"
        && pill.getAttribute("aria-hidden") === "true",

    labelIsInsideTheRow:
        labelNode.getAttribute("class") === "switch-label"
        && labelNode.textContent === "Show subtasks",
    accessibleName: accessibleText(row).trim(),

    startsUnchecked,
    clickChecksAndReports,
    secondClickUnchecks,
    setUpdatesWithoutFiring,

    unlabelledThrows: threw(() => BossModSwitch.create({ onChange() {} })),
    handlerlessThrows: threw(() => BossModSwitch.create({ label: "Follow" })),
}));
