/**
 * BossMod AI — Settings → System Settings → Context Window, the layout.
 *
 * One context window, shared out in tokens (core/context_budget.py): a Model
 * card (starting points, the window, the fixed prompt room, the two shared
 * shares), then Chat mode and Work mode side by side, each with a bar of its
 * largest possible request, then the settings that are in every prompt.
 *
 * Layout only. Every setting position is an empty `[data-setting-slot]`
 * that settings-system.js fills with its standard row and wires with its
 * standard save, so a field here saves exactly like any other setting. The
 * numbers come from GET /api/settings/context-budget. This module's own
 * behaviour is the starting-point buttons (wire). Loaded before
 * settings-system.js.
 */

const BossModContextPanel = (() => {
    const esc = BossModFormat.escapeHtml;
    const escAttr = BossModFormat.escapeAttribute;

    /** The two modes, in the words of the tab. Each names the budget fields its bar and rows use. */
    const MODES = [
        {
            id: 'chat',
            title: 'Chat mode',
            purpose: 'When you message an agent',
            worst: 'worst_chat_turn',
            worstPercent: 'worst_chat_percent',
            barLabel: 'A chat turn at its largest',
            segments: [
                { budget: 'reply', label: 'Reply', tone: 'reply' },
                { budget: 'history_cap', label: 'History, at its hard limit', tone: 'conversation' },
                { budget: 'asked', label: 'When you ask', tone: 'work' },
                { budget: 'result', label: 'Largest result', tone: 'result' },
            ],
        },
        {
            id: 'work',
            title: 'Work mode',
            purpose: 'While it works on a task',
            worst: 'worst_work_turn',
            worstPercent: 'worst_work_percent',
            barLabel: 'A work turn at its largest',
            segments: [
                { budget: 'reply', label: 'Reply', tone: 'reply' },
                { budget: 'task_log', label: 'Task log', tone: 'conversation' },
                { budget: 'work', label: 'Work', tone: 'work' },
                { budget: 'result', label: 'Largest result', tone: 'result' },
            ],
        },
    ];

    // Complete class names per tone: the Tailwind drift guard reads literals only.
    const SEGMENT_CLASS = {
        fixed: 'ctx-budget-seg ctx-budget-seg-fixed',
        reply: 'ctx-budget-seg ctx-budget-seg-reply',
        conversation: 'ctx-budget-seg ctx-budget-seg-conversation',
        work: 'ctx-budget-seg ctx-budget-seg-work',
        result: 'ctx-budget-seg ctx-budget-seg-result',
        free: 'ctx-budget-seg ctx-budget-seg-free',
    };

    const PRESET_IDLE = 'system-category-tab px-4 py-2 rounded-lg text-sm font-medium border transition-colors bg-white text-bm-text border-bm-border hover:bg-slate-50';
    const PRESET_ACTIVE = 'system-category-tab px-4 py-2 rounded-lg text-sm font-medium border transition-colors bg-bm-accent text-white border-bm-accent shadow-sm';

    /** A token count as the tab writes it: `48,800`. */
    function tokens(n) {
        return Number(n).toLocaleString('en-US');
    }

    /** A share of the window as a CSS width, to two decimals. */
    function width(part, whole) {
        return `${(Math.max(0, part) * 100 / whole).toFixed(2)}%`;
    }

    /**
     * One setting position: the slot settings-system.js fills, and the number it works out to.
     * @param {string} key
     * @param {string} [derived]  e.g. `= 48,800 tokens`; omitted for settings that are tokens already.
     */
    function field(key, derived) {
        const line = derived
            ? `<span class="ctx-budget-derived" data-derived="${escAttr(key)}">${esc(derived)}</span>`
            : '';
        return `<div class="ctx-budget-field"><div data-setting-slot="${escAttr(key)}"></div>${line}</div>`;
    }

    function details(summary, keys) {
        return `
            <details class="ctx-budget-details">
                <summary>${esc(summary)}</summary>
                <div class="ctx-budget-details-body">${keys.map(key => field(key)).join('')}</div>
            </details>`;
    }

    function presetButton(preset, activeId) {
        const pressed = preset.id === activeId;
        return `<button type="button" data-context-preset="${escAttr(preset.id)}"
                        aria-pressed="${pressed ? 'true' : 'false'}"
                        class="${pressed ? PRESET_ACTIVE : PRESET_IDLE}">${esc(preset.label)}</button>`;
    }

    function modelCard(budget) {
        const custom = budget.active_preset ? '' : '<span class="ctx-budget-chip">Custom</span>';
        return `
            <section class="ctx-budget-card" aria-labelledby="ctx-budget-model-title">
                <h4 id="ctx-budget-model-title" class="ctx-budget-card-title">Model</h4>
                <div class="ctx-budget-presets" role="group" aria-labelledby="ctx-budget-presets-label">
                    <span id="ctx-budget-presets-label" class="ctx-budget-presets-label">Starting points</span>
                    ${budget.presets.map(preset => presetButton(preset, budget.active_preset)).join('')}
                    ${custom}
                </div>
                <p role="alert" class="text-xs text-red-600 mt-1" data-ctx-preset-error></p>
                <div class="ctx-budget-fields">
                    ${field('context_window_tokens')}
                    ${field('context_fixed_tokens')}
                    ${field('context_reply_percent', `= ${tokens(budget.reply)} tokens`)}
                    ${field('context_result_percent', `= ${tokens(budget.result)} tokens`)}
                </div>
                <p class="ctx-budget-usable">Usable window: ${esc(tokens(budget.usable))} tokens (context window minus fixed prompt room)</p>
            </section>`;
    }

    /**
     * A mode's largest request as a stacked bar: one segment per part, then
     * what is left free. Not colour-only: the bar's name spells out every
     * number, and the legend beside it shows them as text.
     */
    function bar(mode, budget) {
        const parts = [
            { label: 'Fixed prompt room', tone: 'fixed', value: budget.fixed },
            ...mode.segments.map(segment => ({ label: segment.label, tone: segment.tone, value: budget[segment.budget] })),
        ];
        parts.push({ label: 'Free', tone: 'free', value: Math.max(0, budget.window - budget[mode.worst]) });
        const spoken = parts.map(part => `${part.label} ${tokens(part.value)}`).join(', ');
        const name = `${mode.barLabel}, out of a ${tokens(budget.window)}-token window: ${spoken} tokens.`;
        const segments = parts.map(part => `<span class="${escAttr(SEGMENT_CLASS[part.tone])}" style="width: ${escAttr(width(part.value, budget.window))}"></span>`).join('');
        const legend = parts.map(part => `
                    <div class="ctx-budget-legend-item" data-tone="${escAttr(part.tone)}">
                        <dt><span class="${escAttr(SEGMENT_CLASS[part.tone])} ctx-budget-swatch" aria-hidden="true"></span>${esc(part.label)}</dt>
                        <dd>${esc(tokens(part.value))}</dd>
                    </div>`).join('');
        return `
            <div class="ctx-budget-bar" role="img" aria-label="${escAttr(name)}">${segments}</div>
            <dl class="ctx-budget-legend">${legend}</dl>`;
    }

    function chatRows(budget) {
        const v = budget.values;
        const summarizing = `Summarizing: at ${v.conversation_compaction_start_percent}%, keep ${v.conversation_compaction_keep_percent}% + ${v.conversation_notes_max_percent}% notes, hard limit ${v.conversation_history_ceiling_percent}%`;
        return {
            conversation: field('context_history_percent', `= ${tokens(budget.history)} tokens (up to ${tokens(budget.history_cap)})`)
                + details(summarizing, [
                    'conversation_compaction_enabled',
                    'conversation_compaction_start_percent',
                    'conversation_compaction_keep_percent',
                    'conversation_notes_max_percent',
                    'conversation_history_ceiling_percent',
                ]),
            work: field('context_asked_percent', `= ${tokens(budget.asked)} tokens`),
        };
    }

    function workRows(budget) {
        const v = budget.values;
        const summarizing = `Summarizing: keep ${v.work_snapshot_trim_percent}%, summary ${v.work_snapshot_summary_percent}%`;
        return {
            conversation: field('context_task_log_percent', `= ${tokens(budget.task_log)} tokens`),
            work: field('context_work_percent', `= ${tokens(budget.work)} tokens`)
                + details(summarizing, ['work_snapshot_trim_percent', 'work_snapshot_summary_percent']),
        };
    }

    /** One mode's column. Its children line up row for row with the other column's (CSS subgrid). */
    function modeColumn(mode, budget) {
        const rows = mode.id === 'chat' ? chatRows(budget) : workRows(budget);
        const titleId = mode.id === 'chat' ? 'ctx-budget-chat-title' : 'ctx-budget-work-title';
        return `
            <section class="ctx-budget-card ctx-budget-mode" aria-labelledby="${escAttr(titleId)}">
                <div class="ctx-budget-mode-head">
                    <h4 id="${escAttr(titleId)}" class="ctx-budget-card-title">${esc(mode.title)}</h4>
                    <p class="ctx-budget-purpose">${esc(mode.purpose)}</p>
                </div>
                <div class="ctx-budget-meter">${bar(mode, budget)}</div>
                <p class="ctx-budget-worst">Worst case: ${esc(tokens(budget[mode.worst]))} tokens (${esc(String(budget[mode.worstPercent]))}%)</p>
                <div class="ctx-budget-row">
                    <h5 class="ctx-budget-group">Conversation</h5>
                    ${rows.conversation}
                </div>
                <div class="ctx-budget-row">
                    <h5 class="ctx-budget-group">Its work</h5>
                    ${rows.work}
                </div>
            </section>`;
    }

    function alsoCard() {
        return `
            <section class="ctx-budget-card" aria-labelledby="ctx-budget-also-title">
                <h4 id="ctx-budget-also-title" class="ctx-budget-card-title">Also in every prompt</h4>
                <div class="ctx-budget-fields">
                    ${field('context_recent_work_artifacts')}
                    ${field('context_recent_completed_tasks')}
                    ${field('standing_prefs_line_max_chars')}
                    ${field('standing_prefs_section_max_chars')}
                </div>
            </section>`;
    }

    /**
     * The Context Window tab's markup, every text escaped.
     *
     * @param {object} budget  GET /api/settings/context-budget: the budgets in
     *   tokens, the worst cases, `values` (the stored percents), `presets` and
     *   `active_preset`.
     * @returns {string} The Model card, the Chat mode and Work mode columns,
     *   and the "Also in every prompt" card, with an empty
     *   `[data-setting-slot]` at every setting.
     */
    function layout(budget) {
        return `
            <div class="ctx-budget" data-ctx-budget>
                ${modelCard(budget)}
                <div class="ctx-budget-modes">
                    ${MODES.map(mode => modeColumn(mode, budget)).join('')}
                </div>
                ${alsoCard()}
            </div>`;
    }

    /** The server's reason for a refused request, or its status when it sent none. */
    async function refusal(res) {
        // A body that is not JSON has no reason in it; the status still says what happened.
        const payload = await res.json().catch(() => null);
        const detail = payload && payload.detail;
        return typeof detail === 'string' && detail.trim() ? detail : `The server answered ${res.status}.`;
    }

    /**
     * Wire the starting-point buttons. A click applies the preset at once (no
     * confirmation: every value stays visible and editable), then calls
     * `onApplied` so the tab re-renders with the new numbers. A refusal is
     * written to the Model card's alert line and the buttons come back.
     *
     * @param {HTMLElement} el  The rendered tab (layout's markup inside it).
     * @param {{budget: object, api: Function, onApplied: Function}} deps
     *   `api` is apiFetch, injected; `budget` names the presets.
     * @returns {void}
     */
    function wire(el, { budget, api, onApplied }) {
        const error = el.querySelector('[data-ctx-preset-error]');
        const buttons = el.querySelectorAll('[data-context-preset]');
        buttons.forEach(button => button.addEventListener('click', async () => {
            const id = button.dataset.contextPreset;
            const preset = budget.presets.find(item => item.id === id);
            buttons.forEach(other => { other.disabled = true; });
            error.textContent = '';
            try {
                const res = await api(`/api/settings/context-presets/${encodeURIComponent(id)}`, { method: 'POST' });
                if (!res.ok) throw new Error(await refusal(res));
            } catch (err) {
                error.textContent = `Couldn't apply ${preset ? preset.label : id}. ${(err && err.message) || err}`;
                buttons.forEach(other => { other.disabled = false; });
                return;
            }
            onApplied();
        }));
    }

    return { layout, wire };
})();
