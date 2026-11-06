/**
 * Node harness: the office canvas paints its static floor once.
 *
 * Invoked by tests/test_ui_office.py. Not a browser bundle.
 *
 * Tiles, room labels and desks never move, so office-canvas.js paints them
 * into one offscreen canvas and copies it per frame. What this proves, against
 * a recording 2D context: N frames build ONE static layer and blit it N times,
 * the visible canvas never redraws a tile, and a resize — the one thing the
 * floor's pixels depend on — builds a fresh layer. It also proves name tags are
 * a pass of their own, painted after every body with the full name on a pill,
 * and that a room label sits on the room's first row rather than its midpoint.
 * Last, the sizing: the canvas fits its container's content box, stops
 * shrinking at MIN_SCALE (the wrap scrolls past that), and draws its backing
 * store at the device pixel ratio while its CSS size and hit testing stay in
 * CSS pixels.
 *
 * argv: canvas-sprites.js, canvas-motion.js, agent-status.js, office-canvas.js
 */
const fs = require("fs");

/** A 2D context that records every method call by name and accepts any property. */
function recordingContext() {
    const calls = [];
    const props = {};
    const ctx = new Proxy(props, {
        get(target, name) {
            if (name === "calls") return calls;
            if (name === "measureText") return () => ({ width: 10 });
            if (name in target) return target[name];
            return (...args) => { calls.push({ name, args }); };
        },
        set(target, name, value) {
            target[name] = value;
            return true;
        },
    });
    return ctx;
}

const canvases = [];
function fakeCanvas() {
    const canvas = {
        nodeType: 1,
        tagName: "CANVAS",
        width: 300,
        height: 150,
        style: {},
        context: recordingContext(),
        listeners: {},
        getContext(kind) {
            if (kind !== "2d") throw new Error(`unexpected context ${kind}`);
            return this.context;
        },
        addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
        removeEventListener() {},
        getBoundingClientRect() { return { left: 0, top: 0 }; },
    };
    canvases.push(canvas);
    return canvas;
}

global.document = {
    documentElement: {},
    createElement(tag) {
        if (tag !== "canvas") throw new Error(`office-canvas created an unexpected <${tag}>`);
        return fakeCanvas();
    },
};
// places.css pads .office-canvas-wrap by 12px on every side.
const WRAP_PADDING = 12;
global.window = {
    // Every palette token resolves; the value is irrelevant to a recorder.
    // The padding is what the canvas subtracts to find the content box.
    getComputedStyle: () => ({
        getPropertyValue: () => "#123456",
        paddingLeft: `${WRAP_PADDING}px`, paddingRight: `${WRAP_PADDING}px`,
        paddingTop: `${WRAP_PADDING}px`, paddingBottom: `${WRAP_PADDING}px`,
    }),
    addEventListener() {},
    removeEventListener() {},
};
global.ResizeObserver = class { observe() {} disconnect() {} };
global.requestAnimationFrame = () => 0;
global.cancelAnimationFrame = () => {};
global.performance = { now: () => 0 };

const load = (file, name) => {
    eval(`${fs.readFileSync(file, "utf8")}\n;global.${name} = ${name};\n`);
};
load(process.argv[2], "BossModCanvasSprites");
load(process.argv[3], "BossModCanvasMotion");
load(process.argv[4], "BossModAgentStatus");
load(process.argv[5], "BossModOfficeCanvas");

// 4 x 3, two void tiles: ten painted tiles, one room, one desk. The room spans
// two rows so its first row (y1 = 1) and its midpoint (1.5) differ.
const MAP = {
    width: 4,
    height: 3,
    tiles: [[2, 2, 2, 0], [2, 1, 3, 0], [2, 2, 2, 2]],
    rooms: [{ id: "r", name: "Room", bounds: [1, 1, 2, 2] }],
    desks: [{ id: "d", desk_xy: [2, 1], chair_xy: [1, 1] }],
};
const PAINTED_TILES = 10;
const TILE_SIZE = 28;  // office-canvas.js
const LONG_NAME = "Deez Coder Supreme";

function api(url) {
    if (url.startsWith("/api/map")) return Promise.resolve({ ok: true, json: () => Promise.resolve(MAP) });
    if (url.startsWith("/api/settings")) return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    return Promise.reject(new Error(`unexpected request ${url}`));
}

function fail(message) { throw new Error(message); }
const named = (ctx, name) => ctx.calls.filter((call) => call.name === name);

(async () => {
    const visible = fakeCanvas();
    const container = { clientWidth: 400, clientHeight: 300 };
    const clicked = [];
    const controller = BossModOfficeCanvas.createOfficeCanvas({
        canvas: visible, container, api, onAgentClick(id) { clicked.push(id); },
    });
    await controller.init();

    const FRAMES = 12;
    for (let i = 0; i < FRAMES; i += 1) {
        controller.updateAgents([{ id: "a1", name: "Ada", x: 1 + (i % 2), y: 1, status: "idle" }]);
    }

    const layers = canvases.filter((canvas) => canvas !== visible);
    if (layers.length !== 1) fail(`one static layer for ${FRAMES} frames, got ${layers.length}`);
    const layer = layers[0];
    if (layer.width !== visible.width || layer.height !== visible.height) {
        fail(`the layer must match the canvas: ${layer.width}x${layer.height} vs ${visible.width}x${visible.height}`);
    }
    // Every tile stroked exactly once, on the layer, across every frame.
    if (named(layer.context, "strokeRect").length !== PAINTED_TILES) {
        fail(`the floor must be painted once: ${named(layer.context, "strokeRect").length} tile strokes`);
    }
    if (named(visible.context, "strokeRect").length !== 0) {
        fail("the visible canvas must not redraw tiles");
    }
    const renders = named(visible.context, "clearRect").length;
    const blits = named(visible.context, "drawImage");
    if (renders < FRAMES || blits.length !== renders || blits.some((call) => call.args[0] !== layer)) {
        fail(`every frame blits the one layer: ${renders} renders, ${blits.length} blits`);
    }
    const staticLayerDrawnOnce = true;

    // Room label: pill and text centred on the first interior row, not the midpoint.
    const labelY = MAP.rooms[0].bounds[1] * TILE_SIZE + TILE_SIZE / 2;
    const label = named(layer.context, "fillText").find((call) => call.args[0] === "Room");
    if (!label || label.args[2] !== labelY) {
        fail(`the room label must sit on its first row (y ${labelY}), got ${label && label.args[2]}`);
    }
    const labelPill = named(layer.context, "roundRect")[0];
    if (!labelPill || labelPill.args[1] + labelPill.args[3] / 2 !== labelY) {
        fail("the room label's pill must be centred on the label's row");
    }

    // Name tags: one frame, its calls in order. The full name reaches fillText
    // unmodified, on a pill, after the last body arc.
    controller.updateAgents([{ id: "a1", name: LONG_NAME, x: 1, y: 1, status: "idle" }]);
    const frameStart = visible.context.calls.map((call) => call.name).lastIndexOf("clearRect");
    const frame = visible.context.calls.slice(frameStart);
    const nameAt = frame.findIndex((call) => call.name === "fillText" && call.args[0] === LONG_NAME);
    if (nameAt < 0) fail(`the full name "${LONG_NAME}" must be painted unmodified`);
    if (frame.some((call) => call.name === "fillText" && call.args[0] !== LONG_NAME
        && String(call.args[0]).startsWith("Deez"))) {
        fail("the name must not be shortened");
    }
    const lastArc = frame.map((call) => call.name).lastIndexOf("arc");
    if (nameAt < lastArc) fail(`the name tag must paint after every body: fillText ${nameAt}, arc ${lastArc}`);
    const tagPill = frame.map((call) => call.name).lastIndexOf("roundRect");
    if (tagPill < lastArc || tagPill > nameAt) fail("the name tag must be drawn on a pill");

    // A resize changes the floor's pixels, so it builds a new layer — once.
    // 112 x 84 px of map in a 100 x 60 box: the scale drops below the 1.5 cap.
    container.clientWidth = 100;
    container.clientHeight = 60;
    controller.resize();
    controller.updateAgents([{ id: "a1", name: "Ada", x: 2, y: 1, status: "idle" }]);
    const after = canvases.filter((canvas) => canvas !== visible);
    if (after.length !== 2) fail(`a resize must rebuild the layer exactly once, got ${after.length - 1}`);
    if (after[1].width !== visible.width) fail("the rebuilt layer must match the resized canvas");
    const resizeRebuildsTheLayer = true;

    // ─── Sizing: content box, zoom floor, device pixel ratio ───
    const MAP_W = MAP.width * TILE_SIZE;   // 112
    const MAP_H = MAP.height * TILE_SIZE;  // 84
    const MIN_SCALE = 0.75;                // office-canvas.js

    // The wrap scrolls, so the canvas fits its CONTENT box: 98px of content
    // is a 0.875 zoom, exactly 98px wide, where sizing to the 122px client
    // box would overflow the padding and scroll on a pane the floor fits.
    container.clientWidth = 98 + 2 * WRAP_PADDING;
    container.clientHeight = 300;
    controller.resize();
    if (visible.style.width !== "98px") fail(`the canvas must fit the content box: ${visible.style.width}`);

    // Smaller than the floor at MIN_SCALE: the zoom stops there and the wrap
    // scrolls, rather than shrinking 11px labels to unreadable.
    container.clientWidth = 40;
    container.clientHeight = 40;
    controller.resize();
    const minW = Math.floor(MAP_W * MIN_SCALE);
    const minH = Math.floor(MAP_H * MIN_SCALE);
    if (visible.style.width !== `${minW}px` || visible.style.height !== `${minH}px`) {
        fail(`a small pane must hold the zoom at ${MIN_SCALE}: ${visible.style.width} x ${visible.style.height}`);
    }
    if (visible.width !== minW || visible.height !== minH) {
        fail(`at a device pixel ratio of 1 the backing store is the CSS size: ${visible.width} x ${visible.height}`);
    }

    // A dense screen: the backing store doubles, the CSS box does not, and
    // both layers draw under scale x DPR so a sprite lands at the same size.
    global.window.devicePixelRatio = 2;
    controller.resize();
    if (visible.width !== 2 * minW || visible.height !== 2 * minH) {
        fail(`at DPR 2 the backing store must be twice the CSS size: ${visible.width} x ${visible.height}`);
    }
    if (visible.style.width !== `${minW}px`) fail(`the CSS size must not follow the DPR: ${visible.style.width}`);
    const dprLayer = canvases[canvases.length - 1];
    if (dprLayer === visible || dprLayer.width !== visible.width) fail("the DPR layer must match the backing store");
    const lastScale = (ctx) => named(ctx, "scale").slice(-1)[0];
    for (const [name, ctx] of [["visible", visible.context], ["layer", dprLayer.context]]) {
        const call = lastScale(ctx);
        if (!call || call.args[0] !== MIN_SCALE * 2 || call.args[1] !== MIN_SCALE * 2) {
            fail(`the ${name} canvas must draw at scale x DPR, got ${call && call.args}`);
        }
    }
    // Hit testing stays in CSS pixels: Ada stands on tile (2, 1).
    visible.listeners.click.forEach((fn) => fn({
        clientX: 2.5 * TILE_SIZE * MIN_SCALE, clientY: 1.5 * TILE_SIZE * MIN_SCALE,
    }));
    if (clicked.join() !== "a1") fail(`a click on Ada at DPR 2 must find her, got ${JSON.stringify(clicked)}`);

    controller.destroy();
    process.stdout.write(JSON.stringify({ ok: true, staticLayerDrawnOnce, resizeRebuildsTheLayer }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
