/**
 * Node harness: the rail's absolute "when did this last happen" formatter.
 *
 * Invoked by tests/test_ui_polish_round_three.py. Not a browser bundle.
 *
 * Two things here cannot be proven any other way. The literal renderings need
 * a KNOWN clock, or "today" drifts with whatever second the suite happens to
 * run in; and "local, not UTC" needs a known OFFSET, because in the timezone a
 * developer happens to sit in the two answers usually agree. So the harness
 * pins both: `process.env.TZ` for the offset, and a Date subclass whose
 * zero-argument constructor returns a fixed instant for "now".
 *
 * The module reads the clock through `new Date()` at call time, so replacing
 * the global is enough — no seam is added to production code for the test's
 * benefit.
 */
const fs = require("fs");

const RealDate = Date;
const source = fs.readFileSync(process.argv[2], "utf8");
eval(`${source}\n;global.BossModFormat = BossModFormat;\n`);
const fmt = (iso) => global.BossModFormat.formatActivityTime(iso);

/**
 * Run `fn` with `new Date()` and `Date.now()` frozen at `fixedMs`.
 *
 * @param {number} fixedMs
 * @param {() => any} fn
 * @returns {any}
 */
function withClock(fixedMs, fn) {
    class FakeDate extends RealDate {
        constructor(...args) {
            if (args.length === 0) super(fixedMs);
            else super(...args);
        }

        static now() { return fixedMs; }
    }
    global.Date = FakeDate;
    try {
        return fn();
    } finally {
        global.Date = RealDate;
    }
}

// ─── The four renderings, on a known clock in a known zone ───

process.env.TZ = "UTC";
const NOW_MS = RealDate.UTC(2026, 11, 15, 12, 0, 0);
const iso = (...parts) => new RealDate(RealDate.UTC(...parts)).toISOString();

const today = withClock(NOW_MS, () => fmt(iso(2026, 11, 15, 10, 10, 0)));
const thisYear = withClock(NOW_MS, () => fmt(iso(2026, 8, 2, 9, 0, 0)));
const lastYear = withClock(NOW_MS, () => fmt(iso(2025, 8, 2, 9, 0, 0)));
const never = withClock(NOW_MS, () => [fmt(null), fmt(""), fmt(undefined)].join("|"));
// A stored value nobody can parse is not a date. Rendering the string raw, or
// "Invalid Date", would be worse than the blank a silent conversation gets.
const unparseable = withClock(NOW_MS, () => fmt("not a timestamp"));

// ─── Local midnight, across offsets on both sides of UTC ───
//
// Kiritimati is +14 and Niue is -11, which is the widest spread there is. In
// each of them a message and "now" that share a LOCAL day can sit on either
// side of a UTC one, which is the whole of what this is checking.
const ZONES = ["UTC", "America/New_York", "Asia/Kolkata", "Pacific/Kiritimati", "Pacific/Niue"];
const midnightDetail = [];
let respectsLocalMidnight = true;

/** Local wall-clock time on the local day that contains `anchorMs`. */
function localOn(anchorMs, hour, minute, dayOffset = 0) {
    const anchor = new RealDate(anchorMs);
    return new RealDate(
        anchor.getFullYear(), anchor.getMonth(), anchor.getDate() + dayOffset,
        hour, minute, 0,
    );
}

ZONES.forEach((zone) => {
    process.env.TZ = zone;
    // 23:45 tonight, local. Every case below is read from that moment.
    const now = localOn(NOW_MS, 23, 45);
    const nowMs = now.getTime();

    // The operator's case: 23:30 tonight reads as a time.
    const lateTonight = fmt.bind(null, localOn(nowMs, 23, 30).toISOString());
    // ...and 00:30 THIS MORNING is still today, which is where a UTC
    // comparison goes wrong: in a positive offset that instant fell on
    // yesterday's UTC date, and in a negative one tonight's did.
    const earlyToday = fmt.bind(null, localOn(nowMs, 0, 30).toISOString());
    // The mirror, so "always a time" cannot pass this: read at 00:30 local,
    // last night at 23:30 is YESTERDAY and must render as a date.
    const nowAfterMidnight = localOn(nowMs, 0, 30, 1);
    const lastNight = fmt.bind(null, localOn(nowAfterMidnight.getTime(), 23, 30, -1).toISOString());

    const rendered = {
        zone,
        lateTonight: withClock(nowMs, lateTonight),
        earlyToday: withClock(nowMs, earlyToday),
        lastNight: withClock(nowAfterMidnight.getTime(), lastNight),
    };
    midnightDetail.push(rendered);
    if (rendered.lateTonight !== "11:30 PM") respectsLocalMidnight = false;
    if (rendered.earlyToday !== "12:30 AM") respectsLocalMidnight = false;
    if (!/^[A-Z][a-z]{2} \d{1,2}$/.test(rendered.lastNight)) respectsLocalMidnight = false;
});
process.env.TZ = "UTC";

process.stdout.write(JSON.stringify({
    ok: true,
    today,
    thisYear,
    lastYear,
    never: never === "||" ? "" : never,
    unparseable,
    respectsLocalMidnight,
    midnightDetail,
}));
