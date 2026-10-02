/**
 * BossMod AI — Settings → AI Connections, the thinking-level format examples.
 *
 * A collapsed reference panel under the connection form's Thinking levels
 * inputs. Formats belong to the server and its chat template, not to the
 * model name, so these are examples to copy from — never presets. Nothing
 * here writes into the level inputs, and nothing guesses a format from the
 * base URL.
 *
 * Split out of settings-connections-form.js to keep that module under the
 * 400-line cap. The form owns the level list and the clipboard helper; both
 * are passed in, so this module holds only the examples and their panel.
 */
const BossModThinkingExamples = (() => {
    /**
     * One entry per format family. Each `snippets` map has exactly the five
     * ThinkingLevel keys, as the JSON text an operator would paste.
     *
     * Claude is left out on purpose: it goes through a different litellm
     * path whose thinking format has not been checked.
     */
    const THINKING_FORMAT_EXAMPLES = Object.freeze([
        Object.freeze({
            title: 'Z.ai GLM',
            usedBy: 'Used by: Z.ai API',
            snippets: Object.freeze({
                off: '{"thinking":{"type":"disabled"}}',
                low: '{"thinking":{"type":"enabled"},"reasoning_effort":"low"}',
                medium: '{"thinking":{"type":"enabled"},"reasoning_effort":"medium"}',
                high: '{"thinking":{"type":"enabled"},"reasoning_effort":"high"}',
                xhigh: '{"thinking":{"type":"enabled"},"reasoning_effort":"xhigh"}',
            }),
        }),
        Object.freeze({
            title: 'OpenAI-style reasoning_effort',
            usedBy: 'Used by: OpenAI, llama.cpp server',
            snippets: Object.freeze({
                off: '{"reasoning_effort":"none"}',
                low: '{"reasoning_effort":"low"}',
                medium: '{"reasoning_effort":"medium"}',
                high: '{"reasoning_effort":"high"}',
                xhigh: '{"reasoning_effort":"xhigh"}',
            }),
        }),
        Object.freeze({
            title: 'Chat-template kwargs',
            usedBy: 'Used by: vLLM, llama.cpp with Qwen-style templates',
            snippets: Object.freeze({
                off: '{"chat_template_kwargs":{"enable_thinking":false}}',
                low: '{"chat_template_kwargs":{"enable_thinking":true,"reasoning_effort":"low"}}',
                medium: '{"chat_template_kwargs":{"enable_thinking":true,"reasoning_effort":"medium"}}',
                high: '{"chat_template_kwargs":{"enable_thinking":true,"reasoning_effort":"high"}}',
                xhigh: '{"chat_template_kwargs":{"enable_thinking":true,"reasoning_effort":"xhigh"}}',
            }),
        }),
    ]);

    const TOGGLE_ID = 'connection-thinking-examples-toggle';
    const CONTENT_ID = 'connection-thinking-examples-content';
    const STATUS_ID = 'connection-thinking-examples-status';

    /**
     * One family's snippet for one level.
     *
     * @param {number} familyIndex  Index into THINKING_FORMAT_EXAMPLES.
     * @param {string} levelKey  A ThinkingLevel key.
     * @returns {string} The JSON text.
     * @throws {Error} When the family or level does not exist — the panel is
     *   built from this same constant, so a miss is a defect, not a state.
     */
    function snippetFor(familyIndex, levelKey) {
        const family = THINKING_FORMAT_EXAMPLES[familyIndex];
        const snippet = family ? family.snippets[levelKey] : undefined;
        if (typeof snippet !== 'string') {
            throw new Error(`[thinking-examples] no snippet for family ${familyIndex}, level “${levelKey}”.`);
        }
        return snippet;
    }

    /**
     * The panel's markup: a collapsed disclosure holding the disclaimer, one
     * block per family and a shared status line for the Copy buttons.
     *
     * @param {ReadonlyArray<{key: string, label: string}>} levels  The form's
     *   level list, so rows follow its order and reuse its labels.
     * @returns {string} HTML for the panel; pair it with `bindPanel`.
     * @throws {Error} When a family has no snippet for one of `levels`.
     */
    function renderPanel(levels) {
        const families = THINKING_FORMAT_EXAMPLES.map((family, index) => `
            <div class="space-y-1">
                <p class="text-xs font-medium">${BossModFormat.escapeHtml(family.title)}</p>
                <p class="text-xs text-bm-muted">${BossModFormat.escapeHtml(family.usedBy)}</p>
                ${levels.map(({ key, label }) => `<div class="flex items-center gap-2">
                    <span class="w-20 shrink-0 text-xs text-bm-muted">${BossModFormat.escapeHtml(label)}</span>
                    <code class="flex-1 min-w-0 break-all text-xs font-mono">${BossModFormat.escapeHtml(snippetFor(index, key))}</code>
                    <button type="button"
                            data-copy-thinking-example="${BossModFormat.escapeAttribute(String(index))}"
                            data-example-level="${BossModFormat.escapeAttribute(key)}"
                            aria-label="${BossModFormat.escapeAttribute(`Copy ${family.title} ${label} example`)}"
                            class="px-2 py-1 border border-bm-border rounded-lg hover:bg-slate-50 transition-colors text-xs font-medium">
                        Copy
                    </button>
                </div>`).join('')}
            </div>`).join('');
        return `
            <div class="mt-3">
                <button type="button" id="${TOGGLE_ID}" class="advanced-toggle"
                        aria-expanded="false" aria-controls="${CONTENT_ID}">
                    <span>
                        <span class="advanced-title">Examples of common formats</span>
                    </span>
                    <i data-lucide="chevron-right" class="advanced-chevron"></i>
                </button>
                <div id="${CONTENT_ID}" class="hidden advanced-content mt-2">
                    <p class="text-xs text-bm-muted">Formats vary by server, model and chat template. These are starting points, not guarantees — check your provider's docs.</p>
                    ${families}
                    <p id="${STATUS_ID}" role="status" aria-live="polite" class="text-xs text-bm-muted"></p>
                </div>
            </div>`;
    }

    /**
     * Wire the panel `renderPanel` produced: the disclosure and the Copy
     * buttons. Copying only reaches the clipboard — the level inputs are
     * never touched.
     *
     * @param {Element} root  An ancestor of the rendered panel.
     * @param {(value: string, statusEl: Element) => Promise<void>} copyText
     *   The form's clipboard helper, which reports into the status line.
     * @returns {void}
     * @throws {Error} When the panel is not under `root`.
     */
    function bindPanel(root, copyText) {
        const toggle = root.querySelector(`#${TOGGLE_ID}`);
        const content = root.querySelector(`#${CONTENT_ID}`);
        const status = root.querySelector(`#${STATUS_ID}`);
        if (!toggle || !content || !status) {
            throw new Error('[thinking-examples] bindPanel called without a rendered panel.');
        }
        // The chevron's rotation is CSS keyed on aria-expanded, so painting
        // can replace the placeholder without leaving a stale reference.
        BossModIcons.paint(toggle, 'settings-thinking-examples');
        toggle.addEventListener('click', () => {
            const open = content.classList.toggle('hidden') === false;
            toggle.setAttribute('aria-expanded', String(open));
        });
        root.querySelectorAll('[data-copy-thinking-example]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const snippet = snippetFor(Number(btn.dataset.copyThinkingExample), btn.dataset.exampleLevel);
                await copyText(snippet, status);
            });
        });
    }

    return { THINKING_FORMAT_EXAMPLES, renderPanel, bindPanel };
})();
