#!/usr/bin/env node
/**
 * Build ui/static/js/vendor/lucide.subset.js: only the Lucide icons the app uses.
 *
 * The vendored ui/static/js/vendor/lucide.min.js (v0.469.0, ~358 KB, 1,700+
 * icons) is this script's INPUT and is no longer loaded by index.html. The app
 * names roughly a hundred icons; parsing every other one on each launch was
 * the cost this removes.
 *
 * Usage (Node only, no packages):
 *
 *   node scripts/build_lucide_subset.cjs           write the subset
 *   node scripts/build_lucide_subset.cjs --check   exit 1 if the committed
 *                                                  subset is not what this
 *                                                  script would write now
 *
 * Re-run it whenever a module starts using an icon it did not use before.
 * tests/test_lucide_subset.py runs `--check`, so a forgotten re-run fails the
 * suite rather than shipping a blank glyph.
 *
 * WHICH NAMES. Scanned: ui/static/js/** (not vendor/) and ui/templates/**.
 *
 *   1. Declared names — the value of a `data-lucide` attribute written as a
 *      literal, in markup or in an `h()` attribute object, and the value of an
 *      `icon:` property (the data maps: places, tabs, actions, doors). These
 *      MUST be real lucide icons; an unknown one fails the build with
 *      file:line, because it is a typo that would paint nothing.
 *   2. Every other quoted string literal shaped like an icon name that IS a
 *      lucide icon. Many call sites pass the name positionally —
 *      `tool('desk-edit', 'pencil', …)`, `glyph('file-text', …)` — and no
 *      pattern could know which argument of which helper is the icon. Taking
 *      every literal that resolves over-includes a few common words ('text',
 *      'type', 'shell') at a few hundred bytes each, and in exchange no
 *      statically written name can be missed. A literal that is not an icon is
 *      just a string here and is ignored.
 *
 * A name assembled at runtime (`'circle-' + status`) is visible to neither, so
 * the tree has none: write each variant out as a literal in a map. If one
 * slips through anyway, core/icons.js console.errors and throws on it.
 *
 * WHAT IS EMITTED. The icon definitions are copied from the bundle as data
 * (`[tag, attrs, children]`, exactly what `lucide.icons[Key]` holds), and
 * `createElement` is the bundle's own builder, sliced verbatim from the
 * minified source between two anchors — nothing about how an SVG is built is
 * re-implemented. If an upgrade moves either anchor the build fails rather
 * than guessing.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const JS_ROOT = path.join(ROOT, 'ui', 'static', 'js');
const TEMPLATES = path.join(ROOT, 'ui', 'templates');
const VENDOR = path.join(JS_ROOT, 'vendor', 'lucide.min.js');
const OUT = path.join(JS_ROOT, 'vendor', 'lucide.subset.js');

/** The bundle's SVG builder: `n` recurses, `I0` is what it exports as createElement. */
const BUILDER_START = 'const n=(t,d,c=[])=>{';
const BUILDER_END = 'var I0=([t,d,c])=>n(t,d,c);';
const BUILDER_EXPORT = 'I0';

/** A literal `data-lucide` value: `data-lucide="x"` or `'data-lucide': 'x'`. */
const DECLARED_ATTR = /data-lucide['"]?\s*[:=]\s*(['"])([^'"$]+)\1/g;
/** An `icon:` property written as a literal. */
const DECLARED_PROP = /\bicon\s*:\s*(['"])([^'"]+)\1/g;
/** Any quoted literal shaped like a kebab-case icon name. */
const CANDIDATE = /(['"`])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\1/g;

/**
 * Kebab name to the PascalCase key `lucide.icons` uses.
 *
 * The vendor's own `_$` helper, regex included — the same copy core/icons.js
 * keeps — so the build and the painter can never disagree about a key.
 *
 * @param {string} name
 * @returns {string}
 */
function iconKey(name) {
    return String(name).replace(
        /(\w)(\w*)(_|-|\s*)/g,
        (_match, head, tail) => head.toUpperCase() + tail.toLowerCase(),
    );
}

/**
 * Every source file the app ships, sorted so the output is deterministic.
 *
 * @returns {string[]} Absolute paths.
 */
function sourceFiles() {
    const walk = (dir, keep) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return full === path.join(JS_ROOT, 'vendor') ? [] : walk(full, keep);
        return keep(entry.name) ? [full] : [];
    });
    return [
        ...walk(JS_ROOT, (name) => name.endsWith('.js')),
        ...walk(TEMPLATES, (name) => name.endsWith('.html')),
    ].sort();
}

/**
 * Collect the icon keys the sources name.
 *
 * @param {object} icons  The full bundle's `icons` map.
 * @returns {{keys: string[], unknown: string[]}} Sorted keys, and every
 *   declared name that is not an icon as "file:line: name".
 */
function collect(icons) {
    const keys = new Set();
    const unknown = [];
    for (const file of sourceFiles()) {
        const text = fs.readFileSync(file, 'utf8');
        const relative = path.relative(ROOT, file);
        const lineOf = (index) => text.slice(0, index).split('\n').length;
        for (const pattern of [DECLARED_ATTR, DECLARED_PROP]) {
            for (const match of text.matchAll(pattern)) {
                const name = match[2];
                if (Object.prototype.hasOwnProperty.call(icons, iconKey(name))) keys.add(iconKey(name));
                else unknown.push(`${relative}:${lineOf(match.index)}: "${name}"`);
            }
        }
        for (const match of text.matchAll(CANDIDATE)) {
            if (Object.prototype.hasOwnProperty.call(icons, iconKey(match[2]))) keys.add(iconKey(match[2]));
        }
    }
    return { keys: [...keys].sort(), unknown };
}

/**
 * The bundle's createElement source, verbatim.
 *
 * @param {string} bundle  lucide.min.js as text.
 * @returns {string}
 * @throws {Error} When either anchor is missing or appears more than once.
 */
function builderSource(bundle) {
    const start = bundle.indexOf(BUILDER_START);
    const end = bundle.indexOf(BUILDER_END);
    const once = (needle, at) => at !== -1 && bundle.indexOf(needle, at + 1) === -1;
    if (!once(BUILDER_START, start) || !once(BUILDER_END, end) || end < start) {
        throw new Error(
            'lucide.min.js no longer has its createElement where this script expects it '
            + `(anchors ${JSON.stringify(BUILDER_START)} … ${JSON.stringify(BUILDER_END)}). `
            + 'Read the new bundle and update the anchors.',
        );
    }
    return bundle.slice(start, end + BUILDER_END.length);
}

/**
 * Render the subset file.
 *
 * @param {string} bundle  lucide.min.js as text.
 * @param {object} lucide  The same bundle, evaluated.
 * @param {string[]} keys  Icon keys to ship.
 * @returns {string}
 * @throws {Error} When the bundle's version banner is missing.
 */
function render(bundle, lucide, keys) {
    const version = /@license lucide v(\d+\.\d+\.\d+)/.exec(bundle);
    if (!version) throw new Error('lucide.min.js has no "@license lucide vX.Y.Z" banner');
    // The icons' root attrs are one shared default object in the bundle; the
    // most common value is written once, which keeps ~250 bytes per icon out
    // of the file. An icon with different attrs keeps its own inline.
    const counts = new Map();
    keys.forEach((key) => {
        const attrs = JSON.stringify(lucide.icons[key][1]);
        counts.set(attrs, (counts.get(attrs) || 0) + 1);
    });
    const shared = [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0][0];
    const rows = keys.map((key) => {
        const [tag, attrs, children] = lucide.icons[key];
        const attrText = JSON.stringify(attrs) === shared ? 'A' : JSON.stringify(attrs);
        return `${JSON.stringify(key)}:[${JSON.stringify(tag)},${attrText},${JSON.stringify(children)}]`;
    });
    return [
        '/**',
        ` * GENERATED by scripts/build_lucide_subset.cjs from js/vendor/lucide.min.js — do not edit.`,
        ` * @license lucide v${version[1]} - ISC (see js/vendor/VENDOR_SOURCES.md)`,
        ` * ${keys.length} of ${Object.keys(lucide.icons).length} icons: ${keys.join(', ')}`,
        ' */',
        '(function () {',
        '"use strict";',
        builderSource(bundle),
        `const A=${shared};`,
        'const icons={',
        rows.join(',\n'),
        '};',
        `window.lucide=Object.freeze({icons:Object.freeze(icons),createElement:${BUILDER_EXPORT}});`,
        '})();',
        '',
    ].join('\n');
}

/**
 * Entry point.
 *
 * @param {string[]} argv  `--check` compares instead of writing.
 * @returns {number} Exit code.
 */
function main(argv) {
    const check = argv.includes('--check');
    const bundle = fs.readFileSync(VENDOR, 'utf8');
    const lucide = require(VENDOR);
    const { keys, unknown } = collect(lucide.icons);
    if (unknown.length) {
        process.stderr.write(`Not lucide icons (fix the name at the call site):\n  ${unknown.join('\n  ')}\n`);
        return 1;
    }
    if (!keys.length) {
        process.stderr.write('No icon names found — the scan is broken, refusing to write an empty subset.\n');
        return 1;
    }
    const text = render(bundle, lucide, keys);
    const relative = path.relative(ROOT, OUT);
    if (check) {
        const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : null;
        if (current !== text) {
            process.stderr.write(`${relative} is stale: run node scripts/build_lucide_subset.cjs\n`);
            return 1;
        }
        process.stdout.write(`${relative} is current (${keys.length} icons)\n`);
        return 0;
    }
    fs.writeFileSync(OUT, text);
    process.stdout.write(`wrote ${relative}: ${keys.length} icons, ${Buffer.byteLength(text)} bytes\n`);
    return 0;
}

process.exitCode = main(process.argv.slice(2));
