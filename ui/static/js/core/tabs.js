/**
 * BossMod AI — a quiet tab group: one row of buttons, one of them selected.
 *
 * Extracted from places/office/office-place.js when the Agents dialog became
 * the second surface to need one — `Add agent | Marketplace` in its head, the
 * way `Map | Org` sits in the Office header. Two hand-built copies of "roving
 * tabindex, arrows that wrap, Home and End" are two chances for the keyboard
 * to drift, which is the same reason core/search-field.js is one builder.
 *
 * It owns the TABS and nothing else. The panels they control are the
 * caller's — their role, their id, their `aria-labelledby` and which one is
 * `hidden` — because only the caller knows what showing one costs: the Office
 * resizes a canvas that was measured while hidden, and the Agents dialog puts
 * one pane's footer away and brings the other's back.
 *
 * `onSelect` is the OPERATOR'S choice and nothing else's: a click, an arrow,
 * Home or End. `select()` is the caller moving the selection itself, and it
 * never calls back — a caller that heard its own select() would run its
 * show-the-pane work twice, or loop.
 *
 * The look is .tabs / .tab in controls.css: quiet, the selected tab the
 * strongest TEXT in the group rather than the loudest fill.
 *
 * A tab MAY lead with an icon — the Agents dialog's two do, wearing the marks
 * the rail menu's doors into them wear. The icon is a lucide placeholder that
 * the CALLER paints once the group is mounted (core/icons.js), the way
 * core/search-field.js leaves its magnifier to whoever places it: this module
 * builds nodes and depends on nothing but the DOM helpers. It is decorative;
 * the label beside it is the tab's accessible name. The Office's tabs carry
 * none and render exactly as they did.
 */
const BossModTabs = (() => {
    const { h } = BossModDom;

    /**
     * Build a tab group.
     *
     * @param {object} deps
     * @param {string} deps.label  The tablist's accessible name.
     * @param {string} deps.idPrefix  Tab ids are `${idPrefix}-${tab.id}`, which
     *   is what each panel's `aria-labelledby` names.
     * @param {Array<{id: string, label: string, panelId: string, icon?: string}>} deps.tabs
     *   Left to right. `panelId` is the id of the panel the tab controls,
     *   published as its `aria-controls`. `icon`, when given, is a lucide
     *   icon name drawn before the label as an unpainted placeholder.
     * @param {string} deps.selected  The tab selected on build; one of the ids.
     * @param {(id: string) => void} deps.onSelect  Called when the OPERATOR
     *   selects — a click on a tab that is not already selected, or an arrow,
     *   Home or End. Never called by `select()`.
     * @returns {{element: HTMLElement, select: (id: string) => void,
     *   selected: () => string, focus: () => void}} `element` is the tablist
     *   to place. `select` moves the selection (`aria-selected` and the roving
     *   `tabindex`) without calling back; `selected` answers which tab is up;
     *   `focus` puts the keyboard on it.
     * @throws {Error} When the label or the id prefix is missing, `tabs` is
     *   empty or names an id twice, `selected` is not one of its ids, a tab's
     *   `icon` is given but is not a non-empty string, or onSelect is not a
     *   function — a group nobody can name, with nothing to select, or that
     *   reports to nobody is a dead control, and an icon placeholder with no
     *   name is one the painter would refuse later, further from its cause.
     *   `select()` throws on an id the group does not hold, for the same
     *   reason.
     */
    function create(deps) {
        const { label, idPrefix, tabs, selected, onSelect } = deps || {};
        if (!label) throw new Error('[tabs] deps.label is required');
        if (!idPrefix) throw new Error('[tabs] deps.idPrefix is required');
        if (!Array.isArray(tabs) || !tabs.length) {
            throw new Error('[tabs] deps.tabs must name at least one tab');
        }
        const order = tabs.map((tab) => tab.id);
        if (new Set(order).size !== order.length) {
            throw new Error('[tabs] deps.tabs names the same id twice');
        }
        if (!order.includes(selected)) {
            throw new Error(`[tabs] deps.selected "${selected}" is not one of the tabs`);
        }
        const unnamed = tabs.find((tab) => tab.icon !== undefined
            && (typeof tab.icon !== 'string' || !tab.icon));
        if (unnamed) throw new Error(`[tabs] tab "${unnamed.id}" has an icon with no name`);
        if (typeof onSelect !== 'function') throw new Error('[tabs] deps.onSelect is required');

        let current = selected;
        /** id -> its button, in `order`. */
        const buttons = new Map();
        const element = h('div', { class: 'tabs', role: 'tablist', 'aria-label': label });

        function select(id) {
            if (!buttons.has(id)) throw new Error(`[tabs] no tab "${id}" in "${label}"`);
            current = id;
            for (const [tabId, button] of buttons) {
                const on = tabId === id;
                button.setAttribute('aria-selected', on ? 'true' : 'false');
                // Roving tabindex: one tab stop for the group, arrows move within.
                button.setAttribute('tabindex', on ? '0' : '-1');
            }
        }

        function onKeydown(event) {
            const at = order.indexOf(current);
            let next = null;
            if (event.key === 'ArrowRight') next = order[(at + 1) % order.length];
            else if (event.key === 'ArrowLeft') next = order[(at - 1 + order.length) % order.length];
            else if (event.key === 'Home') next = order[0];
            else if (event.key === 'End') next = order[order.length - 1];
            // Any other key is not this group's, and is left alone rather than
            // swallowed.
            if (next === null) return;
            event.preventDefault();
            select(next);
            buttons.get(next).focus();
            onSelect(next);
        }

        tabs.forEach((tab) => {
            const button = h('button', {
                class: 'tab',
                type: 'button',
                role: 'tab',
                id: `${idPrefix}-${tab.id}`,
                'aria-controls': tab.panelId,
                // A click on the tab already up changes nothing, so it says
                // nothing: the caller's show-the-pane work is not free.
                onclick: () => {
                    if (tab.id === current) return;
                    select(tab.id);
                    onSelect(tab.id);
                },
                onkeydown: onKeydown,
            },
            // Icon first, then the words, the rail menu's door shape. Without
            // an icon the label is the button's only text, as it always was.
            tab.icon ? [
                h('i', { 'data-lucide': tab.icon, 'aria-hidden': 'true' }),
                h('span', {}, tab.label),
            ] : tab.label);
            buttons.set(tab.id, button);
            element.append(button);
        });
        select(selected);

        return {
            element,
            select,
            selected: () => current,
            focus: () => buttons.get(current).focus(),
        };
    }

    return { create };
})();
