/**
 * BossMod AI — Add → Extensions: one card per extension, with its switch.
 *
 * A `panel` modal from core/overlays.js (layered, never a slide-out). Cards
 * are built here from the marketplace's `market-card*` classes rather than
 * reusing BossModPackCard.packCard: a pack card is one whole-card <button>,
 * and a switch inside a button is invalid HTML.
 *
 * Card states, all driven by the item the server returns:
 *   invalid               → the reason, no controls;
 *   setup missing         → switch off; turning it on asks first (the
 *                           marketplace's inline confirm strip), then starts
 *                           the download with enable-on-success;
 *   installing            → switch disabled, a polite live line, and the list
 *                           is re-read every 2 s while the dialog is open;
 *   failed                → the error and Retry;
 *   ready / not required  → the switch drives PUT enabled, rolled back on a
 *                           refusal with the server's message on the card;
 *   enabled + excluded    → which agents cannot use it, and why.
 *
 * An extension with per-agent settings (`agent_config`) says where they are
 * set: each agent's desk, not this dialog.
 *
 * A live view is not shown here: it is the screen button in each browsing
 * agent's chat header. After a successful toggle this asks
 * BossModBrowserVisionStatus to re-read, so those buttons follow at once.
 */
const BossModExtensionsDialog = (() => {
    const { h, clear } = BossModDom;
    const API = BossModExtensionsApi;
    const TITLE = 'Extensions';
    const POLL_MS = 2000;

    const COPY = Object.freeze({
        loading: 'Loading extensions…',
        failed: 'Couldn’t load extensions.',
        retry: 'Try again',
        empty: 'No extensions installed. Drop an extension folder into extensions/ and restart BossMod to load it.',
        unavailable: 'Unavailable',
        installing: 'Downloading…',
        retrySetup: 'Retry',
        confirm: 'Download',
        excludedLead: 'Not available to: ',
        excludedTail: ' — their models aren’t marked image-capable (Settings → Connections).',
        noModel: 'no model',
        perAgentLead: 'Set up per agent: open an agent’s desk → Extensions → ',
    });

    /**
     * Open the Extensions dialog.
     *
     * @returns {void}
     */
    function open() {
        const host = h('div', { class: 'market-grid' });
        const state = {
            status: 'loading',
            items: [],
            error: '',
            // Id whose "download first?" strip is showing.
            confirming: null,
            // Id with a request in flight; its controls are disabled.
            busy: null,
            // Id → the last refusal, shown on that card.
            errors: {},
            // Element id to focus after the next render.
            focus: null,
        };
        let timer = null;
        let closed = false;

        BossModOverlays.createModal({
            title: TITLE,
            body: host,
            actions: [],
            size: 'panel',
            onClose: () => {
                closed = true;
                clearTimeout(timer);
            },
        });

        function replaceItem(item) {
            state.items = state.items.map((existing) => (existing.id === item.id ? item : existing));
        }

        function schedulePoll() {
            clearTimeout(timer);
            const installing = state.items.some((item) => item.setup && item.setup.state === 'installing');
            if (!closed && installing) timer = setTimeout(() => load(false), POLL_MS);
        }

        async function load(showLoading) {
            if (showLoading) {
                state.status = 'loading';
                render();
            }
            try {
                state.items = await API.listExtensions();
                state.status = state.items.length ? 'ready' : 'empty';
                state.error = '';
            } catch (err) {
                state.status = 'failed';
                state.error = String((err && err.message) || err);
            }
            if (closed) return;
            render();
            schedulePoll();
        }

        async function toggleEnabled(item, pressed) {
            state.busy = item.id;
            state.errors[item.id] = '';
            state.focus = switchId(item.id);
            render();
            try {
                replaceItem(await API.setEnabled(item.id, pressed));
                void BossModBrowserVisionStatus.refresh();
            } catch (err) {
                // Never show a state the server refused: the item is unchanged,
                // so the re-render puts the switch back where it was.
                state.errors[item.id] = String((err && err.message) || err);
            }
            state.busy = null;
            // Back onto the switch, which was disabled (so the card held focus)
            // while the request ran.
            state.focus = switchId(item.id);
            if (!closed) render();
        }

        async function beginSetup(item) {
            state.confirming = null;
            state.busy = item.id;
            state.errors[item.id] = '';
            state.focus = cardId(item.id);
            render();
            try {
                replaceItem(await API.startSetup(item.id, true));
            } catch (err) {
                state.errors[item.id] = String((err && err.message) || err);
            }
            state.busy = null;
            if (closed) return;
            render();
            schedulePoll();
        }

        function controls(item) {
            const setup = item.setup || { state: 'not_required' };
            const busy = state.busy === item.id;
            const rows = [];

            if (setup.state === 'missing') {
                const confirming = state.confirming === item.id;
                rows.push(switchFor(item, confirming, busy || confirming, (pressed) => {
                    if (!pressed) return;
                    state.confirming = item.id;
                    state.focus = `ext-confirm-${item.id}`;
                    render();
                }));
                if (confirming) {
                    rows.push(BossModMarketplaceDetail.confirmStrip({
                        text: `${item.setup_label || 'Run setup'}?`,
                        id: `ext-confirm-${item.id}`,
                        label: COPY.confirm,
                        tone: 'primary',
                        onConfirm: () => beginSetup(item),
                        onCancel: () => {
                            state.confirming = null;
                            state.focus = switchId(item.id);
                            render();
                        },
                    }));
                }
            } else if (setup.state === 'installing') {
                rows.push(switchFor(item, item.enabled, true, () => {}));
                rows.push(h('p', { class: 'market-status', 'aria-live': 'polite' }, COPY.installing));
            } else if (setup.state === 'failed') {
                rows.push(switchFor(item, false, true, () => {}));
                rows.push(h('p', { class: 'market-failed', role: 'alert' }, setup.detail || COPY.failed));
                rows.push(h('button', {
                    class: 'btn btn-sm', type: 'button', id: `ext-retry-${item.id}`,
                    disabled: busy, onclick: () => beginSetup(item),
                }, COPY.retrySetup));
            } else {
                rows.push(switchFor(item, item.enabled, busy, (pressed) => toggleEnabled(item, pressed)));
            }

            const excluded = item.excluded_agents || [];
            if (item.enabled && excluded.length) {
                const names = excluded
                    .map((agent) => `${agent.name} (${agent.model || COPY.noModel})`)
                    .join(', ');
                rows.push(h('p', { class: 'market-card-note' }, COPY.excludedLead + names + COPY.excludedTail));
            }
            if (state.errors[item.id]) {
                rows.push(h('p', { class: 'market-failed', role: 'alert' }, state.errors[item.id]));
            }
            return rows;
        }

        function switchFor(item, pressed, disabled, onChange) {
            const toggle = BossModSwitch.create({ label: `Enable ${item.name}`, pressed, onChange });
            toggle.element.setAttribute('id', switchId(item.id));
            if (disabled) toggle.element.setAttribute('disabled', '');
            return toggle.element;
        }

        function card(item) {
            const titleId = `ext-title-${item.id}`;
            const meta = [];
            if (item.version) meta.push(h('span', { class: 'market-card-tag' }, `v${item.version}`));
            if (!item.valid) meta.push(h('span', { class: 'market-card-tag' }, COPY.unavailable));
            return h('article', {
                class: 'market-card', id: cardId(item.id), tabindex: '-1', 'aria-labelledby': titleId,
            },
            h('div', { class: 'market-card-head' },
                h('h3', { class: 'market-card-title', id: titleId }, item.name)),
            meta.length ? h('div', { class: 'market-card-meta' }, meta) : null,
            item.command
                ? h('p', { class: 'market-card-specialty' }, `${item.command.name} — ${item.command.summary}`)
                : null,
            item.description ? h('p', { class: 'market-card-desc' }, item.description) : null,
            item.agent_config
                ? h('p', { class: 'market-card-note' }, COPY.perAgentLead + item.agent_config.label + '.')
                : null,
            item.valid
                ? controls(item)
                : h('p', { class: 'market-card-note' }, item.invalid_reason || COPY.unavailable));
        }

        function content() {
            if (state.status === 'loading') {
                return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
            }
            if (state.status === 'failed') {
                return [
                    h('p', { class: 'market-failed', role: 'alert' }, state.error || COPY.failed),
                    h('button', {
                        class: 'market-action', id: 'ext-list-retry', type: 'button',
                        onclick: () => load(true),
                    }, COPY.retry),
                ];
            }
            if (state.status === 'empty') {
                return h('p', { class: 'market-status', role: 'status' }, COPY.empty);
            }
            return h('div', { class: 'market-cards' }, state.items.map(card));
        }

        function render() {
            const active = document.activeElement;
            const kept = active && host.contains(active) ? active.getAttribute('id') : null;
            const wanted = state.focus || kept;
            state.focus = null;
            clear(host);
            const nodes = [].concat(content());
            nodes.forEach((node) => host.append(node));
            if (!wanted) return;
            // A control that was replaced by a disabled one (a switch while its
            // download runs) cannot take focus; its card can, so the keyboard
            // stays on the extension it was working with.
            let target = host.querySelector(`#${wanted}`);
            if (!target || target.hasAttribute('disabled')) {
                const cardNode = target && target.closest ? target.closest('.market-card') : null;
                target = cardNode || null;
            }
            if (target) target.focus();
        }

        load(true);
    }

    function switchId(id) {
        return `ext-switch-${id}`;
    }

    function cardId(id) {
        return `ext-card-${id}`;
    }

    return { open };
})();
