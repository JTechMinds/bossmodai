/**
 * BossMod AI — "open this folder in the file manager", and the one-time
 * question about how.
 *
 * The desk browser can ask the host to reveal a folder. The host refuses with
 * 409 until it knows which file manager to use, so the first attempt turns into
 * a question, an answer saved to settings, and one retry. Split out of the
 * browser because "which file manager does this machine have" is a settings
 * concern that happens to be discovered here.
 *
 * Ported from agent-context.js unchanged in behaviour; the hand-rolled overlay
 * is now core/overlays.js, so the prompt traps focus and answers Esc like every
 * other dialog.
 */
const BossModDeskOpener = (() => {
    const { h } = BossModDom;

    const HANDLER_CODES = Object.freeze([
        'desk_open_folder_handler_required',
        'desk_open_folder_handler_invalid',
    ]);
    const PROMPT_TITLE = 'Choose Folder Opener';
    const PROMPT_FALLBACK = 'Choose how BossMod should open folders on this machine.';
    const CUSTOM_VALUE = '__custom__';

    /**
     * Ask which file manager to use.
     *
     * @param {object} detail  The 409 body's `detail`: `{message, options}`.
     * @returns {Promise<string|null>} The chosen command, or null if the
     *   operator cancelled or dismissed — a real answer, not a swallowed error.
     */
    function prompt(detail) {
        const options = Array.isArray(detail && detail.options) ? detail.options : [];
        return new Promise((resolve) => {
            const custom = h('input', {
                type: 'text',
                id: 'folder-opener-custom-input',
                class: 'desk-opener-custom',
                placeholder: 'e.g. thunar',
            });

            const radios = [];
            function radio(value, checked) {
                const input = h('input', {
                    type: 'radio',
                    name: 'folder-opener-choice',
                    value,
                });
                input.checked = checked;
                radios.push(input);
                return input;
            }

            const list = h('div', { class: 'desk-opener-options', id: 'folder-opener-choice-list' });
            options.forEach((option, index) => {
                list.append(h('label', { class: 'desk-opener-option' },
                    radio(String(option.value), index === 0),
                    h('span', {},
                        h('span', { class: 'desk-opener-label' }, String(option.label)),
                        h('span', { class: 'desk-opener-hint' }, String(option.description || '')))));
            });
            list.append(h('label', { class: 'desk-opener-option' },
                radio(CUSTOM_VALUE, options.length === 0),
                h('span', {},
                    h('span', { class: 'desk-opener-label' }, 'Custom executable'),
                    h('span', { class: 'desk-opener-hint' },
                        'Enter the file manager command available on PATH.'),
                    custom)));

            // Typing a command is a choice; it should not also need the radio.
            custom.addEventListener('focus', () => {
                radios.forEach((input) => { input.checked = input.value === CUSTOM_VALUE; });
            });

            let answered = false;
            BossModOverlays.createModal({
                title: PROMPT_TITLE,
                body: h('div', {},
                    h('p', { class: 'desk-opener-message' },
                        String((detail && detail.message) || PROMPT_FALLBACK)),
                    list),
                actions: [
                    {
                        label: 'Save',
                        id: 'folder-opener-save',
                        tone: 'primary',
                        onSelect: () => {
                            const chosen = radios.find((input) => input.checked === true);
                            if (!chosen) return;
                            const value = chosen.value === CUSTOM_VALUE
                                ? String(custom.value || '').trim()
                                : chosen.value;
                            if (!value) return;
                            answered = true;
                            resolve(value);
                        },
                    },
                    { label: 'Cancel', id: 'folder-opener-cancel', tone: 'quiet' },
                ],
                // Fires however the dialog closed, and after any onSelect, so a
                // dismissal resolves null rather than hanging the caller.
                onClose: () => { if (!answered) resolve(null); },
            });
        });
    }

    /**
     * Ask the host to reveal a folder, answering the handler question once.
     *
     * @param {object} deps
     * @param {Function} deps.api      Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @param {string}   deps.path     Virtual desk path.
     * @param {(message: string) => void} deps.onError  Surfaces a failure; the
     *   original logged to the console alone, which told the operator nothing.
     * @returns {Promise<boolean>} Whether the folder was opened.
     */
    async function openFolder({ api, agentId, path, onError }) {
        const target = path || '/me';
        const url = `/api/agents/${agentId}/desk/open-folder?path=${encodeURIComponent(target)}`;

        async function attempt(allowRetry) {
            let res;
            try {
                res = await api(url, { method: 'POST' });
            } catch (err) {
                console.error('[desk-opener] could not reach the host', err);
                onError('Could not open the folder.');
                return false;
            }
            if (res.ok) return true;

            if (res.status === 409 && allowRetry) {
                const payload = await res.json();
                const detail = payload && payload.detail;
                if (detail && HANDLER_CODES.indexOf(detail.code) !== -1) {
                    const chosen = await prompt(detail);
                    // Cancelling is an answer: nothing opens, nothing is saved.
                    if (!chosen) return false;
                    const saved = await api(
                        '/api/settings/desktop_open_folder_handler'
                        + `?value=${encodeURIComponent(chosen)}&category=advanced`,
                        { method: 'PUT' },
                    );
                    if (!saved.ok) {
                        console.error(`[desk-opener] could not save the handler: HTTP ${saved.status}`);
                        onError('Could not save that folder opener.');
                        return false;
                    }
                    // Exactly one retry: a second 409 is a real failure.
                    return attempt(false);
                }
            }
            const text = await res.text();
            console.error('[desk-opener] open-folder failed', text);
            onError('Could not open the folder.');
            return false;
        }

        return attempt(true);
    }

    return { openFolder, prompt, HANDLER_CODES };
})();
