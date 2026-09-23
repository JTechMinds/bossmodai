/**
 * BossMod AI — the PEOPLE header's `⋯`, and the "Show roles" preference in it.
 *
 * The People counterpart of shell/thread-view-menu.js, and built the way
 * conversation/system-receipts.js is: one persisted display choice, with the
 * storage kept here. The People half asks `showRoles()` when it renders and
 * never learns where the answer is kept. The `⋯` and its panel are
 * shell/roster-header-menu.js's, so the rail's two menus behave as one.
 *
 * The role is `agent.role` — the same field the rail's search box already
 * matches on ("Search by name or role") — which is why the switch says
 * "roles": it names the thing the search already names.
 *
 * HIDDEN BY DEFAULT. Hidden is today's look, and the role is an optional
 * decoration on a row that already reads without it, so failing to show it —
 * a first visit, or site data the browser will not read — is the harmless
 * direction. system-receipts.js defaults the other way for the opposite
 * reason: a receipt it failed to show would be one the operator needed.
 *
 * The panel's one action is "On vacation": agents on vacation are hidden from
 * the rail, so the People menu is where they are found again. It opens
 * shell/vacation-dialog.js.
 */
const BossModPeopleViewMenu = (() => {
    const { h } = BossModDom;

    const STORAGE_KEY = 'bossmod.roster.showRoles';
    /** The `⋯`'s accessible name, its tooltip and its panel's name: one string. */
    const MENU_LABEL = 'People list options';
    const SWITCH_LABEL = 'Show roles';

    /**
     * Read the preference.
     *
     * A browser that blocks site data is a known, documented condition rather
     * than a failure to swallow: it is logged and the default (hidden) wins,
     * because that is the look the rail had before the preference existed.
     *
     * @returns {boolean}
     */
    function read() {
        try {
            return window.localStorage.getItem(STORAGE_KEY) === 'true';
        } catch (err) {
            console.warn('[people-view-menu] site data is unreadable; roles stay hidden', err);
            return false;
        }
    }

    /**
     * Persist the preference.
     * @param {boolean} value
     * @returns {void}
     */
    function write(value) {
        try {
            window.localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false');
        } catch (err) {
            console.warn('[people-view-menu] could not persist the choice', err);
        }
    }

    /**
     * Build the People header's `⋯` and the switch behind it.
     *
     * The switch is the shared core/switch.js control, and it goes into the
     * panel bare — the way conversation/chrome.js mounts the receipts switch —
     * because a panel of one preference needs no caption above it.
     *
     * @param {object} deps
     * @param {() => HTMLElement} deps.getContainer  The PEOPLE header row the
     *   panel hangs off. A thunk, because that row cannot be built until the
     *   `⋯` exists to go in it.
     * @param {(showRoles: boolean) => void} deps.onChange  Called after the new
     *   value has been persisted, so a repaint sees the stored truth.
     * @param {object} deps.store  Handed to the On vacation view, which brings
     *   an agent back to the floor the operator is on.
     * @param {Function} deps.apiFetch  The shell's authenticated fetch, for
     *   the On vacation view's requests.
     * @returns {{ button: HTMLElement, showRoles: () => boolean,
     *             close: () => void, destroy: () => void }}
     * @throws {Error} When any dependency is missing — a switch nothing
     *   listens to, a panel with nowhere to hang, or an On vacation row that
     *   cannot load anyone would render and then do nothing.
     */
    function createPeopleViewMenu(deps) {
        const { getContainer, onChange, store, apiFetch } = deps || {};
        if (typeof getContainer !== 'function') {
            throw new Error('[people-view-menu] deps.getContainer is required');
        }
        if (typeof onChange !== 'function') {
            throw new Error('[people-view-menu] deps.onChange is required');
        }
        if (!store) throw new Error('[people-view-menu] deps.store is required');
        if (typeof apiFetch !== 'function') {
            throw new Error('[people-view-menu] deps.apiFetch is required');
        }

        let showRoles = read();

        const control = BossModSwitch.create({
            label: SWITCH_LABEL,
            pressed: showRoles,
            onChange: (next) => {
                showRoles = next;
                write(next);
                onChange(next);
            },
        });

        // Closed FIRST, so the dialog captures the `⋯` as where focus returns.
        const vacation = h('button', {
            class: 'menu-action',
            type: 'button',
            onclick: () => {
                menu.close();
                BossModVacationDialog.open({
                    store,
                    floorApi: BossModFloorApi.createFloorApi({ apiFetch }),
                });
            },
        },
            h('i', { 'data-lucide': 'tree-palm', 'aria-hidden': 'true' }),
            h('span', {}, 'On vacation'));

        const menu = BossModRosterHeaderMenu.createHeaderMenu({
            id: 'roster-people-view',
            label: MENU_LABEL,
            menuName: 'people-view',
            getContainer,
            items: [control.element, h('div', { class: 'menu-actions' }, vacation)],
        });

        return {
            button: menu.button,
            showRoles: () => showRoles,
            close: menu.close,
            destroy: menu.destroy,
        };
    }

    return { createPeopleViewMenu };
})();
