/**
 * BossMod AI — the status panel on an Extensions card (a panel extension's
 * `PanelStatus`), and the message / error / action-button rendering the model
 * provider controls share with it.
 *
 * The server describes what to show and this knows nothing about any one
 * extension: a plain sentence, an error as an alert, labelled facts (each may
 * be copied, or masked until revealed) and action buttons. Every control is a
 * `btn btn-sm`; a fact's value sits in a read-only box (wrapping text; a secret in the
 * masked field the settings form uses, so Show / Hide is BossModSecretField's),
 * with its buttons on the label's line so a long value keeps the full width.
 *
 * States the caller shows around it: loading (the dialog's own), load failed
 * (the card's retry button and error line) and — because a status always
 * carries a message — never empty.
 */
const BossModExtensionPanel = (() => {
    const { h } = BossModDom;
    // How long a Copy button says what happened before it goes back to Copy.
    const FEEDBACK_MS = 1500;

    const COPY = Object.freeze({
        copy: 'Copy',
        copied: 'Copied',
        copyFailed: 'Couldn’t copy',
        show: 'Show',
    });

    /**
     * The status sentence, then the error (if any) as an alert.
     *
     * @param {{message: string, error?: (string|null)}} status
     * @returns {HTMLElement[]}
     */
    function messageRows(status) {
        const rows = [h('p', { class: 'market-card-note', 'aria-live': 'polite' }, status.message)];
        if (status.error) rows.push(h('p', { class: 'market-failed', role: 'alert' }, status.error));
        return rows;
    }

    /**
     * One flat button per action.
     *
     * @param {Array<{key: string, label: string, values: Object<string, string>}>} actions
     * @param {boolean} busy  Disables every button while a request runs.
     * @param {(action: {key: string, label: string, values: Object<string, string>}) => void} onAction
     * @returns {HTMLElement[]}
     */
    function actionButtons(actions, busy, onAction) {
        return actions.map((action) => h('button', {
            class: 'btn btn-sm', type: 'button', disabled: busy, onclick: () => onAction(action),
        }, action.label));
    }

    async function copyText(button, value) {
        let label = COPY.copied;
        try {
            if (!navigator.clipboard) throw new Error('no clipboard');
            await navigator.clipboard.writeText(value);
        } catch (err) {
            console.error('[extension-panel] copy failed', err);
            label = COPY.copyFailed;
        }
        button.textContent = label;
        setTimeout(() => { button.textContent = COPY.copy; }, FEEDBACK_MS);
    }

    /**
     * One labelled fact: its label with Show (secrets) and Copy beside it, and its
     * value in a read-only field under them.
     *
     * @param {{label: string, value: string, copyable: boolean, secret: boolean}} fact
     * @param {string} id  A unique element id for the field.
     * @returns {HTMLElement}
     */
    function factRow(fact, id) {
        // A secret stays in a masked field (BossModSecretField). Any other
        // value is text that wraps, so a long command is fully visible and
        // selectable instead of clipped in a one-line field.
        const input = fact.secret
            ? h('input', {
                class: 'field-input field-mono bm-secret-masked',
                id, type: 'text', readonly: true, autocomplete: 'off', spellcheck: 'false',
            })
            : h('div', {
                class: 'field-input field-mono ext-fact-value', id, role: 'textbox', 'aria-readonly': 'true',
                'aria-labelledby': `${id}-label`, tabindex: '0',
            }, fact.value);
        if (fact.secret) input.value = fact.value;
        const tools = [];
        if (fact.secret) {
            const reveal = h('button', {
                class: 'btn btn-sm', type: 'button', 'aria-pressed': 'false', 'aria-controls': id,
                onclick: () => BossModSecretField.toggle(input, reveal),
            }, COPY.show);
            tools.push(reveal);
        }
        if (fact.copyable) {
            const copy = h('button', {
                class: 'btn btn-sm', type: 'button', 'aria-label': `${COPY.copy} ${fact.label.toLowerCase()}`,
                onclick: () => { void copyText(copy, fact.value); },
            }, COPY.copy);
            tools.push(copy);
        }
        return h('div', { class: 'ext-fact' },
            h('div', { class: 'ext-fact-head' },
                h('label', { class: 'field-label-sm', id: `${id}-label`, for: fact.secret ? id : null }, fact.label),
                tools.length ? h('div', { class: 'ext-fact-tools' }, tools) : null),
            input);
    }

    /**
     * Render a whole panel: message, error, facts, then actions.
     *
     * @param {object} status  A `PanelStatus` as the server returns it.
     * @param {object} options
     * @param {string} options.idPrefix  Prefix of the facts' element ids.
     * @param {boolean} options.busy  Disables the action buttons while a request runs.
     * @param {(action: object) => void} options.onAction  Called with the chosen action.
     * @returns {HTMLElement[]}
     */
    function render(status, options) {
        const { idPrefix, busy, onAction } = options;
        return [
            ...messageRows(status),
            ...status.facts.map((fact, index) => factRow(fact, `${idPrefix}-fact-${index}`)),
            status.actions.length
                ? h('div', { class: 'ext-panel-actions' }, actionButtons(status.actions, busy, onAction))
                : null,
        ].filter(Boolean);
    }

    return { render, messageRows, actionButtons };
})();
