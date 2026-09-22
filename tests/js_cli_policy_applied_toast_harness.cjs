/**
 * Node harness: Default Policy / CLI rule confirmation is a toast that
 * reads "Saved / Applied", and a second announcement replaces the first.
 * Invoked by tests/test_cli_policy_applied_toast.py. Not a browser bundle.
 */
const fs = require("fs");

class FakeNode {
    constructor(tag) {
        this.tag = tag;
        this.attrs = {};
        this.children = [];
        this.className = "";
        this.textContent = "";
        this.removed = false;
        this.style = {};
    }
    setAttribute(name, value) {
        this.attrs[name] = value;
    }
    appendChild(node) {
        this.children.push(node);
        return node;
    }
    replaceChildren() {
        this.children = [];
    }
    remove() {
        this.removed = true;
    }
}

const bodyChildren = [];
const documentStub = {
    createElement(tag) {
        return new FakeNode(tag);
    },
    querySelector(selector) {
        if (selector !== "[data-cli-applied-toast-host]") return null;
        return bodyChildren.find((node) => node.attrs["data-cli-applied-toast-host"] !== undefined) || null;
    },
    body: {
        appendChild(node) {
            bodyChildren.push(node);
            return node;
        },
    },
};

global.document = documentStub;
global.BossModFormat = {
    escapeHtml: (value) => value,
    escapeAttribute: (value) => value,
};

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModCliPolicyShared = BossModCliPolicyShared;\n`);

if (typeof BossModCliPolicyShared.announceApplied !== "function") {
    throw new Error("announceApplied is not exported");
}

BossModCliPolicyShared.announceApplied();
const host = documentStub.querySelector("[data-cli-applied-toast-host]");
if (!host) throw new Error("toast host was not mounted");
if (host.className !== "cli-applied-toast-host") {
    throw new Error(`host class is ${host.className}`);
}
if (host.style.position !== "fixed") {
    throw new Error(`host is not fixed (${host.style.position})`);
}
if (host.children.length !== 1) throw new Error("expected one toast");
const first = host.children[0];
if (first.textContent !== "Saved / Applied") {
    throw new Error(`toast copy is ${JSON.stringify(first.textContent)}`);
}
if (first.attrs.role !== "status") throw new Error("toast is not a status");
if (first.className === "toast" || host.className === "toast-host") {
    throw new Error("applied toast reused the needs-arrival host");
}

BossModCliPolicyShared.announceApplied();
if (host.children.length !== 1) throw new Error("a second announcement stacked another toast");
if (host.children[0].textContent !== "Saved / Applied") {
    throw new Error("replacement toast lost the copy");
}
if (host.children[0] === first) throw new Error("the first toast was left on screen");

process.stdout.write(`${JSON.stringify({
    ok: true,
    copy: host.children[0].textContent,
    replaced: true,
})}\n`);
