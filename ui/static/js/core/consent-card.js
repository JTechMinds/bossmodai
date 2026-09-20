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

    function isNestGitCard(card) {
        return typeof BossModNestGitCard !== 'undefined' && BossModNestGitCard.isNestGitCard(card);
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
        const nest = kind === 'nest_git';
        title.textContent = workspace
            ? (card.title || 'Work in your workspace?')
            : (shell ? (card.title || 'Enable Shell Executor?')
                : (nest ? BossModNestGitCard.title(card) : 'Host path consent'));
        const pathEl = document.createElement('div');
        pathEl.className = 'hpc-path';
        pathEl.textContent = (shell || nest)
            ? (card.command || card.path || (nest ? 'nest git' : 'validate-on-clone'))
            : (card.path || '');
        const reasonEl = document.createElement('div');
        reasonEl.className = 'hpc-reason';
        reasonEl.textContent = workspace
            ? (card.body || card.reason || "Host paths stay safer if we clone (or branch) into the agent's workspace first. Editing the host folder directly is allowed but not advised.")
            : (shell
                ? (card.body || card.reason || 'Turns on Shell Executor for the company — same as Settings → CLI policy. CLI policy still applies after (not a blanket allow-all). Validate-on-clone needs pytest and local git add/commit on the locked workspace copy.')
                : (nest ? BossModNestGitCard.body(card) : (card.reason || '')));
        container.appendChild(title);
        container.appendChild(pathEl);
        const reasonText = workspace || shell || nest
            ? (card.body || card.reason || reasonEl.textContent)
            : card.reason;
        if (reasonText && status === 'pending') {
            container.appendChild(reasonEl);
        }
        if (nest && status === 'pending') {
            const bounce = BossModNestGitCard.errorText(card);
            if (bounce) {
                const errEl = document.createElement('div');
                errEl.className = 'hpc-status';
                errEl.textContent = bounce;
                container.appendChild(errEl);
            }
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
            : (nest ? BossModNestGitCard.actions(card) : (shell ? shellExecutorActions(card) : [
            { label: 'Allow once', path: 'allow-once' },
            {
                label: 'Always allow (for all agents)',
                path: 'always-allow',
                hidden: card.always_allow === false,
            },
            { label: 'Deny', path: 'deny' },
        ]));
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
            btn.addEventListener('click', () => decideHostPathConsent(container, card, item.path, actions, api, item));
            actions.appendChild(btn);
        });
        container.appendChild(actions);
        if (shell || nest) {
            const hintEl = document.createElement('div');
            hintEl.className = 'hpc-hint';
            hintEl.textContent = nest
                ? BossModNestGitCard.hint(card)
                : (card.enable_hint || 'same as Settings. CLI policy still applies after.');
            container.appendChild(hintEl);
        }
    }

    function renderCliApprovalCard(container, card, api) {
        if (!isGoneApprovalCard(card)) requireApi(api);
        if (!container || !card) return;
        container.classList.add('host-path-consent-card');
        container.dataset.cardKind = 'cli_approval';
        if (card.command) container.dataset.command = card.command;
        if (card.cwd) container.dataset.cwd = card.cwd;
        const status = card.status || 'pending';
        const gone = isGoneApprovalCard(card);
        container.classList.toggle('is-resolved', status !== 'pending' || gone);
        const title = document.createElement('div');
        title.className = 'hpc-title';
        title.textContent = card.title || 'Approve this command?';
        const commandEl = document.createElement('div');
        commandEl.className = 'hpc-path';
        commandEl.textContent = card.command || '';
        container.appendChild(title);
        container.appendChild(commandEl);
        if (card.cwd && status === 'pending' && !gone) {
            const cwdEl = document.createElement('div');
            cwdEl.className = 'hpc-reason';
            cwdEl.textContent = `cwd: ${card.cwd}`;
            container.appendChild(cwdEl);
        }
        if (gone) {
            appendGoneApprovalChrome(container, card, api);
            return;
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
            { label: 'Always allow', path: 'always-allow', hidden: card.always_allow === false || card.always_allow == null },
            { label: 'Reject', path: 'reject' },
        ].forEach((item) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = item.primary ? 'hpc-action hpc-action-primary' : 'hpc-action';
            btn.textContent = item.label;
            if (item.hidden) {
                btn.hidden = true;
                btn.disabled = true;
            }
            btn.addEventListener('click', () => decideCliApproval(container, card, item.path, actions, api));
            actions.appendChild(btn);
        });
        container.appendChild(actions);
    }

    function isGoneApprovalCard(card) {
        const status = (card && card.status) || '';
        return status === 'gone' || status === 'already_resolved';
    }

    function goneApprovalCard(card) {
        return Object.assign({ kind: 'cli_approval' }, card || {}, {
            status: 'gone',
            always_allow: false,
            decision_note: (card && card.decision_note)
                || 'This approval is gone or already resolved.',
        });
    }

    function isGoneApprovalResponse(res, bodyText) {
        if (res && res.status === 404) return true;
        return /not found or already resolved/i.test(String(bodyText || ''));
    }

    function appendGoneApprovalChrome(container, card, api) {
        const resolved = document.createElement('div');
        resolved.className = 'hpc-status';
        resolved.textContent = cliApprovalStatusLabel(goneApprovalCard(card));
        container.appendChild(resolved);
        const actions = document.createElement('div');
        actions.className = 'host-path-consent-actions';
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'hpc-action';
        dismiss.textContent = 'Dismiss';
        dismiss.addEventListener('click', () => dismissCliApprovalChrome(container, card, api));
        actions.appendChild(dismiss);
        container.appendChild(actions);
    }

    function dismissCliApprovalChrome(container, card, api) {
        const gone = goneApprovalCard(card);
        hideApprovalChrome(container, gone);
        collapseRelatedConsentCards(container, gone);
        if (typeof BossModIcons !== 'undefined') BossModIcons.paint(container, 'consent-card');
    }

    function hideApprovalChrome(origin, card) {
        const scope = (origin && (origin.closest('[data-transcript]') || origin.parentElement)) || origin;
        if (!scope || typeof scope.querySelectorAll !== 'function') {
            if (origin) origin.hidden = true;
            return;
        }
        const command = (card && card.command) || (origin && origin.dataset.command) || '';
        const cwd = (card && card.cwd) || (origin && origin.dataset.cwd) || '';
        scope.querySelectorAll('.host-path-consent-card').forEach((el) => {
            if ((el.dataset.cardKind || '') !== 'cli_approval') return;
            if (command && el.dataset.command && el.dataset.command !== command) return;
            if (cwd && el.dataset.cwd && el.dataset.cwd !== cwd) return;
            el.hidden = true;
            el.classList.add('is-resolved');
            el.querySelector('.host-path-consent-actions')?.remove();
        });
    }

    function cliApprovalStatusLabel(card) {
        const status = card.status || 'pending';
        if (status === 'gone' || status === 'already_resolved') {
            return card.decision_note || 'This approval is gone or already resolved.';
        }
        if (status === 'rejected') return card.decision_note || 'Rejected';
        if (status === 'always_allowed') return card.decision_note || 'Always allowed';
        if (status === 'approved') return card.decision_note || 'Approved';
        if (status === 'expired') return 'Expired';
        return status;
    }

    async function decideCliApproval(container, card, action, actions, api) {
        requireApi(api);
        Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const res = await api(`/api/cli-policy/approvals/${card.id}/${action}`, { method: 'POST' });
            const bodyText = await res.text();
            if (!res.ok) {
                if (isGoneApprovalResponse(res, bodyText)) {
                    morphGoneApprovalChrome(container, card, api);
                    return;
                }
                throw new Error(bodyText || 'Approval update failed.');
            }
            const updated = bodyText ? JSON.parse(bodyText) : {};
            const next = Object.assign({ kind: 'cli_approval' }, updated, {
                title: updated.title || card.title || 'Approve this command?',
                command: updated.command || card.command,
                cwd: updated.cwd || card.cwd,
            });
            if (action === 'always-allow') {
                next.status = next.status === 'approved' ? 'always_allowed' : (next.status || 'always_allowed');
                next.decision_note = next.decision_note || 'Always allowed';
            }
            container.replaceChildren();
            renderCliApprovalCard(container, next, api);
            collapseRelatedConsentCards(container, next);
            BossModIcons.paint(container, 'consent-card');
        } catch (err) {
            if (isGoneApprovalResponse(null, err?.message)) {
                morphGoneApprovalChrome(container, card, api);
                return;
            }
            Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = false; });
            const note = document.createElement('div');
            note.className = 'hpc-status';
            note.textContent = err?.message || 'Approval update failed.';
            container.appendChild(note);
        }
    }

    function morphGoneApprovalChrome(container, card, api) {
        const gone = goneApprovalCard(card);
        container.replaceChildren();
        renderCliApprovalCard(container, gone, api);
        collapseRelatedConsentCards(container, gone);
        if (typeof BossModIcons !== 'undefined') BossModIcons.paint(container, 'consent-card');
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
            if (isNestGitCard(card)) return BossModNestGitCard.statusLabel(card);
            return card.decision_note || 'Shell Executor on (company-wide). CLI policy still applies.';
        }
        if (status === 'cloned') return card.clone_dest ? `Cloned into ${card.clone_dest}` : 'Cloned into workspace';
        if (status === 'branched') return card.decision_note || 'Branched into workspace';
        if (status === 'edit_host') return 'Edit host directly (not advised)';
        if (status === 'always_allowed') return 'Always allowed (for all agents)';
        if (status === 'allowed_once') return 'Allowed once';
        return status;
    }

    async function decideHostPathConsent(container, card, action, actions, api, item) {
        requireApi(api);
        if (isNestGitCard(card) && action === 'credentials') {
            BossModNestGitCard.showCredentialsForm(container, card, actions, api, (updated) => {
                container.replaceChildren();
                renderHostPathConsentCard(container, updated, api);
                collapseRelatedConsentCards(container, updated);
                collapseGrantedConsentCards(updated);
                if (typeof BossModIcons !== 'undefined') BossModIcons.paint(container, 'consent-card');
            });
            return;
        }
        if (isNestGitCard(card) && action === 'use') {
            await BossModNestGitCard.useSaved(container, card, actions, api, item, (updated) => {
                container.replaceChildren();
                renderHostPathConsentCard(container, updated, api);
                collapseRelatedConsentCards(container, updated);
                collapseGrantedConsentCards(updated);
                if (typeof BossModIcons !== 'undefined') BossModIcons.paint(container, 'consent-card');
            });
            return;
        }
        Array.from(actions.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const endpoint = isWorkspacePreferenceCard(card)
                ? `/api/workspace-preference/${card.id}/${action}`
                : (isNestGitCard(card)
                    ? `/api/nest-git/${card.id}/${action}`
                    : (isShellExecutorCard(card)
                        ? `/api/shell-executor/${card.id}/${action}`
                        : `/api/host-path-consent/${card.id}/${action}`));
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
            const cwd = card.cwd || (origin && origin.dataset.cwd) || '';
            const gone = isGoneApprovalCard(card);
            scope.querySelectorAll('.host-path-consent-card').forEach((el) => {
                if (el === origin || el.classList.contains('is-resolved')) return;
                if ((el.dataset.cardKind || '') !== 'cli_approval') return;
                if (command && el.dataset.command && el.dataset.command !== command) return;
                if (cwd && el.dataset.cwd && el.dataset.cwd !== cwd) return;
                if (gone) {
                    el.replaceChildren();
                    renderCliApprovalCard(el, goneApprovalCard(Object.assign({}, card, {
                        id: el.id || card.id,
                        command: el.dataset.command || command,
                        cwd: el.dataset.cwd || cwd,
                    })));
                    return;
                }
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
        isNestGitCard,
        renderHostPathConsentCard,
        renderCliApprovalCard,
        decideHostPathConsent,
        decideCliApproval,
        dismissCliApprovalChrome,
        isGoneApprovalResponse,
        collapseRelatedConsentCards,
        collapseGrantedConsentCards,
    };
})();
