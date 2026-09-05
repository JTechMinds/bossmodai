/**
 * BossMod AI — Settings → Telegram (HA-STRUCT-P1-04).
 */

const TelegramSection = (() => {
    let container = null;

    async function render(el) {
        container = el;

        let settings = [];
        try {
            const res = await apiFetch('/api/settings?category=telegram');
            settings = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load Telegram settings.</p>';
            return;
        }

        const getSetting = (key) => settings.find(s => s.key === key) || {};
        const get = (key) => getSetting(key).value || '';
        const isEnabled = get('telegram_enabled') === 'true';
        const tokenSetting = getSetting('telegram_bot_token');
        const hasToken = !!tokenSetting.has_value;
        const tokenLast4 = tokenSetting.value_last4 || '';
        const allowedUsers = get('telegram_allowed_user_ids');

        let html = `
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-lg font-semibold">Telegram Integration</h2>
                    <p class="text-sm text-bm-muted mt-0.5">
                        Connect a Telegram bot to chat with your agents from anywhere.
                    </p>
                </div>
            </div>

            <div class="max-w-lg space-y-5">

                <!-- Setup Guide -->
                <div class="rounded-xl border border-bm-border bg-white p-5 shadow-sm">
                    <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide mb-3">Setup</h3>
                    <ol class="text-sm text-bm-text space-y-2 list-decimal list-inside">
                        <li>Open Telegram and message <strong>@BotFather</strong></li>
                        <li>Send <code class="px-1.5 py-0.5 bg-slate-100 rounded text-xs">/newbot</code> and follow the prompts</li>
                        <li>Copy the bot token and paste it below</li>
                        <li>Add your Telegram user ID to the allowlist (required &mdash; empty means nobody can use the bot)</li>
                        <li>Enable the integration with the toggle</li>
                        <li>Restart BossMod &mdash; then send <code class="px-1.5 py-0.5 bg-slate-100 rounded text-xs">/start</code> to your bot in Telegram</li>
                    </ol>
                </div>

                <!-- Enable Toggle -->
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Enable Telegram Bot</h3>
                            <p class="text-xs text-bm-muted mt-0.5">
                                Start the bot on next app restart. Requires a valid bot token.
                            </p>
                        </div>
                        <button id="btn-toggle-telegram"
                                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors
                                       ${isEnabled ? 'bg-bm-accent' : 'bg-slate-300'}"
                                role="switch" aria-checked="${isEnabled}">
                            <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                                         ${isEnabled ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </button>
                    </div>
                </div>

                <!-- Bot Token -->
                <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                    <label class="block text-sm font-medium mb-1">Bot Token</label>
                    <p class="text-xs text-bm-muted mb-1.5">
                        The API token from @BotFather. Treated as a secret &mdash; the full value is never shown after save.
                        ${hasToken ? `A token is saved (last 4: ${BossModUtils.escapeHtml(tokenLast4)}). Leave blank to keep it.` : 'Saved on change.'}
                    </p>
                    <input type="password"
                           id="telegram-bot-token"
                           data-setting-key="telegram_bot_token"
                           data-setting-category="telegram"
                           data-has-secret="${hasToken ? 'true' : 'false'}"
                           value=""
                           placeholder="${hasToken ? '••••' + BossModUtils.escapeHtml(tokenLast4) : '123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ'}"
                           class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-white focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent font-mono">
                </div>

                <!-- Allowed User IDs -->
                <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                    <label class="block text-sm font-medium mb-1">Allowed User IDs</label>
                    <p class="text-xs text-bm-muted mb-1.5">
                        Required. Comma-separated Telegram user IDs that can interact with the bot.
                        An empty allowlist denies everyone and the bot will not start.
                        To find your user ID, search for
                        <strong>@userinfobot</strong> in Telegram and start a chat &mdash; it will reply with your ID.
                    </p>
                    <input type="text"
                           id="telegram-allowed-users"
                           data-setting-key="telegram_allowed_user_ids"
                           data-setting-category="telegram"
                           value="${BossModUtils.escapeHtml(allowedUsers)}"
                           placeholder="123456789, 987654321"
                           class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-white focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent">
                </div>

                <!-- Commands Reference -->
                <div class="rounded-xl border border-bm-border bg-white p-5 shadow-sm">
                    <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide mb-3">Bot Commands</h3>
                    <div class="text-sm space-y-1.5 font-mono">
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/agents</span>
                            <span class="text-bm-muted font-sans">List all agents with status</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/chat &lt;name&gt;</span>
                            <span class="text-bm-muted font-sans">Open or resume a DM session</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/chat &lt;n1&gt; &lt;n2&gt;</span>
                            <span class="text-bm-muted font-sans">Group chat with multiple agents</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/chat</span>
                            <span class="text-bm-muted font-sans">Close active session</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/group</span>
                            <span class="text-bm-muted font-sans">All-agent group channel (not a spatial office meeting)</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/meeting</span>
                            <span class="text-bm-muted font-sans">Legacy alias for /group</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/channels</span>
                            <span class="text-bm-muted font-sans">List active group chats</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/status</span>
                            <span class="text-bm-muted font-sans">Tasks, agents, pending approvals</span>
                        </div>
                        <div class="flex gap-3">
                            <span class="text-bm-accent shrink-0">/approve</span>
                            <span class="text-bm-muted font-sans">View and action pending approvals</span>
                        </div>
                    </div>
                </div>

                <!-- Status -->
                <div id="telegram-status" class="hidden p-3 rounded-lg text-sm"></div>
            </div>`;

        el.innerHTML = html;
        if (window.lucide) lucide.createIcons({ nodes: [el] });

        // ─── Toggle handler ───
        document.getElementById('btn-toggle-telegram').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const wasEnabled = btn.getAttribute('aria-checked') === 'true';
            const newValue = wasEnabled ? 'false' : 'true';
            const allowlist = (document.getElementById('telegram-allowed-users')?.value || '').trim();
            if (newValue === 'true' && !allowlist) {
                showStatus('Add at least one Telegram user ID before enabling the bot.', 'error');
                return;
            }

            try {
                if (newValue === 'true') {
                    const allowRes = await apiFetch(
                        '/api/settings/telegram_allowed_user_ids?value='
                        + encodeURIComponent(allowlist)
                        + '&category=telegram',
                        { method: 'PUT' },
                    );
                    if (!allowRes.ok) {
                        const err = await allowRes.json().catch(() => ({}));
                        showStatus(err.detail || 'Failed to save allowlist.', 'error');
                        return;
                    }
                }
                const res = await apiFetch('/api/settings/telegram_enabled?value=' + encodeURIComponent(newValue) + '&category=telegram', {
                    method: 'PUT',
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    showStatus(err.detail || 'Failed to update setting.', 'error');
                    return;
                }
                btn.setAttribute('aria-checked', String(!wasEnabled));
                btn.classList.toggle('bg-bm-accent', !wasEnabled);
                btn.classList.toggle('bg-slate-300', wasEnabled);
                btn.querySelector('span').classList.toggle('translate-x-6', !wasEnabled);
                btn.querySelector('span').classList.toggle('translate-x-1', wasEnabled);
                showStatus(!wasEnabled
                    ? 'Telegram enabled. Restart BossMod for changes to take effect.'
                    : 'Telegram disabled. Restart BossMod for changes to take effect.',
                    'info');
            } catch {
                showStatus('Failed to update setting.', 'error');
            }
        });

        // ─── Text input change handlers ───
        el.querySelectorAll('.setting-input').forEach(input => {
            input.addEventListener('change', async (e) => {
                const key = e.target.dataset.settingKey;
                const category = e.target.dataset.settingCategory;
                const value = e.target.value;
                if (key === 'telegram_bot_token' && !value && e.target.dataset.hasSecret === 'true') {
                    return;
                }
                try {
                    const res = await apiFetch(
                        '/api/settings/' + encodeURIComponent(key)
                        + '?value=' + encodeURIComponent(value)
                        + '&category=' + encodeURIComponent(category),
                        { method: 'PUT' },
                    );
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        e.target.classList.add('border-red-400');
                        setTimeout(() => e.target.classList.remove('border-red-400'), 1500);
                        showStatus(err.detail || 'Failed to save setting.', 'error');
                        return;
                    }
                    if (key === 'telegram_bot_token' && value) {
                        e.target.value = '';
                        e.target.dataset.hasSecret = 'true';
                    }
                    e.target.classList.add('border-emerald-400');
                    setTimeout(() => e.target.classList.remove('border-emerald-400'), 1500);
                    showStatus('Saved. Restart BossMod for changes to take effect.', 'success');
                } catch {
                    e.target.classList.add('border-red-400');
                    setTimeout(() => e.target.classList.remove('border-red-400'), 1500);
                    showStatus('Failed to save setting.', 'error');
                }
            });
        });
    }

    function showStatus(message, type) {
        const el = document.getElementById('telegram-status');
        if (!el) return;
        el.classList.remove('hidden');
        const colors = {
            success: 'bg-emerald-50 border border-emerald-200 text-emerald-700',
            error:   'bg-red-50 border border-red-200 text-red-700',
            info:    'bg-blue-50 border border-blue-200 text-blue-700',
        };
        el.className = 'p-3 rounded-lg text-sm ' + (colors[type] || colors.info);
        el.textContent = message;
        setTimeout(() => el.classList.add('hidden'), 4000);
    }

    return { render };
})();
