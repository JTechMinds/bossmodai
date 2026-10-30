/**
 * BossMod AI — a model provider's Account and Models sections in the
 * Extensions detail.
 *
 * THE SEAM: a model-provider extension describes itself through its provider
 * status (core/extensions/model_provider.py `ProviderStatus`) — one sentence
 * about the account, its saved accounts and account actions, its models with
 * what each can do and its image input, and the actions on that list — and
 * this renders that structure knowing no provider at all. Every name, label
 * and sentence is the provider's data or COPY here, and reaches the document
 * as a text node through h(). The only things posted back are the provider's
 * own actions, unchanged, and one model's image policy (IMAGE_POLICY_ACTION).
 *
 * Account: the sentence beside a pill (Connected / Signing in… / Signed out),
 * an error as an alert, the saved accounts when there is more to say than
 * "you're signed in" (each row its label, a quiet badge and its one button),
 * then the account actions, the provider's primary one drawn primary.
 *
 * Models: what each model is to the operator (a connection to pick on an
 * agent), when the list was loaded beside its actions, then a table — Model
 * (its name, then its note) | Thinking (the app's level labels, then the
 * default) | Image input (Auto | On | Off in the menus' segment style, then
 * what Auto means for that model). Signed out, or no models yet, says so.
 *
 * The caller (extension-detail.js) shows loading and a failed read, so both
 * functions only ever get a status the server returned.
 */
const BossModProviderSections = (() => {
    const { h } = BossModDom;

    /**
     * The action posted when the operator picks one model's image input, with
     * values `{ model, policy }`. Mirrors core/extensions/model_provider.py
     * `IMAGE_POLICY_ACTION`: the two change together.
     */
    const IMAGE_POLICY_ACTION = 'image_policy';

    const COPY = Object.freeze({
        connected: 'Connected',
        signingIn: 'Signing in…',
        signedOut: 'Signed out',
        accountsLabel: 'Saved accounts',
        active: 'Active',
        notConnected: 'Sign in to see your models.',
        noModels: 'No models yet. Refresh models to load them.',
        intro: (name) => `Each model is a “${name}” connection you can pick when you create or edit an agent.`,
        updated: (when) => `Updated ${when}`,
        caption: 'Models, their thinking levels and their image input',
        model: 'Model',
        thinking: 'Thinking',
        imageInput: 'Image input',
        defaultOnly: 'Default only',
        defaultIs: (label) => `Default: ${label}`,
        imageGroup: (name) => `Image input for ${name}`,
        autoOn: 'on',
        autoOff: 'off',
    });

    // The segment's options in reading order: what the operator reads, and
    // the policy the provider stores.
    const IMAGE_OPTIONS = Object.freeze([
        Object.freeze({ policy: 'auto', label: 'Auto' }),
        Object.freeze({ policy: 'enabled', label: 'On' }),
        Object.freeze({ policy: 'disabled', label: 'Off' }),
    ]);

    // The contract gives every list a default, so a list the server did not
    // send is a broken status, not an empty one.
    function requireLists(status, keys) {
        keys.forEach((key) => {
            if (!Array.isArray(status[key])) {
                throw new Error(`[provider-sections] the provider status has no "${key}" list`);
            }
        });
    }

    function requireOptions(options, functions, strings) {
        if (!options || typeof options.busy !== 'boolean') {
            throw new Error('[provider-sections] options.busy must be a boolean');
        }
        functions.forEach((name) => {
            if (typeof options[name] !== 'function') throw new Error(`[provider-sections] options.${name} must be a function`);
        });
        strings.forEach((name) => {
            if (typeof options[name] !== 'string' || !options[name]) {
                throw new Error(`[provider-sections] options.${name} must be a non-empty string`);
            }
        });
    }

    // One row of flat buttons, the provider's primary one drawn primary.
    function actionRow(actions, busy, onAction) {
        if (!actions.length) return null;
        const buttons = BossModExtensionPanel.actionButtons(actions, busy, onAction);
        actions.forEach((action, index) => {
            if (action.primary) buttons[index].classList.add('btn-primary');
        });
        return h('div', { class: 'ext-panel-actions' }, buttons);
    }

    // Where the account stands, as a shape as well as the sentence beside it.
    function pill(status) {
        if (status.connected) return h('span', { class: 'status-pill', 'data-tone': 'ok' }, COPY.connected);
        if (status.pending) return h('span', { class: 'status-pill', 'data-tone': 'pending' }, COPY.signingIn);
        return h('span', { class: 'status-pill' }, COPY.signedOut);
    }

    // The saved accounts, only when there is more than "you're signed in" to
    // say: several of them, or the one there is is not the signed-in active one.
    function accountList(status, idPrefix, busy, onAction) {
        const accounts = status.accounts;
        const sole = accounts.length === 1 ? accounts[0] : null;
        if (!accounts.length || (sole && sole.active && sole.signed_in)) return null;
        return h('ul', { class: 'ext-accounts', 'aria-label': COPY.accountsLabel },
            accounts.map((saved, index) => {
                const labelId = `${idPrefix}-account-${index}`;
                let badge = null;
                if (!saved.signed_in) badge = COPY.signedOut;
                else if (saved.active) badge = COPY.active;
                return h('li', {},
                    h('span', { class: 'ext-account-label', id: labelId }, saved.label),
                    badge ? h('span', { class: 'status-pill' }, badge) : null,
                    // Described by its account, so "Switch" says which one.
                    saved.action ? h('button', {
                        class: 'btn btn-sm', type: 'button', disabled: busy, 'aria-describedby': labelId,
                        onclick: () => onAction(saved.action),
                    }, saved.action.label) : null);
            }));
    }

    /**
     * The Account section: who is signed in, the saved accounts and the
     * account actions.
     *
     * @param {object} status  A provider status as the server returns it.
     * @param {object} options
     * @param {string} options.idPrefix  Prefix of the account rows' label ids.
     * @param {boolean} options.busy  Disables every button while a request runs.
     * @param {(action: {key: string, label: string, values: Object<string, string>}) => void} options.onAction
     *   Called with the pressed action, unchanged.
     * @returns {HTMLElement[]}
     * @throws {Error} On a missing option, or a status without its
     *   `accounts` or `actions` list.
     */
    function account(status, options) {
        requireOptions(options, ['onAction'], ['idPrefix']);
        requireLists(status, ['accounts', 'actions']);
        const { idPrefix, busy, onAction } = options;
        const [message, ...error] = BossModExtensionPanel.messageRows(status);
        return [
            h('div', { class: 'ext-account-head' }, message, pill(status)),
            ...error,
            accountList(status, idPrefix, busy, onAction),
            actionRow(status.actions, busy, onAction),
        ].filter(Boolean);
    }

    // A host level key as the app names it everywhere (the agent form's
    // thinking choices); a key with no label is a contract break.
    function levelLabel(key) {
        const choice = BossModAgentFields.THINKING_CHOICES.find((entry) => entry.value === key);
        if (!choice) throw new Error(`[provider-sections] unknown thinking level "${key}"`);
        return choice.label;
    }

    function thinkingCell(model) {
        return h('td', {},
            model.thinking.length ? model.thinking.map(levelLabel).join(', ') : COPY.defaultOnly,
            model.thinking_default
                ? h('span', { class: 'ext-model-note' }, COPY.defaultIs(levelLabel(model.thinking_default)))
                : null);
    }

    // Auto | On | Off. A policy the provider does not offer for this model is
    // disabled, and all three are while a request runs or when it offers none.
    function imageCell(model, busy, onImagePolicy) {
        if (!IMAGE_OPTIONS.some((option) => option.policy === model.image_policy)) {
            throw new Error(`[provider-sections] "${model.key}" has an unknown image policy "${model.image_policy}"`);
        }
        const group = h('div', { class: 'menu-segment', role: 'group', 'aria-label': COPY.imageGroup(model.name) },
            IMAGE_OPTIONS.map((option) => {
                const pressed = option.policy === model.image_policy;
                return h('button', {
                    class: 'menu-segment-option', type: 'button', 'data-policy': option.policy,
                    'aria-pressed': pressed ? 'true' : 'false',
                    disabled: busy || !model.image_choices.includes(option.policy),
                    onclick: () => { if (!pressed) onImagePolicy(model.key, option.policy); },
                }, option.label);
            }));
        // What Auto comes to for this model: on only when it takes images.
        const effect = model.image_policy === 'auto'
            ? h('span', { class: 'ext-image-effect' }, model.images === 'supported' ? COPY.autoOn : COPY.autoOff)
            : null;
        return h('td', { class: 'ext-agents-control' }, h('div', { class: 'ext-image-cell' }, group, effect));
    }

    function modelsTable(models, busy, onImagePolicy) {
        return h('table', { class: 'ext-models-table' },
            h('caption', { class: 'visually-hidden' }, COPY.caption),
            h('thead', {}, h('tr', {},
                h('th', { scope: 'col' }, COPY.model),
                h('th', { scope: 'col' }, COPY.thinking),
                h('th', { scope: 'col', class: 'ext-agents-control' }, COPY.imageInput))),
            h('tbody', {}, models.map((model) => h('tr', {},
                h('th', { scope: 'row' },
                    model.name,
                    model.note ? h('span', { class: 'ext-model-note' }, model.note) : null),
                thinkingCell(model),
                imageCell(model, busy, onImagePolicy)))));
    }

    // `Sep 21, 9:35 AM`. A time the server sent that cannot be read is a
    // server bug, said as one rather than shown as no time.
    function updatedLine(iso) {
        const when = BossModFormat.formatDateTime(iso);
        if (!when) throw new Error(`[provider-sections] unreadable models_updated_at "${iso}"`);
        return h('span', { class: 'field-hint' }, COPY.updated(when));
    }

    /**
     * The Models section: what each model can do, and its image input.
     *
     * @param {object} status  A provider status as the server returns it.
     * @param {object} options
     * @param {boolean} options.busy  Disables every control while a request runs.
     * @param {string} options.connectionName  The connection name each model is
     *   published under (the manifest's `model_provider.connection_name`).
     * @param {(action: object) => void} options.onAction  Called with a pressed
     *   model action (e.g. refreshing the list), unchanged.
     * @param {(model: string, policy: string) => void} options.onImagePolicy
     *   Called with a model's key and the image policy the operator picked;
     *   the caller posts IMAGE_POLICY_ACTION.
     * @returns {HTMLElement[]}
     * @throws {Error} On a missing option; a status without its `models` or
     *   `model_actions` list; a thinking level or image policy outside the
     *   app's vocabulary; or a `models_updated_at` that cannot be read.
     */
    function models(status, options) {
        requireOptions(options, ['onAction', 'onImagePolicy'], ['connectionName']);
        requireLists(status, ['models', 'model_actions']);
        const { busy, connectionName, onAction, onImagePolicy } = options;
        if (!status.connected) return [h('p', { class: 'market-status' }, COPY.notConnected)];
        const actions = actionRow(status.model_actions, busy, onAction);
        if (!status.models.length) {
            return [h('p', { class: 'market-status' }, COPY.noModels), actions].filter(Boolean);
        }
        const updated = status.models_updated_at ? updatedLine(status.models_updated_at) : null;
        return [
            h('p', { class: 'field-hint' }, COPY.intro(connectionName)),
            updated || actions ? h('div', { class: 'ext-models-head' }, updated, actions) : null,
            modelsTable(status.models, busy, onImagePolicy),
        ].filter(Boolean);
    }

    return { IMAGE_POLICY_ACTION, COPY, account, models };
})();
