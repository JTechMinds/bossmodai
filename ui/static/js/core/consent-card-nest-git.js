/**
 * Nest git in-thread card — Enable host git / Add PAT/SSH.
 * Writes the one Settings store. Loaded before consent-card.js.
 */
const BossModNestGitCard = (() => {
    function isNestGitCard(card) {
        return Boolean(card && (card.kind || card.card_kind) === 'nest_git');
    }

    function title(card) {
        return (card && card.title) || 'Enable host git for nest?';
    }

    function body(card) {
        return (card && (card.body || card.reason))
            || 'Remote nest git (typically push) needs credentials the Shell can see. '
            + 'Enable host git after a credential helper or SSH agent is visible to Shell, '
            + 'or add a PAT/SSH. Both write Settings → Nest git. '
            + 'Always-allow on a command does not skip auth. '
            + 'Browser or desktop GitHub login is not the agent\'s.';
    }

    function hint(card) {
        return (card && card.enable_hint)
            || 'same as Settings → Nest git. Always-allow does not skip auth.';
    }

    function actions(card) {
        return [
            {
                label: (card && card.enable_label) || 'Enable host git for nest',
                path: 'enable',
                primary: true,
            },
            {
                label: (card && card.add_label) || 'Add PAT/SSH',
                path: 'credentials',
            },
        ];
    }

    function statusLabel(card) {
        return (card && card.decision_note) || 'Nest git auth ready (Settings → Nest git).';
    }

    function showCredentialsForm(container, card, actionsEl, api, afterSave) {
        if (typeof api !== 'function') throw new Error('[consent-card] api is required');
        actionsEl.replaceChildren();
        const pat = document.createElement('input');
        pat.type = 'password';
        pat.placeholder = 'PAT';
        pat.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-2';
        const ssh = document.createElement('textarea');
        ssh.rows = 3;
        ssh.placeholder = 'SSH private key';
        ssh.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-2';
        const row = document.createElement('div');
        row.className = 'host-path-consent-actions';
        const save = document.createElement('button');
        save.type = 'button';
        save.className = 'hpc-action hpc-action-primary';
        save.textContent = 'Save to Settings';
        const settings = document.createElement('button');
        settings.type = 'button';
        settings.className = 'hpc-action';
        settings.textContent = 'Settings → Nest git';
        settings.addEventListener('click', () => {
            if (typeof SettingsView !== 'undefined' && SettingsView.open) {
                SettingsView.open('nest-git', { focus: 'pat' });
            }
        });
        save.addEventListener('click', async () => {
            save.disabled = true;
            settings.disabled = true;
            try {
                const res = await api(`/api/nest-git/${card.id}/credentials`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pat: pat.value, ssh_key: ssh.value }),
                });
                if (!res.ok) throw new Error((await res.text()) || 'Consent update failed.');
                const updated = await res.json();
                pat.value = '';
                ssh.value = '';
                afterSave(updated);
            } catch (err) {
                save.disabled = false;
                settings.disabled = false;
                const note = document.createElement('div');
                note.className = 'hpc-status';
                note.textContent = err?.message || 'Consent update failed.';
                container.appendChild(note);
            }
        });
        row.appendChild(save);
        row.appendChild(settings);
        actionsEl.appendChild(pat);
        actionsEl.appendChild(ssh);
        actionsEl.appendChild(row);
    }

    return { isNestGitCard, title, body, hint, actions, statusLabel, showCredentialsForm };
})();
