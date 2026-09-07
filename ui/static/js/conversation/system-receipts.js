/**
 * BossMod AI — the "Show system notifications" preference and its control.
 *
 * One persisted display choice, and the only place in the conversation surface
 * that touches site data. Split out of conversation.js so the controller holds
 * conversation state and this holds storage: the controller asks whether
 * receipts are shown, and never learns where the answer is kept.
 *
 * What the toggle hides is `systemReceipt` messages. Walk receipts and consent
 * asks are system messages that it NEVER hides — one is the operator's proof an
 * agent moved, the other is a decision they still owe. That rule lives in the
 * sources, which is what decides `systemReceipt`.
 */
const BossModSystemReceipts = (() => {
    const { h } = BossModDom;

    /** Unchanged from the dock-era chat, so the operator's choice survives. */
    const STORAGE_KEY = 'bossmod.chat.showSystemReceipts';
    const TOGGLE_ID = 'chat-system-receipts-toggle';
    const LABEL = 'Show system notifications';

    /**
     * Read the preference.
     *
     * A browser that blocks site data is a known, documented condition rather
     * than a failure to swallow: it is logged and the default (show them) wins,
     * because hiding an operator's receipts because storage was unreadable
     * would be the worse of the two mistakes.
     *
     * @returns {boolean}
     */
    function read() {
        try {
            return window.localStorage.getItem(STORAGE_KEY) !== 'false';
        } catch (err) {
            console.warn('[system-receipts] site data is unreadable; showing system notifications', err);
            return true;
        }
    }

    /**
     * Persist the preference.
     * @param {boolean} value
     * @returns {void}
     */
    function write(value) {
        try {
            window.localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false');
        } catch (err) {
            console.warn('[system-receipts] could not persist the choice', err);
        }
    }

    /**
     * Build the labelled checkbox.
     *
     * @param {object} deps
     * @param {(enabled: boolean) => void} deps.onChange  Called after the new
     *   value has been persisted, so a repaint sees the stored truth.
     * @returns {{ element: HTMLElement, isEnabled: () => boolean }}
     * @throws {Error} When onChange is missing — a toggle nothing listens to
     *   would silently do nothing.
     */
    function createSystemReceiptsToggle(deps) {
        const onChange = deps && deps.onChange;
        if (typeof onChange !== 'function') {
            throw new Error('[system-receipts] deps.onChange is required');
        }

        let enabled = read();

        const input = h('input', {
            type: 'checkbox',
            id: TOGGLE_ID,
            class: 'conversation-system-toggle',
            onchange: () => {
                enabled = input.checked === true;
                write(enabled);
                onChange(enabled);
            },
        });
        input.checked = enabled;

        const element = h('div', { class: 'conversation-controls' },
            h('label', { class: 'conversation-system-label', for: TOGGLE_ID },
                input,
                h('span', {}, LABEL)));

        return { element, isEnabled: () => enabled };
    }

    return { createSystemReceiptsToggle };
})();
