/**
 * Node harness: the office canvas's motion layer.
 *
 * Invoked by tests/test_ui_office.py. Not a browser bundle.
 *
 * Phase 3A shipped canvas-motion.js with source-level coverage only, and
 * flagged the gap. The module is pure — it never reads the document — so what
 * it actually does is testable: an agent walking a path arrives, and arrives
 * exactly, rather than overshooting or stalling a pixel short; a second walk
 * for the same agent replaces the first instead of two interpolations fighting
 * over one agent's coordinates; and a thought bubble expires on a clock of its
 * own, with no consumer having to remember to pump it.
 *
 * Time is driven by hand. requestAnimationFrame, performance.now, Date.now and
 * setTimeout are all replaced with a single controllable clock, so every
 * assertion is about the module's arithmetic and not about how fast this
 * machine happens to be.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

// The motion layer never touches the document; the DOM is installed only so
// the module evaluates in the same environment as every other harness.
installDom();

let now = 0;
let nextFrameId = 1;
let nextTimerId = 1;
const frames = new Map();
const timers = new Map();

global.performance = { now: () => now };
Date.now = () => now;
global.requestAnimationFrame = (fn) => {
    const id = nextFrameId;
    nextFrameId += 1;
    frames.set(id, fn);
    return id;
};
global.cancelAnimationFrame = (id) => { frames.delete(id); };
global.setTimeout = (fn, ms) => {
    const id = nextTimerId;
    nextTimerId += 1;
    timers.set(id, { fn, at: now + (ms || 0) });
    return id;
};
global.clearTimeout = (id) => { timers.delete(id); };

/**
 * Move the clock forward and run whatever that made due.
 *
 * One frame and one round of timers per step, which is what a browser does:
 * a handler that schedules another frame gets it on the NEXT tick, not this
 * one, so a runaway pump would show up as a hang rather than as a pass.
 *
 * @param {number} ms
 * @param {number} [steps=1]
 * @returns {void}
 */
function advance(ms, steps) {
    const rounds = steps || 1;
    for (let i = 0; i < rounds; i += 1) {
        now += ms / rounds;
        for (const [id, timer] of [...timers.entries()]) {
            if (timer.at <= now) {
                timers.delete(id);
                timer.fn();
            }
        }
        for (const [id, fn] of [...frames.entries()]) {
            frames.delete(id);
            fn(now);
        }
    }
}

const source = fs.readFileSync(process.argv[2], "utf8");
eval(`${source}\n;global.BossModCanvasMotion = BossModCanvasMotion;\n`);
const { BossModCanvasMotion } = global;

function fail(message) {
    throw new Error(message);
}

function main() {
    const agents = [{ id: "a1", x: 0, y: 0, status: "idle" }];
    let repaints = 0;
    // The repaint DRAWS, exactly as office-canvas.js does: it calls bubbles(),
    // which is the only place an expired thought is dropped. A stub that
    // counted repaints without drawing would leave every expired thought in
    // the map and the expiry timer rescheduling itself forever — a real
    // property of this contract, and one worth modelling rather than mocking
    // away.
    const motion = BossModCanvasMotion.createMotion({
        repaint: () => { repaints += 1; motion.bubbles(); },
        getAgents: () => agents,
    });

    // ─── 1. A walk advances toward its destination and stops AT it ───
    // Four tiles a second, so each tile takes 250ms.

    motion.startWalk("a1", [[0, 0], [1, 0], [2, 0]], 4);
    if (agents[0].x !== 0) fail("startWalk must snap the agent to the path's first point");
    if (agents[0].status !== "in_transit") fail("a walking agent is in transit");
    if (!motion.isWalking("a1")) fail("the agent must be walking");

    advance(125);
    if (Math.abs(agents[0].x - 0.5) > 1e-9) {
        fail(`half a tile in, the agent should be at x=0.5, got ${agents[0].x}`);
    }
    advance(125);
    if (Math.abs(agents[0].x - 1) > 1e-9) {
        fail(`one tile in, the agent should be at x=1, got ${agents[0].x}`);
    }
    const midRepaints = repaints;
    if (midRepaints === 0) fail("moving must repaint");

    // Well past the end: it stops exactly on the last point, not beyond it,
    // and the walk drops itself rather than waiting for a completion event.
    advance(1000);
    if (agents[0].x !== 2 || agents[0].y !== 0) {
        fail(`the walk must end exactly on its last point, got (${agents[0].x}, ${agents[0].y})`);
    }
    if (motion.isWalking("a1")) fail("a finished walk must not still be walking");
    const settled = repaints;
    advance(1000);
    if (repaints !== settled) fail("a finished walk must stop the clock");
    const walkAdvancesAndStops = true;

    // ─── 2. A second walk replaces the first rather than racing it ───
    // Two live interpolations over one agent's coordinates would fight, and
    // whichever frame ran last would win — the agent would jitter between two
    // destinations and settle on neither.

    motion.startWalk("a1", [[0, 0], [8, 0]], 4);
    advance(100);
    const divertedFrom = agents[0].x;
    if (divertedFrom <= 0) fail("the first walk must have started moving");
    motion.startWalk("a1", [[0, 0], [0, 4]], 4);
    if (agents[0].x !== 0 || agents[0].y !== 0) {
        fail("the replacing walk must snap the agent to its own start");
    }
    advance(250);
    if (agents[0].y !== 4) fail(`the second walk must complete, got y=${agents[0].y}`);
    if (agents[0].x !== 0) {
        fail(`the first walk must be gone, but x moved to ${agents[0].x}`);
    }
    if (motion.isWalking("a1")) fail("both walks must be finished");
    const afterReplace = repaints;
    advance(2000);
    if (repaints !== afterReplace) fail("a replaced walk must not keep ticking");
    const secondWalkReplacesFirst = true;

    // ─── 3. A bubble expires on its own clock ───
    // Nothing else is moving, so every repaint here comes from the layer's own
    // expiry timer. A layer that needed its consumer to pump it would show a
    // bubble frozen on screen forever, silently.

    motion.setThoughtDuration(1000);
    motion.showThought("a1", "thinking");
    let bubbles = motion.bubbles();
    if (bubbles.length !== 1) fail(`one thought, one bubble, got ${bubbles.length}`);
    if (bubbles[0].opacity !== 1) fail("a fresh bubble is fully opaque");
    if (bubbles[0].x !== agents[0].x || bubbles[0].y !== agents[0].y) {
        fail("a bubble sits on its agent");
    }

    const beforeExpiry = repaints;
    advance(600, 6);
    if (repaints <= beforeExpiry) fail("the expiry clock must repaint on its own");
    bubbles = motion.bubbles();
    if (bubbles.length !== 1) fail("the bubble must still be alive at 600ms of 1000");
    // 400ms of a 500ms fade window left.
    if (Math.abs(bubbles[0].opacity - 0.8) > 1e-9) {
        fail(`the bubble should be fading, got opacity ${bubbles[0].opacity}`);
    }

    advance(500, 5);
    if (motion.bubbles().length !== 0) fail("the bubble must expire on its own clock");
    const quiet = repaints;
    advance(2000, 20);
    if (repaints !== quiet) fail("an expired bubble must stop the clock");
    const bubbleExpiresOnItsOwnClock = true;

    // ─── 4. Bad input is refused, not absorbed ───

    let shortPathThrew = false;
    try {
        motion.startWalk("a1", [[1, 1]], 4);
    } catch (err) {
        shortPathThrew = true;
    }
    if (!shortPathThrew) fail("a path of fewer than two points must throw");

    let badDurationThrew = false;
    try {
        motion.setThoughtDuration(0);
    } catch (err) {
        badDurationThrew = err instanceof RangeError;
    }
    if (!badDurationThrew) fail("a non-positive thought duration must throw a RangeError");
    const rejectsBadInput = true;

    // ─── 5. destroy() stops everything ───

    motion.startWalk("a1", [[0, 0], [4, 0]], 4);
    motion.showThought("a1", "still thinking");
    motion.destroy();
    const afterDestroy = repaints;
    const restingX = agents[0].x;
    advance(2000, 20);
    if (repaints !== afterDestroy) fail("destroy must stop the clock");
    if (agents[0].x !== restingX) fail("destroy must stop moving the agent");
    if (motion.bubbles().length !== 0) fail("destroy must forget every thought");
    if (frames.size !== 0 || timers.size !== 0) {
        fail(`destroy must leave nothing pending: ${frames.size} frames, ${timers.size} timers`);
    }
    const destroyStopsTheClock = true;

    process.stdout.write(JSON.stringify({
        ok: true,
        walkAdvancesAndStops,
        secondWalkReplacesFirst,
        bubbleExpiresOnItsOwnClock,
        rejectsBadInput,
        destroyStopsTheClock,
    }));
}

try {
    main();
} catch (err) {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
}
