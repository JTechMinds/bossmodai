/**
 * Node harness: one invalidate router fans agent events to registered surfaces.
 * Invoked by tests/test_ui_operator_live_paint.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = { createElement: () => ({}), addEventListener() {} };
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModBus = BossModBus;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModOperatorInvalidate = BossModOperatorInvalidate;\n`);

const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const offAttach = BossModOperatorInvalidate.attach({ bus });

const chatEvents = [];
const settingsRepaints = [];

const offChat = BossModOperatorInvalidate.register({
    id: "harness-chat",
    topics: ["chat_message"],
    onEvent(topic, data) {
        chatEvents.push({ topic, agent_id: data && data.agent_id, content: data && data.content });
    },
});

BossModOperatorInvalidate.register({
    id: "harness-settings",
    topics: ["operator_invalidate"],
    onEvent(topic, data) {
        if (topic !== "operator_invalidate") return;
        if (!BossModOperatorInvalidate.touchesSurface(["connections"], data)) return;
        settingsRepaints.push("connections");
    },
});

bus.publish("chat_message", { agent_id: "a1", content: "live reply", from: "agent" });
bus.publish("operator_invalidate", { surfaces: ["connections"] });
bus.publish("operator_invalidate", { surfaces: ["system"] });

offChat();
offAttach();

const liveChatPaint = chatEvents.length === 1 && chatEvents[0].content === "live reply";
const settingsLivePaint = settingsRepaints.length === 1;

process.stdout.write(JSON.stringify({
    ok: true,
    liveChatPaint,
    settingsLivePaint,
    singleBusWire: BossModBus.KNOWN_TOPICS.includes("operator_invalidate"),
}));
