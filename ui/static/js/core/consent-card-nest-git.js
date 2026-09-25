/**
 * Nest git in-thread card — GitHub token / SSH key, or use this computer’s Git login.
 * Named credentials can be picked when a remote doesn’t match. Loaded before consent-card.js.
 */
const BossModNestGitCard = (() => {
    function isNestGitCard(card) {
        return Boolean(card && (card.kind || card.card_kind) === 'nest_git');
    }

    function title(card) {
        if (card && card.title) return card.title;
        const name = (card && (card.agent_name || card.agentName)) || '';
        return name
            ? `${name} needs permission to push to GitHub`
            : 'needs permission to push to GitHub';
    }

    function body(card) {
        return (card && (card.body || card.reason))
            || 'Your computer’s GitHub login isn’t shared with agents. '
            + 'Paste a GitHub access token (a special password from GitHub → Settings → Developer settings), '
            + 'or an SSH key if you use those. Saved once under Settings → Nest git. '
            + 'Approving a command once doesn’t skip this.';
    }

    function hint(card) {
        return (card && card.enable_hint)
            || 'Saved once under Settings → Nest git. Approving a command once doesn’t skip this.';
    }

    function errorText(card) {
        return (card && card.error) || '';
    }

    function actions(card) {
        const out = [
            {
                label: (card && card.enable_label) || 'Use this computer’s Git login',
                path: 'enable',
                primary: true,
            },
            {
                label: (card && card.add_label) || 'Add a GitHub access token or SSH key',
                path: 'credentials',
            },
        ];
        const saved = (card && card.credentials) || [];
        for (const cred of saved) {
            if (!cred || !cred.id) continue;
            const name = cred.label || 'saved credential';
            out.push({
                label: `Use ${name}`,
                path: 'use',
                credential_id: cred.id,
            });
        }
        return out;
    }

    function statusLabel(card) {
        return (card && card.decision_note) || 'GitHub permission saved under Settings → Nest git.';
    }

    function showCredentialsForm(container, card, actionsEl, api, afterSave) {
        if (typeof api !== 'function') throw new Error('[consent-card] api is required');
        actionsEl.replaceChildren();
        const label = document.createElement('input');
        label.type = 'text';
        label.placeholder = 'Name';
        label.setAttribute('aria-label', 'Name');
        label.value = (card && card.suggested_label) || '';
        label.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white mb-2';
        const match = document.createElement('input');
        match.type = 'text';
        match.placeholder = 'github.com/Org/* or github.com/Org/repo';
        match.setAttribute('aria-label', 'Remote match');
        match.value = (card && card.suggested_match) || '';
        match.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-2';
        const pat = document.createElement('input');
        pat.type = 'text';
        pat.placeholder = 'GitHub access token';
        pat.setAttribute('aria-label', 'GitHub access token');
        pat.setAttribute('autocomplete', 'off');
        pat.setAttribute('spellcheck', 'false');
        pat.setAttribute('autocapitalize', 'off');
        pat.className = 'setting-input flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono bm-secret-masked';
        BossModSecretField.bind(pat);
        const reveal = document.createElement('button');
        reveal.type = 'button';
        reveal.className = 'hpc-action';
        reveal.textContent = 'Show';
        reveal.setAttribute('aria-pressed', 'false');
        reveal.addEventListener('click', () => BossModSecretField.toggle(pat, reveal));
        const tokenRow = document.createElement('div');
        tokenRow.className = 'flex gap-2 mb-2';
        tokenRow.append(pat, reveal);
        const ssh = document.createElement('textarea');
        ssh.rows = 3;
        ssh.placeholder = 'SSH key (optional)';
        ssh.setAttribute('aria-label', 'SSH key (optional)');
        ssh.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-2';
        const row = document.createElement('div');
        row.className = 'host-path-consent-actions';
        const save = document.createElement('button');
        save.type = 'button';
        save.className = 'hpc-action hpc-action-primary';
        save.textContent = 'Save';
        const settings = document.createElement('button');
        settings.type = 'button';
        settings.className = 'hpc-action';
        settings.textContent = 'Open Nest git settings';
        settings.addEventListener('click', () => {
            if (typeof SettingsView !== 'undefined' && SettingsView.open) {
                SettingsView.open('nest-git', { focus: 'pat' });
            }
        });
        save.addEventListener('click', async () => {
            const token = BossModSecretField.read(pat);
            const rawKey = String(ssh.value || '');
            const key = rawKey.trim() ? rawKey : '';
            if (!token && !key) {
                const note = document.createElement('div');
                note.className = 'hpc-status';
                note.textContent = 'Paste a GitHub access token or an SSH key. An empty field doesn’t save.';
                container.appendChild(note);
                return;
            }
            save.disabled = true;
            settings.disabled = true;
            try {
                const res = await api(`/api/nest-git/${card.id}/credentials`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        pat: token || undefined,
                        ssh_key: key || undefined,
                        label: label.value,
                        match: match.value,
                    }),
                });
                const bodyText = await res.text();
                if (!res.ok) {
                    if (absorbFailure(container, card, res, bodyText)) return;
                    throw new Error(operatorMessage(bodyText) || 'Consent update failed.');
                }
                BossModSecretField.clear(pat);
                ssh.value = '';
                afterSave(bodyText ? JSON.parse(bodyText) : {});
            } catch (err) {
                if (absorbFailure(container, card, null, err?.message)) return;
                save.disabled = false;
                settings.disabled = false;
                const note = document.createElement('div');
                note.className = 'hpc-status';
                note.textContent = operatorMessage(err?.message) || 'Consent update failed.';
                container.appendChild(note);
            }
        });
        row.appendChild(save);
        row.appendChild(settings);
        actionsEl.appendChild(label);
        actionsEl.appendChild(match);
        actionsEl.appendChild(tokenRow);
        actionsEl.appendChild(ssh);
        actionsEl.appendChild(row);
    }

    async function useSaved(container, card, actionsEl, api, item, afterSave) {
        const credentialId = item && item.credential_id;
        if (!credentialId || typeof api !== 'function') return;
        Array.from(actionsEl.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const res = await api(`/api/nest-git/${card.id}/use`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential_id: credentialId }),
            });
            const bodyText = await res.text();
            if (!res.ok) {
                if (absorbFailure(container, card, res, bodyText)) return;
                throw new Error(operatorMessage(bodyText) || 'Consent update failed.');
            }
            afterSave(bodyText ? JSON.parse(bodyText) : {});
        } catch (err) {
            if (absorbFailure(container, card, null, err?.message)) return;
            Array.from(actionsEl.querySelectorAll('button')).forEach((btn) => { btn.disabled = false; });
            const note = document.createElement('div');
            note.className = 'hpc-status';
            note.textContent = operatorMessage(err?.message) || 'Consent update failed.';
            container.appendChild(note);
        }
    }

    function isSchemaMismatch(res, bodyText) {
        if (res && res.status === 422) return true;
        const text = String(bodyText || '');
        return /Field required/i.test(text) && /"body"/.test(text);
    }

    function isGoneResponse(res, bodyText) {
        if (res && res.status === 404) return true;
        return /not found or already resolved/i.test(String(bodyText || ''));
    }

    function operatorMessage(text) {
        const raw = String(text || '');
        if (isSchemaMismatch(null, raw)) return '';
        try {
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed.detail === 'string') return parsed.detail;
        } catch (err) {
            return raw;
        }
        return raw;
    }

    function goneCard(card, note) {
        return Object.assign({}, card || {}, {
            kind: 'nest_git',
            status: 'gone',
            decision_note: note
                || (card && card.decision_note)
                || 'This GitHub permission ask is gone or already resolved.',
        });
    }

    function paintGone(container, card) {
        if (!container) return;
        container.replaceChildren();
        container.classList.add('host-path-consent-card', 'is-resolved');
        container.dataset.cardKind = 'nest_git';
        const titleEl = document.createElement('div');
        titleEl.className = 'hpc-title';
        titleEl.textContent = title(card);
        const resolved = document.createElement('div');
        resolved.className = 'hpc-status';
        resolved.textContent = card.decision_note || '';
        const row = document.createElement('div');
        row.className = 'host-path-consent-actions';
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'hpc-action';
        dismiss.textContent = 'Dismiss';
        dismiss.addEventListener('click', () => {
            container.hidden = true;
            row.remove();
        });
        row.appendChild(dismiss);
        container.appendChild(titleEl);
        container.appendChild(resolved);
        container.appendChild(row);
    }

    function collapseGoneCards(card, root) {
        const gone = goneCard(card);
        const doc = root || (typeof document !== 'undefined' ? document : null);
        if (!doc || typeof doc.querySelectorAll !== 'function') return;
        const nodes = doc.querySelectorAll('.host-path-consent-card');
        Array.from(nodes).forEach((el) => {
            if ((el.dataset.cardKind || '') !== 'nest_git') return;
            paintGone(el, gone);
        });
    }

    function isNeed(need, action) {
        if (need && need.cardKind === 'nest_git') return true;
        return Boolean(action && /\/api\/nest-git\//.test(String(action.href || '')));
    }

    function actionNeedsBody(need, action) {
        if (!isNeed(need, action)) return false;
        return /\/(credentials|use)\/?$/.test(String((action && action.href) || ''));
    }

    function classifyNeedFailure(need, action, res, bodyText) {
        if (!isNeed(need, action)) return '';
        if (isGoneResponse(res, bodyText)) return 'gone';
        if (isSchemaMismatch(res, bodyText)) return 'mismatch';
        return '';
    }

    function dismissOnlyNeed(need) {
        return Object.assign({}, need, {
            error: '',
            actions: [{
                label: 'Dismiss', href: '#', method: 'POST', tone: 'quiet', dismiss: true,
            }],
        });
    }

    function collapseNeed(need) {
        collapseGoneCards({
            kind: 'nest_git',
            status: 'gone',
            title: (need && need.title) || '',
            command: (need && need.sub) || '',
            decision_note: 'This GitHub permission ask is gone or already resolved.',
        });
    }

    function absorbFailure(container, card, res, bodyText) {
        if (isGoneResponse(res, bodyText) || isSchemaMismatch(res, bodyText)) {
            const note = isSchemaMismatch(res, bodyText)
                ? 'This GitHub permission ask no longer matches.'
                : '';
            const gone = goneCard(card, note);
            paintGone(container, gone);
            collapseGoneCards(gone);
            return true;
        }
        return false;
    }

    async function decideEnable(container, card, actionsEl, api, afterSave) {
        if (typeof api !== 'function') return;
        Array.from(actionsEl.querySelectorAll('button')).forEach((btn) => { btn.disabled = true; });
        try {
            const res = await api(`/api/nest-git/${card.id}/enable`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            const bodyText = await res.text();
            if (!res.ok) {
                if (absorbFailure(container, card, res, bodyText)) return;
                throw new Error(operatorMessage(bodyText) || 'Consent update failed.');
            }
            afterSave(bodyText ? JSON.parse(bodyText) : {});
        } catch (err) {
            if (absorbFailure(container, card, null, err?.message)) return;
            Array.from(actionsEl.querySelectorAll('button')).forEach((btn) => { btn.disabled = false; });
            const note = document.createElement('div');
            note.className = 'hpc-status';
            note.textContent = operatorMessage(err?.message) || 'Consent update failed.';
            container.appendChild(note);
        }
    }

    return {
        isNestGitCard, title, body, hint, errorText, actions, statusLabel,
        showCredentialsForm, useSaved, decideEnable, collapseGoneCards,
        isSchemaMismatch, absorbFailure, actionNeedsBody, classifyNeedFailure,
        dismissOnlyNeed, collapseNeed, operatorMessage,
    };
})();
