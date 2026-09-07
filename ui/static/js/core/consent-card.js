/**
 * BossMod AI — host-path consent cards.
 *
 * An agent that wants to read or write outside its own desk asks first, and
 * the ask is rendered inline in the conversation where it happened rather
 * than in a separate queue: the operator decides with the request's context
 * in front of them. Deciding one card collapses every sibling the decision
 * already answers, so the transcript does not keep asking a settled question.
 *
 * Moved out of utils.js. The one behavioural change is that the fetch helper
 * is now a required argument instead of a global reached for at call time.
 */
const BossModConsentCard = (() => {

    /**
     * Is this backend message a host-path consent request?
     *
     * @param {object|null} message  A raw chat or channel message payload.
     * @returns {boolean}
     */
    function isHostPathConsentMessage(message) {
        return Boolean(
            message
            && (
                message.notification_kind === 'host_path_consent'
                || message.host_path_consent
            )
        );
    }

    function requireApi(api) {
        if (typeof api !== 'function') {
            throw new Error('[consent-card] api is required');
        }
        return api;
    }

    /**
     * Paint one consent card into `container`.
     *
     * Renders the pending form (path, reason, three actions) or the resolved
     * state, depending on `card.status`. The container is mutated in place and
     * gains the `host-path-consent-card` class.
     *
     * @param {HTMLElement} container  The node the card owns.
     * @param {object} card  `{id, path, reason, status, grant_root, decision_note}`.
     * @param {Function} api  Authenticated fetch helper, used when an action is
     *   clicked.
     * @returns {void}  No-op when container or card is missing.
     * @throws {Error} When `api` is not a function — a card that cannot resolve
     *   is worse than no card, so it fails at render rather than on click.
     */
    function renderHostPathConsentCard(container, card, api) {
        requireApi(api);
        if (!container || !card) return;
        container.classList.add('host-path-consent-card');
        if (card.grant_root) container.dataset.grantRoot = card.grant_root;
        const status = card.status || 'pending';
        container.classList.toggle('is-resolved', status !== 'pending');
        const title = document.createElement('div');
        title.className = 'hpc-title';
        title.textContent = 'Host path consent';
        const pathEl = document.createElement('div');
        pathEl.className = 'hpc-path';
        pathEl.textContent = card.path || '';
        const reasonEl = document.createElement('div');
        reasonEl.className = 'hpc-reason';
        reasonEl.textContent = card.reason || '';
        container.appendChild(title);
        container.appendChild(pathEl);
        if (card.reason && status === 'pending') container.appendChild(reasonEl);

        if (status !== 'pending') {
            const resolved = document.createElement('div');
            resolved.className = 'hpc-status';
            resolved.textContent = status === 'denied'
                ? (card.decision_note || 'Denied')
                : (status === 'always_allowed' ? 'Always allowed (for all agents)' : 'Allowed once');
            container.appendChild(resolved);
            return;
        }

        const actions = document.createElement('div');
        actions.className = 'host-path-consent-actions';
        [
            { label: 'Allow once', path: 'allow-once' },
            { label: 'Always allow (for all agents)', path: 'always-allow' },
            { label: 'Deny', path: 'deny' },
        ].forEach((item) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'hpc-action';
            btn.textContent = item.label;
            btn.addEventListener('click', () => decideHostPathConsent(container, card, item.path, actions, api));
            actions.appendChild(btn);
        });
        container.appendChild(actions);
    }

    /**
     * POST one decision and repaint the card with the server's answer.
     *
     * The buttons are disabled for the duration so a double-click cannot send
     * two decisions. A failure re-enables them and appends the reason — the
     * operator must never be left believing they decided something they did not.
     *
     * @param {HTMLElement} container
     * @param {object} card
     * @param {'allow-once'|'always-allow'|'deny'} action
     * @param {HTMLElement} actions  The action row, disabled while in flight.
     * @param {Function} api  Authenticated fetch helper.
     * @returns {Promise<void>}
     * @throws {Error} When `api` is not a function.
     */
    async function decideHostPathConsent(container, card, action, actions, api) {
        requireApi(api);
        Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const res = await api(`/api/host-path-consent/${card.id}/${action}`, { method: 'POST' });
            if (!res.ok) {
                throw new Error((await res.text()) || 'Consent update failed.');
            }
            const updated = await res.json();
            container.replaceChildren();
            renderHostPathConsentCard(container, updated, api);
            collapseRelatedConsentCards(container, updated);
            if (window.lucide) lucide.createIcons({ nodes: [container] });
        } catch (err) {
            Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = false; });
            const note = document.createElement('div');
            note.className = 'hpc-status';
            note.textContent = err?.message || 'Consent update failed.';
            container.appendChild(note);
        }
    }

    /**
     * Collapse sibling cards a decision already answered.
     *
     * "Always allow" answers every pending card in the transcript; a scoped
     * grant answers only cards sharing its `grant_root`. Anything else is left
     * alone, because collapsing an unrelated ask would silently drop a
     * decision the operator still owes.
     *
     * @param {HTMLElement} container  The card that was just decided.
     * @param {object} card  The server's updated card.
     * @returns {void}  No-op while the decision is still pending.
     */
    function collapseRelatedConsentCards(container, card) {
        if (!container || !card || (card.status || 'pending') === 'pending') return;
        // A card rendered outside a transcript (a standalone prompt) still
        // collapses its siblings; the parent element is the documented scope
        // for that case, not a swallowed lookup failure.
        const scope = container.closest('[data-transcript]') || container.parentElement;
        if (!scope) return;
        const grantRoot = card.grant_root || '';
        const companyWide = card.status === 'always_allowed';
        scope.querySelectorAll('.host-path-consent-card').forEach((el) => {
            if (el === container || el.classList.contains('is-resolved')) return;
            const sameRoot = grantRoot && el.dataset.grantRoot === grantRoot;
            if (!companyWide && !sameRoot) return;
            el.classList.add('is-resolved');
            el.querySelector('.host-path-consent-actions')?.remove();
            el.querySelector('.hpc-reason')?.remove();
            if (!el.querySelector('.hpc-status')) {
                const resolved = document.createElement('div');
                resolved.className = 'hpc-status';
                resolved.textContent = card.status === 'denied'
                    ? (card.decision_note || 'Denied')
                    : (card.status === 'always_allowed' ? 'Always allowed (for all agents)' : 'Allowed once');
                el.appendChild(resolved);
            }
        });
    }

    return {
        isHostPathConsentMessage,
        renderHostPathConsentCard,
        decideHostPathConsent,
        collapseRelatedConsentCards,
    };
})();
