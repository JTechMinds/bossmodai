/**
 * BossMod AI — the one-time "What should your team call you?" dialog.
 *
 * Agents call the human "Boss", or "<name> (the boss)" once a name is set
 * (core/boss.py). This asks for that name once, at boot, and never again
 * whatever the answer: Save stores the name, and Save, Not now, ✕ and Esc
 * all record `boss_name_prompted`, so the question does not come back on the
 * next launch. The name stays editable under Settings → System → Profile.
 *
 * The server validates the name (core.boss.validate_boss_name); a refusal is
 * shown inline and the dialog stays open with what was typed.
 */
const BossModBossNamePrompt = (() => {
    const { h } = BossModDom;

    const TITLE = 'What should your team call you?';
    const HINT = 'Agents will call you by this name. If you leave it blank, they\'ll call you Boss. '
        + 'You can change it anytime in Settings.';
    const FORM_ID = 'boss-name-form';
    const INPUT_ID = 'boss-name-input';
    const SUBMIT_ID = 'boss-name-submit';
    const SAVE_LABEL = 'Save';
    const SAVING_LABEL = 'Saving…';

    /**
     * Store one profile setting.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @param {string} key
     * @param {string} value
     * @returns {Promise<void>}
     * @throws {Error} (rejects) With the server's refusal message, or the
     *   network failure.
     */
    async function putSetting(api, key, value) {
        const url = `/api/settings/${encodeURIComponent(key)}`
            + `?value=${encodeURIComponent(value)}&category=profile`;
        const res = await api(url, { method: 'PUT' });
        if (res.ok) return;
        let payload = null;
        try {
            payload = await res.json();
        } catch (err) {
            console.error(`[boss-name-prompt] the ${key} refusal had no JSON body`, err);
        }
        throw new Error(BossModApi.formatError(payload, res.status));
    }

    /**
     * Read the profile settings the dialog depends on.
     *
     * @param {Function} api
     * @returns {Promise<{name: string, prompted: boolean}>}
     * @throws {Error} (rejects) On a failed request, or when either row is
     *   missing: the seed creates both, so a missing row is a broken
     *   database, never "ask anyway".
     */
    async function readProfile(api) {
        const res = await api('/api/settings?category=profile', { cache: 'no-store' });
        if (!res.ok) throw new Error(`Could not read your profile settings (HTTP ${res.status}).`);
        const rows = await res.json();
        const byKey = new Map((Array.isArray(rows) ? rows : []).map((row) => [row.key, row.value]));
        if (!byKey.has('boss_name') || !byKey.has('boss_name_prompted')) {
            throw new Error('The profile settings are missing boss_name or boss_name_prompted.');
        }
        return {
            name: String(byKey.get('boss_name') || '').trim(),
            prompted: byKey.get('boss_name_prompted') === 'true',
        };
    }

    /**
     * Open the dialog.
     *
     * @param {Function} api
     * @returns {{close: () => void}}
     */
    function open(api) {
        // True once the prompted flag is stored, so closing never writes it twice.
        let answered = false;
        let busy = false;
        let modal = null;

        const input = h('input', {
            class: 'field-input',
            id: INPUT_ID,
            type: 'text',
            autocomplete: 'name',
            'aria-describedby': `${INPUT_ID}-hint`,
        });
        const error = h('p', { class: 'context-error', role: 'alert' });

        async function save() {
            await putSetting(api, 'boss_name', input.value.trim());
            await putSetting(api, 'boss_name_prompted', 'true');
            answered = true;
        }

        const form = h('form', {
            id: FORM_ID,
            onsubmit: (event) => {
                event.preventDefault();
                if (busy) return;
                const submit = modal.element.querySelector(`#${SUBMIT_ID}`);
                busy = true;
                submit.disabled = true;
                submit.textContent = SAVING_LABEL;
                error.textContent = '';
                return save().then(() => {
                    modal.close();
                }, (err) => {
                    console.error('[boss-name-prompt] could not save your name', err);
                    error.textContent = (err && err.message) || 'Your name could not be saved.';
                    busy = false;
                    submit.disabled = false;
                    submit.textContent = SAVE_LABEL;
                    input.focus();
                });
            },
        },
            h('div', { class: 'field' },
                h('label', { class: 'field-label', for: INPUT_ID }, 'Your name'),
                input,
                h('p', { class: 'field-hint', id: `${INPUT_ID}-hint` }, HINT),
                error));

        modal = BossModOverlays.createModal({
            title: TITLE,
            body: form,
            actions: [
                { label: 'Not now', tone: 'quiet' },
                { label: SAVE_LABEL, tone: 'primary', id: SUBMIT_ID, form: FORM_ID },
            ],
            // Not now, ✕ and Esc all land here without a saved answer: the
            // question is still answered, so it does not return next launch.
            onClose: () => {
                if (answered) return;
                answered = true;
                putSetting(api, 'boss_name_prompted', 'true').catch((err) => {
                    console.error('[boss-name-prompt] could not record that the name prompt was dismissed', err);
                });
            },
        });
        return { close: () => modal.close() };
    }

    /**
     * Open the dialog when no name is set and it was never answered.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper, injected by the shell.
     * @returns {Promise<boolean>} Whether the dialog opened.
     * @throws {Error} (rejects) When the profile settings cannot be read;
     *   the caller reports it, nothing here swallows it.
     */
    async function maybeAsk(deps) {
        const { api } = deps || {};
        if (typeof api !== 'function') throw new Error('[boss-name-prompt] deps.api is required');
        const profile = await readProfile(api);
        if (profile.name || profile.prompted) return false;
        open(api);
        return true;
    }

    return { maybeAsk, TITLE };
})();
