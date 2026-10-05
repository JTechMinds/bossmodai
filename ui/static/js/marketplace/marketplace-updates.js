/**
 * BossMod AI — catalog updates for what is installed: the banner over the
 * marketplace grid and the review layer behind its two buttons.
 *
 * The catalog is read at a pinned commit, so nothing changes under the
 * operator until the pin moves. This asks the server once per marketplace
 * open whether the catalog's HEAD has moved past the pin and what that would
 * change (`checkUpdates` in context/agent-templates-api.js, which writes
 * nothing), and offers
 * the move:
 *
 *   Update all packs           re-installs every changed pack and moves the pin
 *   Update all packs + agents  also rewrites the agents hired from those packs
 *
 * NOTHING IS WRITTEN WITHOUT A REVIEW, except one case. Either button opens a
 * layer (core/overlays.js) listing every pack, every agent — with "edited —
 * will be overwritten" on an agent whose contract the operator changed — and
 * every pack that will be skipped and why. Confirm sends back the exact
 * `target_sha` that was reviewed. The one exception is a QUIET ADVANCE: when
 * HEAD moved and nothing installed changed, nothing of the operator's is at
 * stake, so the pin moves without a banner and new catalog packs appear.
 *
 * Built with BossModDom.h: pack titles, agent names and the server's skip
 * messages are remote or operator text and never reach a markup string.
 */
const BossModMarketplaceUpdates = (() => {
    const { h, clear } = BossModDom;

    const COPY = Object.freeze({
        checking: 'Checking the catalog for updates…',
        checkFailed: 'Couldn’t check the catalog for updates.',
        retry: 'Try again',
        updatePacks: 'Update all packs',
        updateAll: 'Update all packs + agents',
        updating: 'Updating…',
        confirm: 'Update',
        cancel: 'Cancel',
        packsHead: 'Packs',
        agentsHead: 'Agents',
        skippedHead: 'Skipped',
        edited: 'edited — will be overwritten',
        fields: 'Each agent’s description, done bar and communication are replaced. '
            + 'Name, specialty, colour, AI connection and desk are kept.',
        unknownSha: 'unknown',
    });

    // The server's whole vocabulary for `skipped[].kind`. An unknown kind is a
    // contract break and throws, rather than being shown under a guessed label.
    const KIND_LABEL = Object.freeze({
        refused: 'Refused by this app',
        unavailable: 'Could not be read',
        removed: 'No longer in the catalog',
        not_installed: 'Not installed',
    });

    /** `3 packs`, `1 agent`. */
    function count(n, noun) {
        return `${n} ${noun}${n === 1 ? '' : 's'}`;
    }

    /** `8a0d68a → 9d2352e`; a row with no recorded commit says so. */
    function fromTo(from, to) {
        return `${from || COPY.unknownSha} → ${to}`;
    }

    /**
     * The banner's one line.
     *
     * @param {object} plan  What `checkUpdates` answered.
     * @returns {string} `Catalog update <pinned> → <target> · N packs · M agents`.
     */
    function summary(plan) {
        return `Catalog update ${fromTo(plan.pinned_short, plan.target_short)} · `
            + `${count(plan.templates.length, 'pack')} · ${count(plan.agents.length, 'agent')}`;
    }

    /** One reviewed row: a name, its facts, an optional warning chip and line. */
    function row(name, meta, chip, message) {
        return h('li', { class: 'market-review-row' },
            h('span', { class: 'market-review-name' }, name),
            h('span', { class: 'market-review-meta' }, meta),
            chip ? h('span', { class: 'market-review-chip' }, chip) : null,
            message ? h('span', { class: 'market-review-message' }, message) : null);
    }

    /** A titled list, or null when it has nothing in it. */
    function group(title, rows, lead) {
        if (!rows.length && !lead) return null;
        return h('section', { class: 'market-review-group' },
            h('h3', { class: 'market-review-head' }, title),
            lead ? h('p', { class: 'market-review-note' }, lead) : null,
            rows.length ? h('ul', { class: 'market-review-list' }, rows) : null);
    }

    /**
     * What the review layer lists before anything is written.
     *
     * @param {object} plan  The previewed plan.
     * @param {boolean} includeAgents  The "+ agents" variant.
     * @returns {HTMLElement}
     * @throws {Error} On a skipped `kind` this build does not know.
     */
    function reviewBody(plan, includeAgents) {
        const packs = plan.templates.map((tpl) => row(tpl.title, fromTo(tpl.from_short, tpl.to_short)));
        const agents = includeAgents
            ? group(COPY.agentsHead, plan.agents.map((agent) => row(
                agent.name,
                `${agent.template_title} · ${fromTo(agent.from_short, agent.to_short)}`,
                agent.edited ? COPY.edited : null,
            )), plan.agents.length ? COPY.fields : null)
            : (plan.agents.length
                ? h('p', { class: 'market-review-note' },
                    `${count(plan.agents.length, 'agent')} will show an update on their desk.`)
                : null);
        const skipped = plan.skipped.map((pack) => {
            const label = KIND_LABEL[pack.kind];
            if (!label) throw new Error(`[marketplace-updates] unknown skipped kind "${pack.kind}"`);
            return row(pack.title, label, null, pack.message);
        });
        return h('div', { class: 'market-review' },
            h('p', { class: 'market-review-note' },
                `Catalog ${fromTo(plan.pinned_short, plan.target_short)}. Nothing changes until you confirm.`),
            group(COPY.packsHead, packs),
            agents,
            group(COPY.skippedHead, skipped));
    }

    /**
     * Build the updates banner.
     *
     * @param {object} deps
     * @param {{checkUpdates: () => Promise<object>,
     *   applyUpdates: (body: {target_sha: string, include_agents: boolean}) => Promise<object>}} deps.api
     *   context/agent-templates-api.js, or a double.
     * @param {() => void} deps.onApplied  Called after any apply landed —
     *   reviewed or quiet — so the marketplace re-reads the catalog at the new
     *   pin and the library it rewrote. Never after a failure.
     * @returns {{element: HTMLElement, refresh: () => Promise<void>, destroy: () => void}}
     *   `element` is hidden while there is nothing to say. `refresh` checks
     *   the catalog again and never rejects: a failure is the banner's error
     *   state, with a retry.
     * @throws {Error} When a dependency is missing.
     */
    function create(deps) {
        const { api, onApplied } = deps || {};
        if (!api || typeof api.checkUpdates !== 'function' || typeof api.applyUpdates !== 'function') {
            throw new Error('[marketplace-updates] deps.api needs checkUpdates and applyUpdates');
        }
        if (typeof onApplied !== 'function') throw new Error('[marketplace-updates] deps.onApplied is required');

        const element = h('div', { class: 'market-updates', id: 'market-updates' });
        const loads = BossModGates.createLoadGeneration();
        /** idle | checking | error | review | applying | done */
        let status = 'idle';
        let plan = null;
        let message = '';
        /** Which button is applying, so only its label says so. */
        let applyingAgents = false;
        let layer = null;
        let destroyed = false;

        function button(id, label, onclick, primary) {
            return h('button', {
                class: primary ? 'market-action primary' : 'market-action',
                id, type: 'button', disabled: status === 'applying', onclick,
            }, label);
        }

        function render() {
            clear(element);
            element.hidden = status === 'idle';
            element.removeAttribute('aria-busy');
            if (status === 'checking') {
                element.append(h('p', { class: 'market-updates-status', role: 'status' }, COPY.checking));
            } else if (status === 'error') {
                element.append(h('div', { class: 'callout', 'data-tone': 'alert', role: 'alert' },
                    h('p', { class: 'callout-body' }, message || COPY.checkFailed),
                    h('div', { class: 'callout-actions' },
                        button('market-updates-retry', COPY.retry, () => { void refresh(); }))));
            } else if (status === 'done') {
                element.append(h('p', { class: 'market-notice', role: 'status' }, message));
            } else if (status === 'review' || status === 'applying') {
                if (status === 'applying') element.setAttribute('aria-busy', 'true');
                element.append(h('div', { class: 'callout market-updates-banner', 'data-tone': 'info' },
                    h('p', { class: 'callout-title' }, summary(plan)),
                    h('div', { class: 'callout-actions' },
                        button('market-updates-packs',
                            status === 'applying' && !applyingAgents ? COPY.updating : COPY.updatePacks,
                            () => openReview(false), false),
                        button('market-updates-all',
                            status === 'applying' && applyingAgents ? COPY.updating : COPY.updateAll,
                            () => openReview(true), true))));
            }
        }

        function set(next, text) {
            status = next;
            message = text || '';
            if (!destroyed) render();
        }

        /**
         * Apply the plan on screen. The caller has reviewed it, or it is a
         * quiet advance with nothing of the operator's to review.
         * @param {boolean} includeAgents
         * @param {boolean} quiet  No banner, no success line.
         * @returns {Promise<void>} Never rejects.
         */
        async function apply(includeAgents, quiet) {
            const target = plan.target_sha;
            applyingAgents = includeAgents;
            set(quiet ? 'idle' : 'applying');
            let applied;
            try {
                applied = await api.applyUpdates({ target_sha: target, include_agents: includeAgents });
            } catch (err) {
                console.error('[marketplace-updates] the update could not be applied', err);
                if (!destroyed) set('error', (err && err.message) || COPY.checkFailed);
                return;
            }
            if (destroyed) return;
            plan = null;
            if (quiet) set('idle');
            else {
                set('done', `Updated ${count(applied.templates.length, 'pack')}, `
                    + `${count(applied.agents.length, 'agent')}.`);
            }
            onApplied();
        }

        function openReview(includeAgents) {
            if (layer || status !== 'review') return;
            layer = BossModOverlays.createModal({
                title: includeAgents ? COPY.updateAll : COPY.updatePacks,
                body: reviewBody(plan, includeAgents),
                closeOnBackdrop: true,
                actions: [
                    { label: COPY.confirm, tone: 'primary', id: 'market-updates-confirm',
                        onSelect: () => { void apply(includeAgents, false); } },
                    { label: COPY.cancel, tone: 'quiet', id: 'market-updates-cancel' },
                ],
                onClose: () => { layer = null; },
            });
        }

        /**
         * Ask the server what a move to the catalog's HEAD would change.
         * @returns {Promise<void>} Never rejects.
         */
        async function refresh() {
            const loadId = loads.next();
            set('checking');
            let next;
            try {
                next = await api.checkUpdates();
            } catch (err) {
                if (destroyed || !loads.isCurrent(loadId)) return;
                console.error('[marketplace-updates] the catalog could not be checked', err);
                set('error', (err && err.message) || COPY.checkFailed);
                return;
            }
            if (destroyed || !loads.isCurrent(loadId)) return;
            plan = next;
            if (plan.needs_review) set('review');
            else if (plan.pin_moves) await apply(false, true);
            else set('idle');
        }

        render();
        return {
            element,
            refresh,
            /** Stop painting, drop an in-flight check, and close an open review. */
            destroy() {
                destroyed = true;
                loads.next();
                if (layer) layer.close();
            },
        };
    }

    return { COPY, KIND_LABEL, summary, reviewBody, create };
})();
