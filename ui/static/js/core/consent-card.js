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

    function cardKind(card) {
        if (!card) return 'host_path';
        return (card.kind || card.card_kind || 'host_path');
    }

    function isWorkspacePreferenceCard(card) {
        return cardKind(card) === 'workspace_preference';
    }

    function isShellExecutorCard(card) {
        return cardKind(card) === 'shell_executor';
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
        const kind = cardKind(card);
        container.dataset.cardKind = kind;
        const status = card.status || 'pending';
        container.classList.toggle('is-resolved', status !== 'pending');
        const title = document.createElement('div');
        title.className = 'hpc-title';
        const workspace = kind === 'workspace_preference';
        const shell = kind === 'shell_executor';
        title.textContent = workspace
            ? (card.title || 'Work in your workspace?')
            : (shell ? (card.title || 'Enable Shell Executor?') : 'Host path consent');
        const pathEl = document.createElement('div');
        pathEl.className = 'hpc-path';
        pathEl.textContent = shell
            ? (card.command || card.path || 'validate-on-clone')
            : (card.path || '');
        const reasonEl = document.createElement('div');
        reasonEl.className = 'hpc-reason';
        reasonEl.textContent = workspace
            ? (card.body || card.reason || "Host paths stay safer if we clone (or branch) into the agent's workspace first. Editing the host folder directly is allowed but not advised.")
            : (shell
                ? (card.body || card.reason || 'Turns on Shell Executor for the company — same as Settings → CLI policy. CLI policy still applies after (not a blanket allow-all). Validate-on-clone needs pytest and local git add/commit on the locked workspace copy.')
                : (card.reason || ''));
        container.appendChild(title);
        container.appendChild(pathEl);
        const reasonText = workspace || shell
            ? (card.body || card.reason || reasonEl.textContent)
            : card.reason;
        if (reasonText && status === 'pending') {
            container.appendChild(reasonEl);
        }

        if (status !== 'pending') {
            const resolved = document.createElement('div');
            resolved.className = 'hpc-status';
            resolved.textContent = consentStatusLabel(card);
            container.appendChild(resolved);
            return;
        }

        const actions = document.createElement('div');
        actions.className = 'host-path-consent-actions';
        const buttons = workspace
            ? workspacePreferenceActions(card)
            : (shell ? shellExecutorActions(card) : [
            { label: 'Allow once', path: 'allow-once' },
            {
                label: 'Always allow (for all agents)',
                path: 'always-allow',
                hidden: card.always_allow === false,
            },
            { label: 'Deny', path: 'deny' },
        ]);
        buttons.forEach((item) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = item.primary ? 'hpc-action hpc-action-primary' : 'hpc-action';
            if (item.muted) btn.classList.add('hpc-action-muted');
            btn.textContent = item.label;
            if (item.hidden) {
                btn.hidden = true;
                btn.disabled = true;
            }
            btn.addEventListener('click', () => decideHostPathConsent(container, card, item.path, actions, api));
            actions.appendChild(btn);
        });
        container.appendChild(actions);
        if (shell) {
            const hintEl = document.createElement('div');
            hintEl.className = 'hpc-hint';
            hintEl.textContent = card.enable_hint || 'same as Settings. CLI policy still applies after.';
            container.appendChild(hintEl);
        }
    }

    function workspacePreferenceActions(card) {
        const git = Boolean(card && (card.git || card.is_git));
        return [
            { label: 'Clone into workspace', path: 'clone', primary: true },
            { label: 'Make a branch', path: 'branch', hidden: !git },
            { label: 'Edit host directly (not advised)', path: 'edit-host', muted: true },
            { label: 'Cancel', path: 'cancel' },
        ];
    }

    function shellExecutorActions(card) {
        return [
            {
                label: (card && card.enable_label) || 'Turn on Shell Executor (company-wide)',
                path: 'enable',
                primary: true,
            },
            {
                label: (card && card.deny_label) || 'Deny — Shell Executor stays off',
                path: 'deny',
            },
        ];
    }

    function consentStatusLabel(card) {
        const status = card.status || 'pending';
        const workspace = isWorkspacePreferenceCard(card);
        if (status === 'denied') {
            if (isShellExecutorCard(card)) {
                return card.decision_note || 'Shell Executor stays off.';
            }
            return card.decision_note || (workspace ? 'Cancelled' : 'Denied');
        }
        if (status === 'enabled') {
            return card.decision_note || 'Shell Executor on (company-wide). CLI policy still applies.';
        }
        if (status === 'cloned') return card.clone_dest ? `Cloned into ${card.clone_dest}` : 'Cloned into workspace';
        if (status === 'branched') return card.decision_note || 'Branched into workspace';
        if (status === 'edit_host') return 'Edit host directly (not advised)';
        if (status === 'always_allowed') return 'Always allowed (for all agents)';
        if (status === 'allowed_once') return 'Allowed once';
        return status;
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
     * @param {'allow-once'|'always-allow'|'deny'|'clone'|'branch'|'edit-host'|'cancel'} action
     * @param {HTMLElement} actions  The action row, disabled while in flight.
     * @param {Function} api  Authenticated fetch helper.
     * @returns {Promise<void>}
     * @throws {Error} When `api` is not a function.
     */
    async function decideHostPathConsent(container, card, action, actions, api) {
        requireApi(api);
        Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const endpoint = isWorkspacePreferenceCard(card)
                ? `/api/workspace-preference/${card.id}/${action}`
                : (isShellExecutorCard(card)
                    ? `/api/shell-executor/${card.id}/${action}`
                    : `/api/host-path-consent/${card.id}/${action}`);
            const res = await api(endpoint, { method: 'POST' });
            if (!res.ok) {
                throw new Error((await res.text()) || 'Consent update failed.');
            }
            const updated = await res.json();
            container.replaceChildren();
            renderHostPathConsentCard(container, updated, api);
            collapseRelatedConsentCards(container, updated);
            collapseGrantedConsentCards(updated);
            BossModIcons.paint(container, 'consent-card');
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
     * Enable from the needs bell never clicks the in-thread card, so
     * `collapseGrantedConsentCards` is the same walk without an origin node:
     * every matching pending shell_executor card in a transcript becomes
     * resolved-only.
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
        collapseConsentCardsInScope(scope, card, container);
    }

    /**
     * Collapse every matching pending card in open transcripts.
     *
     * Used when Enable lands from the bell or an `activity` broadcast rather
     * than a click on the origin card. Deny is not company-wide and must not
     * walk other cards.
     *
     * @param {object} card  `{status, kind, grant_root, decision_note}`.
     * @param {ParentNode} [root]  Query root; defaults to `document`.
     * @returns {void}
     */
    function collapseGrantedConsentCards(card, root) {
        if (!card || (card.status || 'pending') === 'pending') return;
        const companyWide = card.status === 'always_allowed' || card.status === 'enabled';
        if (!companyWide) return;
        const doc = root || (typeof document !== 'undefined' ? document : null);
        if (!doc || typeof doc.querySelectorAll !== 'function') return;
        const scopes = [];
        if (typeof doc.getAttribute === 'function' && doc.getAttribute('data-transcript') !== null) {
            scopes.push(doc);
        }
        doc.querySelectorAll('[data-transcript]').forEach((scope) => scopes.push(scope));
        scopes.forEach((scope) => collapseConsentCardsInScope(scope, card, null));
    }

    function collapseConsentCardsInScope(scope, card, origin) {
        if (!scope || typeof scope.querySelectorAll !== 'function') return;
        const grantRoot = card.grant_root || '';
        const companyWide = card.status === 'always_allowed' || card.status === 'enabled';
        const kind = (origin && origin.dataset.cardKind) || cardKind(card);
        // Deny is per-request for Shell Executor; siblings stay pending.
        if (kind === 'shell_executor' && !companyWide) return;
        scope.querySelectorAll('.host-path-consent-card').forEach((el) => {
            if (el === origin || el.classList.contains('is-resolved')) return;
            const sameKind = (el.dataset.cardKind || 'host_path') === kind;
            if (!sameKind) return;
            const sameRoot = grantRoot && el.dataset.grantRoot === grantRoot;
            if (!companyWide && !sameRoot) return;
            el.classList.add('is-resolved');
            el.querySelector('.host-path-consent-actions')?.remove();
            el.querySelector('.hpc-reason')?.remove();
            el.querySelector('.hpc-hint')?.remove();
            if (!el.querySelector('.hpc-status')) {
                const resolved = document.createElement('div');
                resolved.className = 'hpc-status';
                resolved.textContent = consentStatusLabel(card);
                el.appendChild(resolved);
            }
        });
    }

    return {
        isHostPathConsentMessage,
        isWorkspacePreferenceCard,
        isShellExecutorCard,
        renderHostPathConsentCard,
        decideHostPathConsent,
        collapseRelatedConsentCards,
        collapseGrantedConsentCards,
    };
})();
