/**
 * BossMod AI — the one avatar, and the tint derivation behind it.
 *
 * Six surfaces used to build their own initial-in-a-circle, each with its own
 * geometry, its own way of finding the first letter, and its own idea of what
 * the agent's colour means. This is that vocabulary extracted: one builder, one
 * derivation, one guarantee.
 *
 * The guarantee is the reason the derivation lives in a pure function rather
 * than in each caller. `agent.color` is arbitrary operator-set hex — the eight
 * seeds today, anything an operator types tomorrow — so a pale-tint background
 * with dark-ink initials can only be trusted if it is PROVEN readable across
 * the colour space, not spot-checked against the colours we happen to ship.
 * tests/js_avatar_harness.cjs sweeps 216 colours with its own copy of the WCAG
 * formula and measures the worst pair; it is 4.51:1, which clears AA.
 */
const BossModAvatar = (() => {
    const { h } = BossModDom;

    /** How much of the agent's colour survives into the tint. */
    const TINT_WEIGHT = 0.16;

    /** WCAG 2.2 AA for normal text. The loop's guard, not a target. */
    const AA_RATIO = 4.5;

    /** Resolution of the darkening ramp: 20 steps of 5% reaches black. */
    const DARKEN_STEPS = 20;

    const WHITE = [255, 255, 255];
    const BLACK = [0, 0, 0];

    /** The sizes controls.css defines. An unknown one is a typo, not a default. */
    const SIZES = Object.freeze(['chip', 'sm', 'md', 'lg']);

    /** What an agent with no colour renders as. Measured 5.33:1. */
    const NEUTRAL = Object.freeze({ bg: 'var(--line)', ink: 'var(--muted)' });

    /**
     * Read `#rgb` or `#rrggbb` into channel values.
     *
     * @param {string|null} hex
     * @returns {number[]|null} null for BOTH an absent colour and an
     *   unparseable one — but only the second is a defect, so only the second
     *   is logged. An agent may legitimately have no colour set; an agent
     *   whose colour is `blue` or `#12` is a data bug worth surfacing.
     */
    function parse(hex) {
        const raw = String(hex === null || hex === undefined ? '' : hex).trim();
        if (!raw) return null;
        const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(raw);
        if (short) {
            return [1, 2, 3].map((index) => parseInt(short[index] + short[index], 16));
        }
        const long = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(raw);
        if (long) {
            return [1, 2, 3].map((index) => parseInt(long[index], 16));
        }
        console.error('[avatar] unusable colour', hex);
        return null;
    }

    /**
     * Linear blend, `t` of the way from `from` to `to`.
     *
     * @param {number[]} from
     * @param {number[]} to
     * @param {number} t  0 keeps `from`, 1 reaches `to`.
     * @returns {number[]}
     */
    function mix(from, to, t) {
        return from.map((value, index) => Math.round(value + (to[index] - value) * t));
    }

    /**
     * WCAG relative luminance.
     * @param {number[]} rgb
     * @returns {number}
     */
    function luminance(rgb) {
        const [r, g, b] = rgb.map((value) => {
            const c = value / 255;
            return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    /**
     * @param {number[]} a
     * @param {number[]} b
     * @returns {number} 1 (identical) to 21 (black on white).
     */
    function contrast(a, b) {
        const la = luminance(a);
        const lb = luminance(b);
        return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
    }

    /**
     * The lightest tile an agent sprite is ever drawn on: `--office-transit`
     * #f3f4f6. Held as channels rather than as a luminance literal so the bound
     * below is re-derived from the token rather than copied out of it.
     */
    const LIGHTEST_OFFICE_SURFACE = Object.freeze([243, 244, 246]);

    /** SC 1.4.11: non-text content clears 3:1 against what is behind it. */
    const NON_TEXT_RATIO = 3;

    /**
     * How light a seed colour may be and still be visible on the office floor.
     *
     * Task 0 ringed the sprite and measured the ring honestly: `--panel` scores
     * 5.02:1 on the chair and 3.19:1 on the desk, but 1.10–1.24:1 on the pale
     * floor and `--office-transit` tiles. On those the ring is invisible and the
     * BODY colour is the only thing separating an agent from the floor — so the
     * ring is a mitigation and this is the guarantee.
     *
     * Derivation, from `--office-transit` #f3f4f6 (relative luminance 0.9041):
     * a body fill clears 3:1 against it while
     * `(0.9041 + 0.05) / (L + 0.05) >= 3`, i.e. **L <= 0.2680**. That number is
     * computed here rather than typed, so moving the floor tokens moves the
     * bound with them instead of silently invalidating it.
     */
    const SEED_MAX_LUMINANCE =
        (luminance(LIGHTEST_OFFICE_SURFACE) + 0.05) / NON_TEXT_RATIO - 0.05;

    /**
     * Is this colour dark enough to be seen as an agent on the office floor?
     *
     * @param {string|null} hex
     * @returns {boolean} False for a colour that is too light, AND for one that
     *   is absent or unparseable — a value that cannot be measured has not been
     *   shown to be safe, and waving it through is how an invisible agent gets
     *   saved. The caller refuses; nothing is silently darkened, because a
     *   colour changed on save is a colour the operator did not pick.
     */
    function isSeedLegible(hex) {
        const rgb = parse(hex);
        if (!rgb) return false;
        return luminance(rgb) <= SEED_MAX_LUMINANCE;
    }

    /**
     * @param {number[]} rgb
     * @returns {string} `#rrggbb`.
     */
    function css(rgb) {
        return `#${rgb.map((value) => value.toString(16).padStart(2, '0')).join('')}`;
    }

    /**
     * A pale background and an ink that is guaranteed readable on it.
     *
     * The background mixes the agent's colour 16% into white, which is the
     * concept's tint weight. The ink starts at the agent's colour and darkens
     * until it clears AA on that background — a loop that always terminates,
     * because black on a tint that is at least 84% white measures 14.2:1 at
     * its very worst.
     *
     * @param {string|null} hex  `#rgb` or `#rrggbb`. Null/empty is a real state
     *   (an agent with no colour set) and yields the neutral pair.
     * @returns {{bg: string, ink: string}} CSS colour values.
     */
    function tintFor(hex) {
        const rgb = parse(hex);
        if (!rgb) return { bg: NEUTRAL.bg, ink: NEUTRAL.ink };
        const bg = mix(WHITE, rgb, TINT_WEIGHT);
        let ink = rgb;
        // The guard is the RATIO, not the step count, so a future tint weight
        // cannot silently break AA — it can only cost more steps.
        for (let step = 0; step <= DARKEN_STEPS && contrast(bg, ink) < AA_RATIO; step += 1) {
            ink = mix(rgb, BLACK, step / DARKEN_STEPS);
        }
        return { bg: css(bg), ink: css(ink) };
    }

    /**
     * The letter the circle carries.
     *
     * @param {string|null} name
     * @returns {string} One uppercase character; `?` for a nameless agent,
     *   which is still better than an empty circle nobody can identify.
     */
    function initial(name) {
        return String(name || '?').trim().charAt(0).toUpperCase() || '?';
    }

    /**
     * Build one avatar node.
     *
     * @param {object} options
     * @param {string|null} options.name          Drives the initial.
     * @param {string|null} options.color         The agent's stored hex.
     * @param {'chip'|'sm'|'md'|'lg'} [options.size='md']
     * @param {boolean} [options.interactive=false]  True builds a <button>
     *   carrying `label` as its accessible name — Enter and Space then work
     *   with no key handling of its own (SC 2.1.1). False builds a
     *   `<span aria-hidden>` to sit beside text that already names the person,
     *   because a second announcement of the same name is noise.
     * @param {string} [options.label='']         Required when interactive.
     * @param {Function|null} [options.onClick=null]  Required when interactive.
     * @returns {HTMLElement}
     * @throws {Error} On an unknown size, or an interactive avatar with no
     *   accessible name or no handler — each of those renders a control that
     *   looks live and is not.
     */
    function create(options) {
        const opts = options || {};
        const size = opts.size || 'md';
        if (!SIZES.includes(size)) {
            throw new Error(`[avatar] unknown size "${size}"; expected one of ${SIZES.join(', ')}`);
        }
        const { bg, ink } = tintFor(opts.color === undefined ? null : opts.color);
        const attrs = {
            class: `avatar avatar-${size}`,
            style: `background:${bg};color:${ink}`,
        };
        if (!opts.interactive) {
            return h('span', { ...attrs, 'aria-hidden': 'true' }, initial(opts.name));
        }
        const label = String(opts.label || '').trim();
        if (!label) throw new Error('[avatar] an interactive avatar needs a label');
        if (typeof opts.onClick !== 'function') {
            throw new Error('[avatar] an interactive avatar needs an onClick handler');
        }
        return h('button', {
            ...attrs,
            type: 'button',
            'aria-label': label,
            onclick: opts.onClick,
        }, initial(opts.name));
    }

    return {
        tintFor, create, initial, isSeedLegible,
        TINT_WEIGHT, AA_RATIO, SEED_MAX_LUMINANCE,
    };
})();
