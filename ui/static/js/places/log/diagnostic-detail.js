/**
 * BossMod AI — a Log row's expansion.
 *
 * Ported from diagnostics.js's right-hand panel, with one structural change
 * that is the whole point of the merge: it renders INLINE, beneath the row that
 * opened it (spec 6.6). The dock-era version hid the office canvas and took the
 * centre pane, so reading one turn meant leaving the feed.
 *
 * It is one renderer for both feeds. Every row arrives carrying its facts, and
 * a row that also carries a diagnostic id gets its execution trace fetched and
 * appended below them — that is a property of the row, not a branch on where
 * the row came from.
 */
const BossModDiagnosticDetail = (() => {
    const { h, clear } = BossModDom;

    const COPY_OK_MS = 1500;

    /**
     * Pretty-print a JSON payload for a <pre>.
     *
     * Ported verbatim from diagnostics.js, including the unescaping: agents
     * write prompts and results with real newlines inside JSON strings, and
     * leaving them as `\n` made every trace one unreadable line.
     *
     * @param {string|object} raw
     * @returns {string}
     */
    function formatJson(raw) {
        try {
            const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
            return JSON.stringify(parsed, null, 2)
                .replace(/\\n/g, '\n')
                .replace(/\\t/g, '\t');
        } catch (err) {
            // Not JSON — an engine payload is sometimes plain text, and showing
            // it is the answer rather than showing nothing.
            return String(raw);
        }
    }

    /**
     * One collapsible section with a copy button.
     *
     * @param {string} label
     * @param {string} body
     * @param {boolean} [isError]
     * @returns {HTMLElement}
     */
    function section(label, body, isError) {
        const pre = h('pre', { class: 'log-pre' }, body);
        const content = h('div', { class: 'log-section-body' }, pre);
        const status = h('span', { class: 'log-copy-status', role: 'status' });

        const toggle = h('button', {
            class: 'log-section-toggle', type: 'button', 'aria-expanded': 'true',
            onclick: () => {
                const open = content.hidden;
                content.hidden = !open;
                toggle.setAttribute('aria-expanded', String(open));
            },
        }, label);

        const copy = h('button', {
            class: 'log-copy', type: 'button', 'aria-label': `Copy ${label}`,
            onclick: () => {
                void navigator.clipboard.writeText(pre.textContent).then(() => {
                    status.textContent = 'Copied';
                    setTimeout(() => { status.textContent = ''; }, COPY_OK_MS);
                }).catch((err) => {
                    // The dock-era version logged this and left the operator
                    // wondering why nothing was on their clipboard.
                    console.warn('[log-detail] the clipboard refused', err);
                    status.textContent = 'Could not copy';
                });
            },
        }, 'Copy');

        return h('section', {
            class: `log-section${isError ? ' is-error' : ''}`,
        },
            h('div', { class: 'log-section-head' }, toggle, copy, status),
            content);
    }

    function factList(facts) {
        const list = h('dl', { class: 'log-facts' });
        facts.forEach((entry) => {
            list.append(
                h('dt', { class: 'log-fact-label' }, entry.label),
                h('dd', { class: 'log-fact-value' }, entry.value));
        });
        return list;
    }

    function traceStep(step) {
        const blocks = [
            step.context_snapshot ? section('Prompt Delta', formatJson(step.context_snapshot)) : null,
            step.raw_response ? section('Raw Response', String(step.raw_response)) : null,
            step.parsed_action ? section('Parsed Action', formatJson(step.parsed_action)) : null,
            step.result ? section('Execution Result', formatJson(step.result)) : null,
            step.error ? section('Error', String(step.error), true) : null,
        ].filter(Boolean);

        return h('article', { class: 'log-step', 'data-status': step.error ? 'error' : 'ok' },
            h('div', { class: 'log-step-head' },
                h('span', { class: 'log-step-index' }, `Step ${Number(step.step_index || 0)}`),
                h('span', { class: 'log-step-action' }, String(step.action_name || 'no action')),
                h('span', { class: 'log-step-badge' }, step.error ? 'Error' : 'OK'),
                h('span', { class: 'log-step-meta' },
                    `${step.prompt_tokens || 0} / ${step.completion_tokens || 0} / `
                    + `${step.total_tokens || 0} tok · ${step.duration_ms || 0}ms`)),
            ...blocks);
    }

    /**
     * The sections a fetched diagnostic contributes.
     *
     * @param {object} data  `GET /api/diagnostics/{id}`.
     * @returns {HTMLElement[]}
     */
    function detailSections(data) {
        const out = [];
        if (data.trigger_data) out.push(section('Trigger', formatJson(data.trigger_data)));
        if (Array.isArray(data.steps) && data.steps.length) {
            out.push(h('section', { class: 'log-trace' },
                h('h3', { class: 'log-trace-title' }, 'Execution Trace'),
                ...data.steps.map(traceStep)));
        }
        if (data.context) out.push(section('Context Sent', formatJson(data.context)));
        if (data.raw_response) out.push(section('Raw Response', String(data.raw_response)));
        if (data.parsed_action) out.push(section('Parsed Action', formatJson(data.parsed_action)));
        if (data.result) out.push(section('Execution Result', formatJson(data.result)));
        if (data.error) out.push(section('Error', String(data.error), true));
        if (out.length === 0) {
            out.push(h('p', { class: 'place-empty-hint' }, 'No detail data available.'));
        }
        return out;
    }

    /**
     * Build the detail renderer.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @returns {{render: (row: object) => HTMLElement}}
     * @throws {Error} When api is missing.
     */
    function createDetail(deps) {
        const { api } = deps || {};
        if (typeof api !== 'function') throw new Error('[log-detail] deps.api is required');

        /**
         * The expansion for one row, returned immediately.
         *
         * @param {object} row  A LogRow.
         * @returns {HTMLElement} Filled in place once the trace, if any,
         *   arrives — the row is already open, so a promise here would leave a
         *   blank panel with no explanation.
         */
        function render(row) {
            const element = h('div', { class: 'log-detail' });
            if (row.facts.length) element.append(factList(row.facts));
            if (row.json) element.append(section('Metadata', row.json));
            if (!row.diagnosticId) return element;

            const slot = h('div', { class: 'log-detail-slot' },
                h('p', { class: 'place-empty-hint' }, 'Loading detail…'));
            element.append(slot);

            function fail(message) {
                clear(slot);
                slot.append(
                    h('p', { class: 'log-detail-error', role: 'alert' }, message),
                    h('button', {
                        class: 'btn', type: 'button', onclick: () => { void load(); },
                    }, 'Try again'));
            }

            async function load() {
                clear(slot);
                slot.append(h('p', { class: 'place-empty-hint' }, 'Loading detail…'));
                let data;
                try {
                    const res = await api(
                        `/api/diagnostics/${encodeURIComponent(row.diagnosticId)}`,
                        { cache: 'no-store' });
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    data = await res.json();
                } catch (err) {
                    console.error('[log-detail] could not load the diagnostic', err);
                    fail(`Could not load this turn: ${(err && err.message) || 'the request failed.'}`);
                    return;
                }
                clear(slot);
                slot.append(...detailSections(data));
            }

            void load();
            return element;
        }

        return { render };
    }

    return { createDetail, formatJson, section, detailSections };
})();
