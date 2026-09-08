/**
 * BossMod AI — application header.
 *
 * Brand, company name, the six-place nav, the needs bell, Pause, and the
 * Settings gear. Everything it needs arrives in `deps`; it reaches for no
 * application global, so a missing collaborator fails at mount instead of
 * quietly no-opping.
 *
 * Pause is deliberately a click plus a dialog, never a press-and-hold. A hold
 * has no keyboard equivalent (WCAG 2.2 SC 2.1.1) and this is the emergency
 * stop — the one control that must work for every operator by every input
 * method. The guard stays because pausing cancels in-flight turns and loses
 * that work; the artificial delay goes, because when someone reaches for a
 * kill switch they want it now. Resume is a single click with no dialog.
 */
const BossModHeader = (() => {
    const { h, clear } = BossModDom;

    const PAUSE_DIALOG_TITLE = 'Pause everyone?';
    const PAUSE_DIALOG_BODY =
        'Every agent stops taking turns. Turns already running are cancelled and '
        + 'that work is lost. Nothing restarts until you resume.';

    /**
     * The bell's accessible name. It states the count because the badge is
     * decorative — a screen reader user gets the number from here or nowhere.
     *
     * @param {number} count
     * @returns {string}
     */
    function needsLabel(count) {
        if (count === 0) return 'Nothing needs you';
        if (count === 1) return '1 thing needs you';
        return `${count} things need you`;
    }

    /**
     * Render the header into `el`.
     *
     * @param {HTMLElement} el
     * @param {object} deps
     * @param {object} deps.store            Application store.
     * @param {Function} deps.apiFetch       Authenticated request helper.
     * @param {(placeId: string) => void} deps.navigate
     * @param {object} deps.needs            From BossModNeeds.createNeedsStore;
     *   the popover resolves through it. The bell is the one guaranteed path to
     *   the queue, so it is never suppressible (spec 5.5).
     * @param {() => void} deps.openSettings Opens the Settings takeover.
     * @returns {() => void} disposer — drains every subscription.
     * @throws {Error} When deps.needs is missing — a bell with no queue behind
     *   it would report "nothing needs you" forever.
     */
    function mount(el, deps) {
        const { store, apiFetch, navigate, needs, openSettings } = deps;
        if (!needs) throw new Error('[header] deps.needs is required');
        const disposers = [];

        clear(el);

        // ─── Rail toggle ───
        //
        // A <button>, so Enter and Space work with no key handling of its own
        // (SC 2.1.1) — the rail must never be something only a pointer can
        // reach. The state lives in the store rather than here because
        // shell/session.js persists it across reloads.

        const railToggle = h('button', {
            class: 'header-icon-btn header-rail-toggle',
            type: 'button',
            'aria-label': 'Toggle sidebar',
            'aria-expanded': 'true',
            onclick: () => {
                store.setState({ railCollapsed: !store.getState().railCollapsed });
            },
        }, h('i', { 'data-lucide': 'menu', 'aria-hidden': 'true' }));

        function applyRail(collapsed) {
            railToggle.setAttribute('aria-expanded', collapsed === true ? 'false' : 'true');
        }

        // ─── Brand ───

        const companyEl = h('span', { class: 'brand-company' });
        const brand = h('div', { class: 'brand' },
            h('i', { 'data-lucide': 'bot', class: 'brand-mark', 'aria-hidden': 'true' }),
            h('span', { class: 'brand-name' }, 'BossMod'),
            companyEl);

        // ─── Place nav ───

        const navButtons = new Map();
        const nav = h('nav', { class: 'place-nav', 'aria-label': 'Places' });
        BossModPlaces.PLACE_IDS.forEach((id) => {
            const place = BossModPlaces.get(id);
            const button = h('button', {
                class: 'place-nav-item',
                type: 'button',
                'data-place': id,
                onclick: () => navigate(id),
            },
                h('i', { 'data-lucide': place.icon, 'aria-hidden': 'true' }),
                h('span', { class: 'place-nav-label' }, place.label));
            navButtons.set(id, button);
            nav.append(button);
        });

        function applyActivePlace(placeId) {
            navButtons.forEach((button, id) => {
                if (id === placeId) button.setAttribute('aria-current', 'page');
                else button.removeAttribute('aria-current');
            });
        }

        // ─── Needs bell ───

        const bell = h('button', {
            class: 'header-icon-btn header-bell',
            type: 'button',
            onclick: openNeeds,
        });
        // The count is announced once, politely, from here — never from the
        // badge, which is decorative, and never per need.
        const bellLive = h('span', { class: 'visually-hidden', 'aria-live': 'polite' });

        function applyNeeds(open) {
            const count = open.length;
            const label = needsLabel(count);
            bell.setAttribute('aria-label', label);
            clear(bell);
            bell.append(h('i', { 'data-lucide': 'bell', 'aria-hidden': 'true' }));
            if (count > 0) {
                bell.append(h('span', { class: 'bell-badge', 'aria-hidden': 'true' }, String(count)));
            }
            clear(bellLive);
            bellLive.append(label);
            paintIcons();
        }

        /**
         * Toggle the needs popover.
         *
         * One entry point, so a second click on the bell closes what the first
         * opened rather than stacking a second panel on top of it.
         */
        let popover = null;

        function openNeeds() {
            if (popover) {
                popover.close();
                return;
            }
            popover = BossModNeedsPopover.openPopover({
                store,
                needs,
                anchor: bell,
                navigate,
                onClose: () => { popover = null; },
            });
        }

        // ─── Pause / Resume ───

        const errorEl = h('span', { class: 'header-error', role: 'alert' });
        const pause = h('button', { class: 'header-pause', type: 'button', onclick: onPauseClick });

        function applyPaused(paused) {
            pause.setAttribute('aria-label', paused ? 'Resume the AI runtime' : 'Pause the AI runtime');
            clear(pause);
            pause.append(
                h('i', { 'data-lucide': paused ? 'play' : 'octagon-x', 'aria-hidden': 'true' }),
                h('span', { class: 'header-pause-label' }, paused ? 'Resume' : 'Pause'));
            paintIcons();
        }

        function onPauseClick() {
            if (store.getState().runtimePaused) {
                // Resuming loses nothing, so it does not ask.
                void setPaused(false);
                return;
            }
            BossModOverlays.createModal({
                title: PAUSE_DIALOG_TITLE,
                body: PAUSE_DIALOG_BODY,
                actions: [
                    { label: 'Pause everyone', tone: 'danger', onSelect: () => { void setPaused(true); } },
                    { label: 'Cancel', tone: 'quiet' },
                ],
            });
        }

        /**
         * Apply a runtime pause state. The response is written straight to the
         * store so the header is right even when the socket is down; the
         * runtime_state broadcast writes the same value when it arrives.
         *
         * @param {boolean} paused
         */
        async function setPaused(paused) {
            clear(errorEl);
            let response;
            try {
                response = await apiFetch('/api/runtime/state', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paused }),
                });
            } catch (err) {
                reportRuntimeFailure(paused, err);
                return;
            }
            if (!response.ok) {
                reportRuntimeFailure(paused, new Error(`HTTP ${response.status}`));
                return;
            }
            const payload = await response.json();
            store.setState({ runtimePaused: !!payload.paused });
        }

        function reportRuntimeFailure(paused, err) {
            const what = paused ? 'pause' : 'resume';
            console.error(`[header] could not ${what} the runtime`, err);
            clear(errorEl);
            errorEl.append(`Could not ${what} the runtime. Try again.`);
        }

        // ─── Settings ───

        const gear = h('button', {
            class: 'header-icon-btn header-gear',
            type: 'button',
            // A toggle: the gear both opens and closes the Settings takeover,
            // so its name stays neutral rather than lying in one direction.
            'aria-label': 'Settings',
            onclick: () => openSettings(),
        }, h('i', { 'data-lucide': 'settings', 'aria-hidden': 'true' }));

        el.append(railToggle, brand, nav, h('div', { class: 'header-actions' },
            errorEl, bellLive, bell, pause, gear));

        function paintIcons() {
            lucide.createIcons();
        }

        // ─── Company name ───

        /**
         * `company_name` is an optional display setting: when the operator has
         * not set one, the header shows the product brand alone. A failed
         * request is a different thing from an unset value and is reported.
         */
        async function loadCompanyName() {
            let response;
            try {
                response = await apiFetch('/api/settings');
            } catch (err) {
                console.error('[header] could not load settings for the company name', err);
                return;
            }
            if (!response.ok) {
                console.error(`[header] settings request failed: HTTP ${response.status}`);
                return;
            }
            const settings = await response.json();
            const row = settings.find((item) => item && item.key === 'company_name');
            const name = row ? String(row.value).trim() : '';
            clear(companyEl);
            if (name) companyEl.append(`· ${name}`);
        }

        const state = store.getState();
        applyActivePlace(state.place);
        applyNeeds(state.needs);
        applyPaused(state.runtimePaused);
        applyRail(state.railCollapsed);
        paintIcons();
        void loadCompanyName();

        disposers.push(store.subscribe((s) => s.place, applyActivePlace));
        disposers.push(store.subscribe((s) => s.needs, applyNeeds));
        disposers.push(store.subscribe((s) => s.runtimePaused, applyPaused));
        // Subscribed rather than set only on click: boot restores the persisted
        // rail state AFTER the header mounts, and a toggle that announces
        // "expanded" beside a collapsed rail is worse than one with no state.
        disposers.push(store.subscribe((s) => s.railCollapsed, applyRail));

        return () => { disposers.splice(0).forEach((off) => off()); };
    }

    return { mount };
})();
