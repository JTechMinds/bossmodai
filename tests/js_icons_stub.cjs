/**
 * BossModIcons, stubbed, for harnesses that are testing something else.
 *
 * The surfaces call `BossModIcons.paint(root, context)` the way they used to
 * call `lucide.createIcons` — and these harnesses stubbed that call to a
 * no-op for the same reason: a roster harness is asserting rows, not SVG
 * geometry, and it has no vendored icon bundle to paint from. The real
 * painter is exercised for real, against the real bundle, in
 * tests/js_icons_harness.cjs.
 *
 * The stub is not inert about its contract. It refuses a call with no root or
 * no context string, because the context is what turns "unknown lucide icon"
 * into a report naming the module that asked — a call site that forgets one
 * fails here, in whichever harness paints it, rather than at 3am in a browser.
 *
 * Not a browser bundle. Required by the .cjs harnesses under tests/.
 */

/**
 * Install `global.BossModIcons` as a stub that records calls.
 *
 * @param {(root: object|null, context: string) => void} [onPaint]  Called on
 *   every paint, for harnesses that assert a surface painted at all.
 * @returns {{paint: Function, paintDocument: Function, calls: object[]}} The
 *   stub, with every call recorded in `calls`.
 */
function installIconsStub(onPaint) {
    const record = typeof onPaint === "function" ? onPaint : () => {};
    const calls = [];

    function check(root, context) {
        if (!root) throw new Error(`[icons-stub] paint got no root (context: ${context})`);
        if (typeof context !== "string" || !context) {
            throw new Error("[icons-stub] paint got no context naming the call site");
        }
        calls.push({ root, context });
        record(root, context);
        return 0;
    }

    const stub = {
        paint: (root, context) => check(root, context),
        paintDocument: (context) => check(global.document && global.document.body, context),
        calls,
    };
    global.BossModIcons = stub;
    return stub;
}

module.exports = { installIconsStub };
