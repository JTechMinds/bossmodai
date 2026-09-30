/**
 * BossMod AI — the breadcrumb trail in a modal's head.
 *
 * Split from core/overlays.js, which owns the modal LAYERS, when the trail
 * pushed that file past the line cap — the same seam core/overlay-focus.js was
 * cut along. overlays.js holds the stack and hands it over; this module holds
 * how a trail looks, the frame's ‹, what a valid step is, and the ONE walk
 * back that a crumb click takes. It never opens or keeps a layer itself.
 *
 * A layer's crumbs are its title and then the steps its dialog reported
 * (createModal's `setSteps`), and a head shows the crumbs of every layer up to
 * its own, base first: `Brian › Write TDD specs › Draft M5.3 list`. The last
 * crumb is the layer's `h2.modal-title`; the earlier ones are buttons that go
 * back to themselves.
 */
const BossModModalTrail = (() => {
    const { h, clear } = BossModDom;

    /**
     * Ancestors shown before the trail collapses. Past this, the first one, a
     * `…`, and the last two: the head's ✕ and tools must stay on the row, and
     * the hidden crumbs are still one ‹ or Esc away each.
     */
    const MAX_ANCESTORS = 3;
    /** The collapsed stretch of a long trail, as render() sees it. */
    const GAP = Object.freeze({ gap: true });

    /**
     * The frame's ‹: the one back control, built only for createModal.
     *
     * `.btn.btn-sm.step-back` with a lucide `chevron-left`: the bordered,
     * icon-only control controls.css squares off through `.btn[data-tooltip]`.
     * No longer exported from core/overlays.js: the Add agent form and the
     * marketplace detail built their own from it, and the trail replaced both.
     * The glyph is a placeholder until createModal paints the head.
     *
     * @param {() => void} onBack  What going back means: the layer's backOne.
     * @returns {HTMLButtonElement} Named and hidden by render().
     */
    function backButton(onBack) {
        return h('button', {
            class: 'btn btn-sm step-back modal-back',
            type: 'button',
            'aria-label': 'Back',
            'data-tooltip': 'Back',
            onclick: onBack,
        }, h('i', { 'data-lucide': 'chevron-left', 'aria-hidden': 'true' }));
    }

    /**
     * Check and copy the steps a dialog reports through createModal's
     * `setSteps`.
     *
     * @param {Array<{title: string, onBack: () => void}>} steps
     * @returns {Array<{title: string, onBack: () => void}>} A copy, so the
     *   frame's record is its own to pop.
     * @throws {Error} On anything but an array of `{ title: non-empty string,
     *   onBack: function }` — a nameless crumb or a step with no way back is
     *   a caller's bug.
     */
    function copySteps(steps) {
        const valid = Array.isArray(steps) && steps.every((step) => step
            && typeof step.title === 'string' && step.title.trim()
            && typeof step.onBack === 'function');
        if (!valid) {
            throw new Error('[modal-trail] setSteps needs an array of { title: non-empty string, onBack: function }');
        }
        return steps.map((step) => ({ title: step.title, onBack: step.onBack }));
    }

    /**
     * A crumb click: go back until that crumb is the current one — the
     * layers above its layer close, top first, and then its steps pop one at
     * a time through the layer's own `backOne`, so each step's `onBack` runs
     * exactly as ‹ would run it.
     *
     * @param {object[]} layers  The open layers, base first (overlays.js's).
     * @param {object} target  The crumb's layer record.
     * @param {number} depth   How many of its steps stay.
     * @returns {void}
     * @throws {Error} When the target is no longer open, or when a step's
     *   onBack re-reports the step it left: the walk could never end, and a
     *   frozen head is worse than a named bug.
     */
    function walkTo(layers, target, depth) {
        const index = layers.indexOf(target);
        if (index === -1) throw new Error('[modal-trail] a crumb for a layer that is no longer open');
        layers.slice(index + 1).reverse().forEach((layer) => layer.close());
        // An onClose above may have closed the target with it (a dialog that
        // tears down what it opened); then there is nothing left to walk.
        if (!layers.includes(target)) return;
        while (target.steps.length > depth) {
            const before = target.steps.length;
            target.backOne();
            if (target.steps.length >= before) {
                throw new Error('[modal-trail] a step\'s onBack re-reported the step it left');
            }
        }
    }

    /**
     * Every crumb the head of `layers[index]` shows, base first.
     *
     * @param {Array<{title: string, steps: Array<{title: string}>}>} layers
     *   The open layers, base first.
     * @param {number} index  The layer whose head this is.
     * @returns {Array<{title: string, layer: object, depth: number}>} `layer`
     *   and `depth` are where a click on the crumb goes back to — depth 0 for
     *   a layer's own title, n for its n-th step.
     */
    function crumbsOf(layers, index) {
        const crumbs = [];
        layers.slice(0, index + 1).forEach((layer) => {
            crumbs.push({ title: layer.title, layer, depth: 0 });
            layer.steps.forEach((step, at) => crumbs.push({ title: step.title, layer, depth: at + 1 }));
        });
        return crumbs;
    }

    /** The chevron between two crumbs; decorative, the list says the order. */
    const separator = () => h('i', {
        'data-lucide': 'chevron-right', class: 'modal-crumb-sep', 'aria-hidden': 'true',
    });

    /**
     * One ancestor crumb, or the collapsed gap. Buttons, not links: a crumb
     * walks the stack back, it does not go to an address.
     */
    function crumbItem(crumb, first, onCrumb) {
        if (crumb === GAP) {
            return h('li', { 'aria-hidden': 'true' },
                separator(), h('span', { class: 'modal-crumb-gap' }, '…'));
        }
        return h('li', {},
            first ? null : separator(),
            h('button', {
                class: 'modal-crumb',
                type: 'button',
                onclick: () => onCrumb(crumb.layer, crumb.depth),
            }, crumb.title));
    }

    /**
     * Rebuild one layer's trail from its crumbs, and point its ‹ at the crumb
     * before the current one.
     *
     * The current crumb is the layer's own `h2.modal-title`, MOVED into the
     * new list rather than rebuilt, so the node a caller or a harness holds is
     * the one on screen. The dialog's accessible name follows it: a dialog
     * showing a pack is named for the pack.
     *
     * @param {{element: HTMLElement, trailNode: HTMLElement,
     *   titleNode: HTMLElement, back: HTMLElement}} layer  The layer's nodes.
     * @param {Array<{title: string, layer: object, depth: number}>} crumbs
     *   From crumbsOf(); never empty — a layer is always its own crumb.
     * @param {(layer: object, depth: number) => void} onCrumb  What a click
     *   on an ancestor crumb does: overlays.js's walk back to it.
     * @returns {void}
     * @throws {Error} On an empty crumb list: a head with no title is a bug in
     *   the caller, not something to render.
     */
    function render(layer, crumbs, onCrumb) {
        if (!crumbs.length) throw new Error('[modal-trail] a trail needs at least its own crumb');
        const current = crumbs[crumbs.length - 1];
        const earlier = crumbs.slice(0, -1);
        layer.titleNode.textContent = current.title;
        layer.element.setAttribute('aria-label', current.title);
        const shown = earlier.length > MAX_ANCESTORS
            ? [earlier[0], GAP, ...earlier.slice(-2)]
            : earlier;
        clear(layer.trailNode);
        layer.trailNode.append(h('ol', {},
            shown.map((crumb, at) => crumbItem(crumb, at === 0, onCrumb)),
            h('li', { 'aria-current': 'page' }, shown.length ? separator() : null, layer.titleNode)));
        // One crumb is a base layer with nowhere to go back to: a live
        // control that does nothing is worse than none.
        layer.back.hidden = earlier.length === 0;
        if (earlier.length) {
            // The name and the tooltip are one string, and both follow the
            // crumb that going back lands on.
            const label = `Back to ${earlier[earlier.length - 1].title}`;
            layer.back.setAttribute('aria-label', label);
            layer.back.setAttribute('data-tooltip', label);
        }
        BossModIcons.paint(layer.trailNode, 'modal-trail');
    }

    return { backButton, copySteps, crumbsOf, render, walkTo };
})();
