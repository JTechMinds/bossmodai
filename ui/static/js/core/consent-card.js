/**
 * BossMod AI — host-path consent and CLI approval cards.
 * Fetch helper is required at render, not a global reached at click time.
 */
const BossModConsentCard = (() => {

    function isHostPathConsentMessage(message) {
        return Boolean(
            message
            && (
                message.notification_kind === 'host_path_consent'
                || message.host_path_consent
            )
        );
    }

    function isCliApprovalMessage(message) {
        return Boolean(
            message
            && (
                message.notification_kind === 'cli_approval'
                || message.cli_approval
            )
        );
    }

    function isCliApprovalCard(card) {
        return cardKind(card) === 'cli_approval';
    }

    function cardFromMessage(message) {
        if (isCliApprovalMessage(message) && message.cli_approval) {
            return message.cli_approval;
        }
        if (isHostPathConsentMessage(message) && message.host_path_consent) {
            return message.host_path_consent;
        }
        return null;
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

    function renderCliApprovalCard(container, card, api) {
        requireApi(api);
        if (!container || !card) return;
        container.classList.add('host-path-consent-card');
        container.dataset.cardKind = 'cli_approval';
        if (card.command) container.dataset.command = card.command;
        const status = card.status || 'pending';
        container.classList.toggle('is-resolved', status !== 'pending');
        const title = document.createElement('div');
        title.className = 'hpc-title';
        title.textContent = card.title || 'Approve this command?';
        const commandEl = document.createElement('div');
        commandEl.className = 'hpc-path';
        commandEl.textContent = card.command || '';
        container.appendChild(title);
        container.appendChild(commandEl);
        if (card.cwd && status === 'pending') {
            const cwdEl = document.createElement('div');
            cwdEl.className = 'hpc-reason';
            cwdEl.textContent = `cwd: ${card.cwd}`;
            container.appendChild(cwdEl);
        }
        if (status !== 'pending') {
            const resolved = document.createElement('div');
            resolved.className = 'hpc-status';
            resolved.textContent = cliApprovalStatusLabel(card);
            container.appendChild(resolved);
            return;
        }
        const actions = document.createElement('div');
        actions.className = 'host-path-consent-actions';
        [
            { label: 'Approve', path: 'approve', primary: true },
            { label: 'Reject', path: 'reject' },
        ].forEach((item) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = item.primary ? 'hpc-action hpc-action-primary' : 'hpc-action';
            btn.textContent = item.label;
            btn.addEventListener('click', () => decideCliApproval(container, card, item.path, actions, api));
            actions.appendChild(btn);
        });
        container.appendChild(actions);
    }

    function cliApprovalStatusLabel(card) {
        const status = card.status || 'pending';
        if (status === 'rejected') return card.decision_note || 'Rejected';
        if (status === 'approved') return 'Approved';
        if (status === 'expired') return 'Expired';
        return status;
    }

    async function decideCliApproval(container, card, action, actions, api) {
        requireApi(api);
        Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const res = await api(`/api/cli-policy/approvals/${card.id}/${action}`, { method: 'POST' });
            if (!res.ok) {
                throw new Error((await res.text()) || 'Approval update failed.');
            }
            const updated = await res.json();
            const next = Object.assign({ kind: 'cli_approval' }, updated, {
                title: updated.title || card.title || 'Approve this command?',
            });
            container.replaceChildren();
            renderCliApprovalCard(container, next, api);
            collapseRelatedConsentCards(container, next);
            BossModIcons.paint(container, 'consent-card');
        } catch (err) {
            Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = false; });
            const note = document.createElement('div');
            note.className = 'hpc-status';
            note.textContent = err?.message || 'Approval update failed.';
            container.appendChild(note);
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

    function collapseRelatedConsentCards(container, card) {
        if (!container || !card || (card.status || 'pending') === 'pending') return;
        const scope = container.closest('[data-transcript]') || container.parentElement;
        if (!scope) return;
        collapseConsentCardsInScope(scope, card, container);
    }

    function collapseGrantedConsentCards(card, root) {
        if (!card || (card.status || 'pending') === 'pending') return;
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
        const kind = (origin && origin.dataset.cardKind) || cardKind(card);
        if (kind === 'cli_approval') {
            const command = card.command || (origin && origin.dataset.command) || '';
            scope.querySelectorAll('.host-path-consent-card').forEach((el) => {
                if (el === origin || el.classList.contains('is-resolved')) return;
                if ((el.dataset.cardKind || '') !== 'cli_approval') return;
                if (command && el.dataset.command && el.dataset.command !== command) return;
                el.classList.add('is-resolved');
                el.querySelector('.host-path-consent-actions')?.remove();
                el.querySelector('.hpc-reason')?.remove();
                if (!el.querySelector('.hpc-status')) {
                    const resolved = document.createElement('div');
                    resolved.className = 'hpc-status';
                    resolved.textContent = cliApprovalStatusLabel(card);
                    el.appendChild(resolved);
                }
            });
            return;
        }
        const grantRoot = card.grant_root || '';
        const companyWide = card.status === 'always_allowed' || card.status === 'enabled';
        // Deny is per-request for Shell Executor; siblings stay pending.
        if (kind === 'shell_executor' && !companyWide) return;
        if (!companyWide && !grantRoot) return;
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
        isCliApprovalMessage,
        isCliApprovalCard,
        cardFromMessage,
        isWorkspacePreferenceCard,
        isShellExecutorCard,
        renderHostPathConsentCard,
        renderCliApprovalCard,
        decideHostPathConsent,
        decideCliApproval,
        collapseRelatedConsentCards,
        collapseGrantedConsentCards,
    };
})();
