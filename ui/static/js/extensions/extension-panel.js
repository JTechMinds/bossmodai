/**
 * BossMod AI — the Extensions detail's Status section: a panel extension's
 * `PanelStatus`, and the message / error / action-button rendering a model
 * provider's Account and Models sections (provider-sections.js) share.
 *
 * The server describes what to show and this knows nothing about any one
 * extension: a plain sentence, an error as an alert, then titled groups. A
 * group is a heading (h4: the hero's title is the h3), an optional intro and
 * its labelled facts; each fact may be copied, or masked until revealed, and
 * may carry a one-line hint under its value, which the value names as its
 * description. The sentence is ordinary state in the page's own ink, not a
 * warning: only an error is coloured, as an alert. Every control is a
 * `btn btn-sm`; a fact's value sits in a read-only box (wrapping text; a
 * secret in the masked field the settings form uses, so Show / Hide is
 * BossModSecretField's), with its buttons on the label's line so a long
 * value keeps the full width. A panel has no buttons of its own beyond Show
 * and Copy: what the operator changes lives in the Settings section.
 *
 * States the caller shows around it: loading and load failed (the detail's
 * own line and retry) and — because a status always carries a message —
 * never empty.
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
        const rows = [h('p', { class: 'ext-message', 'aria-live': 'polite' }, status.message)];
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
     * One labelled fact: its label with Show (secrets) and Copy beside it, its
     * value in a read-only field under them, and its hint (if any) under that.
     *
     * @param {{label: string, value: string, copyable: boolean, secret: boolean,
     *   hint: (string|null)}} fact
     * @param {string} id  A unique element id for the field; the hint is `${id}-hint`.
     * @returns {HTMLElement}
     */
    function factRow(fact, id) {
        const hintId = fact.hint ? `${id}-hint` : null;
        // A secret stays in a masked field (BossModSecretField). Any other
        // value is text that wraps, so a long command is fully visible and
        // selectable instead of clipped in a one-line field.
        const input = fact.secret
            ? h('input', {
                class: 'field-input field-mono bm-secret-masked',
                id, type: 'text', readonly: true, autocomplete: 'off', spellcheck: 'false',
                'aria-describedby': hintId,
            })
            : h('div', {
                class: 'field-input field-mono ext-fact-value', id, role: 'textbox', 'aria-readonly': 'true',
                'aria-labelledby': `${id}-label`, 'aria-describedby': hintId, tabindex: '0',
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
            input,
            fact.hint ? h('p', { class: 'field-hint', id: hintId }, fact.hint) : null);
    }

    /**
     * One titled group: a section named by its heading, its intro, then its facts.
     *
     * @param {{title: string, intro: (string|null), facts: object[]}} group
     * @param {number} g  The group's position; numbers its ids.
     * @param {string} idPrefix
     * @returns {HTMLElement} `section.ext-panel-group`.
     */
    function groupSection(group, g, idPrefix) {
        const titleId = `${idPrefix}-group-${g}`;
        return h('section', { class: 'ext-panel-group', 'aria-labelledby': titleId },
            h('h4', { class: 'ext-panel-group-title', id: titleId }, group.title),
            group.intro ? h('p', { class: 'field-hint' }, group.intro) : null,
            group.facts.map((fact, f) => factRow(fact, `${idPrefix}-fact-${g}-${f}`)));
    }

    /**
     * Render a whole panel: message, error, then one section per group.
     *
     * @param {object} status  A `PanelStatus` as the server returns it.
     * @param {object} options
     * @param {string} options.idPrefix  Prefix of the groups' and facts' element
     *   ids: `${idPrefix}-group-${g}` titles group g, `${idPrefix}-fact-${g}-${f}`
     *   is its fact f.
     * @returns {HTMLElement[]}
     */
    function render(status, options) {
        const { idPrefix } = options;
        return [
            ...messageRows(status),
            ...status.groups.map((group, g) => groupSection(group, g, idPrefix)),
        ];
    }

    return { render, messageRows, actionButtons };
})();
