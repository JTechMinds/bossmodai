/**
 * Node harness: Needs store length → desktop badge/tray, and nothing else.
 *
 * Invoked by tests/test_desktop_needs_attention.py. Not a browser bundle.
 *
 * The properties a grep cannot see: the host sends the queue length (the same
 * number the bell paints), clears at zero, never forwards a need title or
 * body, and a tray-focus event opens Needs through the registered opener.
 */
const fs = require("fs");

const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
const [storePath, attentionPath] = process.argv.slice(2);
load(storePath, "BossModStore");
load(attentionPath, "BossModNeedsAttention");

const { BossModStore, BossModNeedsAttention } = global;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 6; i += 1) await settle(); };

function installTauri(invokes, listens) {
    global.__TAURI__ = {
        core: {
            invoke(cmd, args) {
                invokes.push({ cmd, args });
                return Promise.resolve();
            },
        },
        event: {
            listen(name, fn) {
                listens.push({ name, fn });
                return Promise.resolve(() => { listens.push({ name, fn, unlistened: true }); });
            },
        },
    };
}

(async () => {
    if (BossModNeedsAttention.OS_TOAST_ENABLED !== false) {
        throw new Error("OS toast must stay parked for v1");
    }
    if (BossModNeedsAttention.COMMAND !== "sync_needs_attention") {
        throw new Error("desktop command drifted");
    }
    if (typeof Notification !== "undefined") {
        throw new Error("harness must not polyfill Notification — the host must not call it");
    }

    // ─── Same store slice as the bell ───
    const invokes = [];
    const listens = [];
    installTauri(invokes, listens);

    const store = BossModStore.createStore({ needs: [] });
    const baseline = store.subscriberCount();
    const host = BossModNeedsAttention.createHost({ store });
    await drain();

    if (invokes.length !== 1 || invokes[0].cmd !== "sync_needs_attention") {
        throw new Error(`boot must sync once, got ${JSON.stringify(invokes)}`);
    }
    if (invokes[0].args.count !== 0) {
        throw new Error(`empty queue must clear the badge, got ${JSON.stringify(invokes[0].args)}`);
    }
    if (JSON.stringify(invokes[0].args) !== "{\"count\":0}") {
        throw new Error(`invoke payload must be count-only, got ${JSON.stringify(invokes[0].args)}`);
    }

    const secretNeed = {
        id: "n-secret",
        kind: "approval",
        title: "Agent wants to run a command",
        sub: "curl -H 'Authorization: Bearer sk-SECRET-token' https://example.invalid",
        token: "ghp_SECRET",
    };
    store.setState({ needs: [secretNeed, { id: "n2", title: "blocked on /etc/passwd" }] });
    await drain();
    const afterTwo = invokes[invokes.length - 1];
    if (afterTwo.args.count !== 2) {
        throw new Error(`badge must mirror store.needs.length, got ${JSON.stringify(afterTwo)}`);
    }
    const wire = JSON.stringify(invokes);
    if (wire.includes("sk-SECRET") || wire.includes("ghp_SECRET") || wire.includes("/etc/passwd")) {
        throw new Error("OS chrome received a need body or secret");
    }
    if (Object.keys(afterTwo.args).join(",") !== "count") {
        throw new Error(`unexpected invoke keys: ${Object.keys(afterTwo.args)}`);
    }

    const beforeSameLength = invokes.length;
    store.setState({ needs: [{ id: "a" }, { id: "b" }] });
    await drain();
    if (invokes.length !== beforeSameLength) {
        throw new Error("a same-length rewrite must not re-push the badge");
    }

    store.setState({ needs: [] });
    await drain();
    if (invokes[invokes.length - 1].args.count !== 0) {
        throw new Error("dismissing the queue must clear the badge");
    }

    // ─── Tray click focuses Needs ───
    let shown = 0;
    host.setShowNeeds(() => { shown += 1; });
    const focus = listens.find((item) => item.name === BossModNeedsAttention.FOCUS_EVENT);
    if (!focus) throw new Error("host must listen for the tray focus event");
    focus.fn();
    if (shown !== 1) throw new Error("tray focus must call the Needs opener");

    host.destroy();
    if (store.subscriberCount() !== baseline) {
        throw new Error("destroy must drain the store subscription");
    }

    // ─── Browser / no Tauri: must not throw ───
    delete global.__TAURI__;
    const browserStore = BossModStore.createStore({ needs: [{ id: "x" }] });
    const browserHost = BossModNeedsAttention.createHost({ store: browserStore });
    browserStore.setState({ needs: [] });
    browserHost.destroy();

    let threw = false;
    try {
        BossModNeedsAttention.createHost({});
    } catch (err) {
        threw = String(err.message || err).includes("deps.store");
    }
    if (!threw) throw new Error("a host without a store must fail loudly");

    process.stdout.write(JSON.stringify({
        ok: true,
        mirrorsStoreCount: true,
        clearsAtZero: true,
        copyIsCountOnly: true,
        osToastParked: true,
        trayFocusOpensNeeds: true,
        browserIsNoop: true,
        disposerDrainsSubscriptions: true,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
