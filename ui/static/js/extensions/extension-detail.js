/**
 * BossMod AI — the Extensions view's detail: one extension, read and managed.
 *
 * Picking a card replaces the rail and the grid with this full-width view,
 * built from the marketplace detail's classes so the two details read alike:
 * who the extension is (the mark, the name, its one-line summary and a byline
 * of category, author and version) on the left of the hero and the one action
 * — its on/off switch — on the right; then the setup line and any reason it
 * cannot run; then its description; then, under one rule, its sections read a
 * tab at a time through section-reader.js.
 *
 * The sections come from the manifest (BossModExtensionItems.sectionsFor), so
 * the tabs never jump when the switch flips: a section whose content needs the
 * extension on says how to turn it on instead. A model provider's Account and
 * Models sections are provider-sections.js's, drawn from the one provider
 * status the controller reads. Two sections hold LIVE nodes the controller
 * (extensions-dialog.js) keeps for as long as the detail is open — the
 * Settings form and the Agents table — and this only mounts them, so typing,
 * a switch mid-request and focus survive every rebuild.
 *
 * Drawn wholesale on every render; the controller owns state and requests.
 * Every string — names, summaries, authors, server messages — is manifest or
 * server data and reaches the document as a text node through h().
 */
const BossModExtensionDetail = (() => {
    const { h } = BossModDom;
    const ITEMS = BossModExtensionItems;
    const PROVIDER = BossModProviderSections;

    const COPY = Object.freeze({
        detailLabel: 'Extension detail',
        enabled: 'Enabled',
        by: 'By',
        setUp: 'Set up',
        runSetup: 'Run its setup?',
        downloading: 'Downloading…',
        retry: 'Retry',
        tryAgain: 'Try again',
        sectionsLabel: 'Extension sections',
        loading: 'Loading…',
        offStatus: 'Turn it on to see its status.',
        offAccount: 'Turn it on to sign in.',
        offModels: 'Turn it on to see its models.',
        offAgents: 'Turn it on to set it up for each agent.',
        excludedLead: 'Not available to agents whose model can’t see images:',
        excludedTail: 'Change models in Settings → Connections.',
        noModel: 'no model',
        everyoneSees: 'Every agent’s model can see images, so all of them can use it.',
    });

    // `<Category> · By <author> · v<version>`, each part only when present.
    function byline(item) {
        const parts = [];
        if (item.category) parts.push(BossModMarketplaceItems.categoryLabel(item.category));
        if (item.author) parts.push(`${COPY.by} ${item.author}`);
        if (item.version) parts.push(`v${item.version}`);
        return parts.length ? h('p', { class: 'market-detail-by' }, parts.join(' · ')) : null;
    }

    // The hero's one action. Absent for an invalid extension, which cannot be
    // turned on. While setup is missing it is off and turning it on ASKS (the
    // confirm strip above the hero); it is held disabled while a request,
    // a download or that question is open.
    function enableSwitch(item, state, handlers) {
        if (!item.valid) return null;
        const setup = ITEMS.setupState(item);
        const missing = setup === 'missing';
        const toggle = BossModSwitch.create({
            label: COPY.enabled,
            pressed: missing ? state.confirming : item.enabled,
            onChange: missing ? () => handlers.onSetupAsk() : (pressed) => handlers.onToggle(pressed),
        });
        toggle.element.setAttribute('id', 'ext-enabled');
        if (state.busy || setup === 'installing' || setup === 'failed' || state.confirming) {
            toggle.element.setAttribute('disabled', '');
        }
        return h('div', { class: 'market-detail-actions' }, toggle.element);
    }

    function hero(item, state, handlers) {
        return h('div', { class: 'market-detail-hero' },
            h('div', { class: 'market-detail-ident' },
                ITEMS.markFor(item, 'lg'),
                h('div', { class: 'market-detail-names' },
                    h('h3', { class: 'market-detail-title' }, item.name),
                    item.summary ? h('p', { class: 'market-detail-specialty' }, item.summary) : null,
                    byline(item))),
            enableSwitch(item, state, handlers));
    }

    // Where the one-click setup stands, when it is doing or has done something
    // the operator must know about.
    function setupLine(item, state, handlers) {
        if (!item.valid) return null;
        const setup = ITEMS.setupState(item);
        if (setup === 'installing') {
            return h('p', { class: 'market-status', 'aria-live': 'polite' }, COPY.downloading);
        }
        if (setup === 'failed') {
            return [
                h('p', { class: 'market-failed', role: 'alert' }, item.setup.detail),
                h('button', {
                    class: 'btn btn-sm', id: 'ext-setup-retry', type: 'button', disabled: state.busy,
                    onclick: () => handlers.onSetupConfirm(),
                }, COPY.retry),
            ];
        }
        return null;
    }

    // A live read that failed: its reason, and the way to read it again.
    function liveFailed(state, handlers) {
        return [
            h('p', { class: 'market-failed', role: 'alert' }, state.liveError),
            h('button', {
                class: 'btn btn-sm', id: 'ext-live-retry', type: 'button',
                onclick: () => handlers.onRetryLive(),
            }, COPY.tryAgain),
        ];
    }

    function statusBody(item, state, handlers) {
        if (!item.enabled) return h('p', { class: 'market-status' }, COPY.offStatus);
        if (state.panel) {
            return BossModExtensionPanel.render(state.panel, {
                idPrefix: 'ext-panel', busy: state.busy, onAction: handlers.onPanelAction,
            });
        }
        if (state.liveError) return liveFailed(state, handlers);
        return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
    }

    // A model provider's Account or Models section (`render` is
    // PROVIDER.account or PROVIDER.models): what to do while it is off, then
    // the one provider read both are drawn from, or why it failed.
    function providerBody(item, state, handlers, offText, render) {
        if (!item.enabled) return h('p', { class: 'market-status' }, offText);
        if (state.provider) {
            return render(state.provider, {
                idPrefix: 'ext-provider',
                busy: state.busy,
                connectionName: item.model_provider.connection_name,
                onAction: (action) => handlers.onProviderAction(action.key, action.values),
                onImagePolicy: (model, policy) => handlers.onProviderAction(PROVIDER.IMAGE_POLICY_ACTION, { model, policy }),
            });
        }
        if (state.liveError) return liveFailed(state, handlers);
        return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
    }

    function settingsBody(item, live) {
        if (!live || live.id !== item.id || !live.settings) {
            throw new Error(`[extension-detail] "${item.id}" has a Settings section but no settings form`);
        }
        return live.settings.element;
    }

    // Which agents cannot use an extension that needs a model that sees
    // images, and — for one with per-agent settings — the table that sets it
    // up for each of them.
    function agentsBody(item, live) {
        const excluded = item.requires_image_model ? item.excluded_agents : [];
        const parts = [];
        if (excluded.length) {
            parts.push(h('div', { class: 'market-card-note' },
                h('p', {}, COPY.excludedLead),
                h('ul', { class: 'ext-excluded' },
                    excluded.map((agent) => h('li', {}, `${agent.name} · ${agent.model || COPY.noModel}`))),
                h('p', {}, COPY.excludedTail)));
        }
        if (item.agent_config) {
            if (!item.enabled) {
                parts.push(h('p', { class: 'market-status' }, COPY.offAgents));
            } else {
                if (!live || live.id !== item.id || !live.agents) {
                    throw new Error(`[extension-detail] "${item.id}" is on with agent_config but has no agents table`);
                }
                parts.push(live.agents.element);
            }
        } else if (!excluded.length) {
            parts.push(h('p', { class: 'market-status' }, COPY.everyoneSees));
        }
        return parts;
    }

    function sectionBody(id, item, state, handlers, live) {
        let content;
        if (id === 'status') content = statusBody(item, state, handlers);
        else if (id === 'account') content = providerBody(item, state, handlers, COPY.offAccount, PROVIDER.account);
        else if (id === 'models') content = providerBody(item, state, handlers, COPY.offModels, PROVIDER.models);
        else if (id === 'settings') content = settingsBody(item, live);
        else if (id === 'agents') content = agentsBody(item, live);
        else if (id === 'tech') content = BossModFactList.create(ITEMS.techFacts(item));
        else throw new Error(`[extension-detail] unknown section "${id}"`);
        return h('div', { class: 'ext-section-body' }, content);
    }

    /**
     * Build the detail view for the extension the state has selected.
     *
     * @param {object} state  BossModExtensionsDialog's state. `selected` is the
     *   item to show; `actionError` and `confirming` sit above the hero;
     *   `busy` disables the switch and the section controls; `sectionKey` is
     *   the open section; `panel`, `provider` and `liveError` are the live
     *   reads the Status section and a provider's Account and Models
     *   sections show.
     * @param {object} handlers  onToggle, onSetupAsk, onSetupConfirm,
     *   onSetupCancel, onSection, onPanelAction, onProviderAction, onRetryLive.
     * @param {{id: string, settings: ({element: HTMLElement}|null),
     *   agents: ({element: HTMLElement}|null)}|null} live  The controller's
     *   persistent nodes for the open extension.
     * @returns {HTMLElement} `section.market-detail.ext-detail#ext-detail`;
     *   `ext-detail` is the hook extensions.css scopes this detail's section
     *   panel on (no ceiling, no scroller of its own).
     * @throws {Error} When nothing is selected, or a section whose live node
     *   the controller owes is missing: an empty section would read as an
     *   extension with nothing to set, and a silent one.
     */
    function render(state, handlers, live) {
        const item = state.selected;
        if (!item) throw new Error('[extension-detail] rendered with nothing selected');
        const reader = BossModSectionReader.render({
            idPrefix: 'ext-section',
            label: COPY.sectionsLabel,
            entries: ITEMS.sectionsFor(item).map((spec) => ({
                id: spec.id, label: spec.label, sub: spec.sub,
                body: sectionBody(spec.id, item, state, handlers, live),
            })),
            selectedId: state.sectionKey,
            onSelect: handlers.onSection,
        });
        return h('section', {
            class: 'market-detail ext-detail', id: 'ext-detail', tabindex: '-1', 'aria-label': COPY.detailLabel,
        },
        state.actionError ? h('p', { class: 'market-error', role: 'alert' }, state.actionError) : null,
        state.confirming ? BossModMarketplaceDetail.confirmStrip({
            text: item.setup_label ? `${item.setup_label}?` : COPY.runSetup,
            id: 'ext-setup-confirm',
            label: COPY.setUp,
            tone: 'primary',
            onConfirm: () => handlers.onSetupConfirm(),
            onCancel: () => handlers.onSetupCancel(),
        }) : null,
        hero(item, state, handlers),
        setupLine(item, state, handlers),
        !item.valid ? h('p', { class: 'market-detail-note' }, item.invalid_reason) : null,
        item.valid && item.enabled && item.service_error
            ? h('p', { class: 'market-error', role: 'alert' }, item.service_error) : null,
        item.description ? h('p', { class: 'market-detail-lead' }, item.description) : null,
        reader ? h('hr', { class: 'market-detail-rule' }) : null,
        reader);
    }

    return { COPY, render };
})();
