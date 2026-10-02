/**
 * BossMod AI — Settings → AI Connections, the connection form.
 *
 * Create and edit one provider connection, test it before saving, and the
 * show / copy controls for the API-key field. Split out of
 * settings-connections.js in Phase 3C at the list-versus-form seam: the list
 * reads connections and the form writes one, and only the form ever puts a
 * secret on screen.
 *
 * The API-key field controls live here rather than beside the list because
 * this is the only markup that carries `[data-toggle-api-key]` and
 * `[data-copy-api-key]`; the list calls the binder through this module so
 * there is one copy of it.
 */
const BossModConnectionForm = (() => {
    /**
     * The levels a connection can offer, in the order the agent form lists
     * them. Keys are core/models/thinking.py's ThinkingLevel; the server
     * refuses any other.
     */
    const THINKING_LEVELS = Object.freeze([
        { key: 'off', label: 'Off' },
        { key: 'low', label: 'Low' },
        { key: 'medium', label: 'Medium' },
        { key: 'high', label: 'High' },
        { key: 'xhigh', label: 'Extra high' },
    ]);

    /**
     * Read the five thinking-level inputs into the map the server takes.
     *
     * @param {FormData} fd
     * @returns {object} level → parsed JSON object; blank levels are left out,
     *   so an all-blank form is `{}`.
     * @throws {Error} Naming the level whose text is not a JSON object.
     */
    function readThinkingLevels(fd) {
        const levels = {};
        for (const { key, label } of THINKING_LEVELS) {
            const text = String(fd.get(`thinking_level_${key}`) || '').trim();
            if (!text) continue;
            let parsed;
            try {
                parsed = JSON.parse(text);
            } catch {
                throw new Error(`Thinking level “${label}” is not valid JSON.`);
            }
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || !Object.keys(parsed).length) {
                throw new Error(`Thinking level “${label}” must be a non-empty JSON object.`);
            }
            levels[key] = parsed;
        }
        return levels;
    }

    /**
     * Copy a string to the clipboard and report the outcome.
     *
     * Shared by the API-key Copy button and the thinking-level examples, so
     * both say "Copied" / "Copy failed" the same way.
     *
     * @param {string} value  The text to copy; an empty value copies nothing.
     * @param {Element|null} [statusEl]  Where to report; "Copied" clears
     *   itself after 1.5s, "Copy failed" stays.
     * @returns {Promise<void>}
     */
    async function copyText(value, statusEl = null) {
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            if (statusEl) {
                statusEl.textContent = 'Copied';
                setTimeout(() => {
                    if (statusEl.textContent === 'Copied') statusEl.textContent = '';
                }, 1500);
            }
        } catch {
            if (statusEl) statusEl.textContent = 'Copy failed';
        }
    }

    function bindApiKeyFieldControls(root = document) {
        root.querySelectorAll('[data-toggle-api-key]').forEach(btn => {
            btn.addEventListener('click', () => {
                const targetId = btn.dataset.toggleApiKey;
                const input = document.getElementById(targetId);
                if (!input) return;
                const isHidden = input.type === 'password';
                input.type = isHidden ? 'text' : 'password';
                btn.textContent = isHidden ? 'Hide' : 'Show';
            });
        });

        root.querySelectorAll('[data-copy-api-key]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const targetId = btn.dataset.copyApiKey;
                const source = document.getElementById(targetId);
                const statusEl = btn.dataset.copyStatus ? document.getElementById(btn.dataset.copyStatus) : null;
                if (!source) return;
                await copyText(source.value, statusEl);
            });
        });
    }

    /**
     * Render the create / edit form over the section container.
     *
     * @param {object|null} conn  The connection to edit, or null to create.
     *   An existing connection never carries its full key — only the last four
     *   digits — so a blank key field means "keep the saved one".
     *   `supports_images` seeds the "Supports images" switch; the create and
     *   the update payload send it only after the switch was toggled.
     * @param {object} options
     * @param {Element} options.container  The section's content element; the
     *   form replaces the list in place, as it did before the split.
     * @param {() => Promise<void>} options.onDone  Return to the list, on
     *   cancel and after a successful save.
     * @returns {void}
     */
    function renderForm(conn, { container, onDone }) {
        const isEdit = !!conn;
        container.innerHTML = `
            <div class="max-w-lg">
                <h2 class="text-lg font-semibold mb-1">${isEdit ? 'Edit Connection' : 'New Connection'}</h2>
                <p class="text-sm text-bm-muted mb-6">${isEdit ? 'Update this connection.' : 'Add a new LLM provider connection.'}</p>
                <form id="connection-form" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Connection Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">This is what you'll see when selecting a connection for an agent.</p>
                        <input type="text" name="name" required
                               value="${BossModFormat.escapeAttribute(conn?.name || '')}"
                               placeholder="e.g. OpenAI Production"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">API Base URL</label>
                        <p class="text-xs text-bm-muted mb-1.5">The exact provider base URL. Use something like <code>https://api.openai.com/v1</code>, not <code>/chat/completions</code>.</p>
                        <input type="url" name="api_base_url" required
                               value="${BossModFormat.escapeAttribute(conn?.api_base_url || '')}"
                               placeholder="https://api.openai.com/v1"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">API Key</label>
                        <p class="text-xs text-bm-muted mb-1.5">${isEdit && conn?.has_api_key
                            ? `A key is saved (last 4: ${BossModFormat.escapeHtml(conn.api_key_last4 || '')}). Leave blank to keep it, or enter a new key to rotate.`
                            : 'Optional. Leave blank for local OpenAI-compatible servers. The runtime supplies a harmless transport placeholder when the upstream library requires one.'}</p>
                        <div class="flex gap-2">
                            <input id="connection-api-key-input" type="password" name="api_key"
                                   value=""
                                   placeholder="${isEdit && conn?.has_api_key ? '••••' + BossModFormat.escapeAttribute(conn.api_key_last4 || '') : 'sk-...'}"
                                   class="flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg
                                          bg-bm-bg">
                            <button type="button"
                                    data-toggle-api-key="connection-api-key-input"
                                    class="px-3 py-2 border border-bm-border rounded-lg hover:bg-slate-50 transition-colors text-sm font-medium">
                                Show
                            </button>
                            <button type="button"
                                    data-copy-api-key="connection-api-key-input"
                                    data-copy-status="connection-api-key-status"
                                    class="px-3 py-2 border border-bm-border rounded-lg hover:bg-slate-50 transition-colors text-sm font-medium">
                                Copy
                            </button>
                        </div>
                        <p id="connection-api-key-status" class="text-[11px] text-bm-muted mt-1"></p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Model Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">Model name exposed by the server. Raw names like <code>llama3</code> work for local OpenAI-compatible endpoints; provider-prefixed names also work.</p>
                        <input type="text" name="model"
                               value="${BossModFormat.escapeAttribute(conn?.model || '')}"
                               placeholder="e.g. llama3 or openai/gpt-4.1-mini"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg">
                        <div id="connection-supports-images" class="mt-2"></div>
                        <p class="text-xs text-bm-muted mt-1">Applies to every agent using this model. Names match exactly: <code>gpt-4o</code> and <code>openai/gpt-4o</code> are separate.</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Extra Body Params</label>
                        <p class="text-xs text-bm-muted mb-1.5">Optional JSON merged into every request body. For provider-specific fields.</p>
                        <textarea name="extra_body" rows="3"
                                  placeholder='e.g. {"stream": false, "thinking": {"type": "disabled"}}'
                                  class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                         bg-bm-bg font-mono">${BossModFormat.escapeHtml(conn?.extra_body || '')}</textarea>
                    </div>
                    <fieldset>
                        <legend class="block text-sm font-medium mb-1">Thinking levels</legend>
                        <p class="text-xs text-bm-muted mb-1.5">Optional. The JSON for each level is deep-merged over Extra Body for agents that pick that level. Leave a level blank to not offer it.</p>
                        <div class="space-y-2">
                            ${THINKING_LEVELS.map(({ key, label }) => `<div>
                                <label for="connection-thinking-${BossModFormat.escapeAttribute(key)}" class="block text-xs font-medium mb-1">${BossModFormat.escapeHtml(label)}</label>
                                <input type="text" id="connection-thinking-${BossModFormat.escapeAttribute(key)}" name="thinking_level_${BossModFormat.escapeAttribute(key)}"
                                       value="${BossModFormat.escapeAttribute(conn?.thinking_levels?.[key] ? JSON.stringify(conn.thinking_levels[key]) : '')}"
                                       placeholder='e.g. {"thinking": {"type": "${key === 'off' ? 'disabled' : 'enabled'}"}}'
                                       class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                              bg-bm-bg font-mono">
                            </div>`).join('')}
                        </div>
                        ${BossModThinkingExamples.renderPanel(THINKING_LEVELS)}
                    </fieldset>
                    <div id="connection-save-status" class="hidden p-3 rounded-lg text-sm"></div>
                    <div id="test-conn-result" class="hidden p-3 rounded-lg text-sm"></div>
                    <div class="flex gap-2 pt-2">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            ${isEdit ? 'Save Changes' : 'Create Connection'}
                        </button>
                        <button type="button" id="btn-test-conn"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Test Connection
                        </button>
                        <button type="button" id="btn-cancel-conn"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        document.getElementById('btn-cancel-conn').addEventListener('click', onDone);
        bindApiKeyFieldControls(container);
        BossModThinkingExamples.bindPanel(container, copyText);

        // Image support is keyed by model name, so it means nothing until a
        // model is named. The switch is the project's one toggle component.
        // The flag is shared by every connection naming the model, so it is
        // only sent once the operator has actually toggled it here.
        let supportsImages = conn?.supports_images === true;
        let imagesToggled = false;
        const imagesSwitch = BossModSwitch.create({
            label: 'Supports images',
            pressed: supportsImages,
            onChange: (pressed) => { supportsImages = pressed; imagesToggled = true; },
        });
        document.getElementById('connection-supports-images').append(imagesSwitch.element);
        const modelInput = document.querySelector('#connection-form [name="model"]');
        function syncImagesSwitch() {
            const hasModel = String(modelInput.value || '').trim() !== '';
            imagesSwitch.element.disabled = !hasModel;
            if (!hasModel && supportsImages) {
                supportsImages = false;
                imagesSwitch.set(false);
            }
        }
        modelInput.addEventListener('input', syncImagesSwitch);
        syncImagesSwitch();

        document.getElementById('btn-test-conn').addEventListener('click', async () => {
            const form = document.getElementById('connection-form');
            const fd = new FormData(form);
            const resultEl = document.getElementById('test-conn-result');

            resultEl.className = 'p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
            resultEl.textContent = 'Testing connection...';
            resultEl.classList.remove('hidden');

            try {
                const resp = await apiFetch('/api/connections/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_base_url: fd.get('api_base_url'),
                        api_key: fd.get('api_key') || null,
                        model: fd.get('model') || null,
                        connection_id: isEdit ? conn.id : null,
                    }),
                });
                const result = await resp.json().catch(() => ({}));

                if (!resp.ok || !result.ok) {
                    const formatError = window.BossModApi && window.BossModApi.formatError;
                    const message = (formatError
                        ? formatError(result, resp.status)
                        : (result.error || result.detail || `Test failed (${resp.status})`));
                    resultEl.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    resultEl.textContent = message;
                } else {
                    const isWarning = !!result.warning;
                    resultEl.className = isWarning
                        ? 'p-3 rounded-lg text-sm bg-amber-50 border border-amber-200 text-amber-700'
                        : 'p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';

                    const msg = isWarning
                        ? result.warning
                        : `Connected — ${result.models_count} model${result.models_count !== 1 ? 's' : ''} available`;

                    let html = `<p class="font-medium">${BossModFormat.escapeHtml(msg)}</p>`;
                    if (result.models && result.models.length > 0) {
                        html += `<p class="mt-2 mb-1 text-xs font-semibold opacity-70 uppercase tracking-wide">Available models</p>`;
                        html += `<div class="flex flex-wrap gap-1.5">`;
                        for (const m of result.models) {
                            html += `<span class="px-2 py-0.5 rounded text-xs font-mono ${isWarning ? 'bg-amber-100' : 'bg-emerald-100'}">${BossModFormat.escapeHtml(m)}</span>`;
                        }
                        if (result.models_count > result.models.length) {
                            html += `<span class="px-2 py-0.5 text-xs opacity-60">+${result.models_count - result.models.length} more</span>`;
                        }
                        html += `</div>`;
                    }
                    resultEl.innerHTML = html;
                }
            } catch {
                resultEl.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                resultEl.textContent = 'Request failed — check your network';
            }
        });

        document.getElementById('connection-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const status = document.getElementById('connection-save-status');
            let thinkingLevels;
            try {
                thinkingLevels = readThinkingLevels(fd);
            } catch (err) {
                status.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                status.textContent = err.message;
                status.classList.remove('hidden');
                return;
            }
            const data = {
                name: fd.get('name'),
                api_base_url: fd.get('api_base_url'),
                model: fd.get('model') || null,
                extra_body: fd.get('extra_body')?.trim() || null,
            };
            // An edit always sends the map, so clearing every level clears it
            // ({}); a create with none offered sends nothing.
            if (isEdit || Object.keys(thinkingLevels).length) data.thinking_levels = thinkingLevels;
            if (imagesToggled) data.supports_images = supportsImages;
            const enteredKey = fd.get('api_key');
            if (enteredKey) {
                data.api_key = enteredKey;
            } else if (!isEdit) {
                data.api_key = null;
            }
            if (status) {
                status.className = 'p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
                status.textContent = 'Saving...';
                status.classList.remove('hidden');
            }
            try {
                if (isEdit) {
                    await apiFetchOk(`/api/connections/${conn.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await apiFetchOk('/api/connections', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                BossModOperatorInvalidate.notifyLocal(['connections']);
                await onDone();
                // BossModApp died with the dock shell; the banner moved to
                // shell/banners.js. The old typeof guard silently swallowed
                // this call, so the no-model banner outlived the change
                // that fixed it. Unguarded on purpose: a missing module is
                // a defect, not a condition to tiptoe around.
                void BossModBanners.refreshModelAvailability();
            } catch (err) {
                console.error('[Connections] Save failed:', err);
                if (status) {
                    status.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    status.textContent = err.message || 'Save failed';
                    status.classList.remove('hidden');
                }
            }
        });
    }

    return { renderForm, bindApiKeyFieldControls };
})();
