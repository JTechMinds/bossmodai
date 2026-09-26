/**
 * BossMod AI — Settings → System Settings (HA-STRUCT-P1-04).
 */

const SystemSection = (() => {
    let container = null;
    let activeCategory = 'simulation';

    const CATEGORIES = [
        { key: 'simulation', label: 'Simulation' },
        { key: 'social',     label: 'Social Triggers' },
        { key: 'context',    label: 'Context Window' },
        { key: 'llm',        label: 'AI Output' },
        { key: 'desk',       label: 'Desk Settings' },
    ];

    const CATEGORY_DESCRIPTIONS = {
        simulation: 'Movement speed, simulation cadence, and recovery behavior for the office runtime.',
        social: 'Controls when idle agents may start optional social behavior based on time and proximity.',
        context: 'Controls how much recent conversation and work history is included in each agent turn.',
        llm: 'Controls global completion behavior and compaction pressure.',
        desk: 'Controls Desk preview behavior and filesystem browsing limits.',
    };

    const SETTING_META = {
        tick_interval: {
            order: 20,
            label: 'Tick Interval (seconds)',
            description: 'How often the world simulation updates. Lower values make movement and presence updates feel smoother, but increase backend and UI update frequency.',
        },
        movement_tiles_per_second: {
            order: 10,
            label: 'Movement Speed (tiles/sec)',
            description: 'How fast agents physically travel across the office. This affects arrival timing and should be tuned separately from tick interval.',
        },
        sim_error_threshold: {
            order: 30,
            label: 'Simulation Error Threshold',
            description: 'How many consecutive simulation loop failures are allowed before the engine pauses and enters backoff.',
        },
        sim_error_backoff_seconds: {
            order: 40,
            label: 'Simulation Error Backoff (seconds)',
            description: 'How long the simulation waits after repeated failures before it tries ticking again.',
        },
        watchdog_check_interval_seconds: {
            order: 50,
            label: 'Watchdog Check Interval (seconds)',
            description: 'How often the watchdog scans active tasks for silence or stalls.',
        },
        watchdog_soft_ping_minutes: {
            order: 60,
            label: 'Watchdog Soft Ping (minutes)',
            description: 'How long an active task can stay quiet before the system asks the agent for a status update.',
        },
        watchdog_escalation_minutes: {
            order: 70,
            label: 'Watchdog Escalation Delay (minutes)',
            description: 'How much additional quiet time is allowed after a soft ping before the task is marked stalled.',
        },
        meeting_watchdog_check_interval_seconds: {
            order: 72,
            label: 'Meeting Watchdog Check Interval (seconds)',
            description: 'How often the meeting watchdog scans assembling room meetings for invite and arrival timeouts.',
        },
        meeting_invite_accept_timeout_seconds: {
            order: 74,
            label: 'Meeting Invite Accept Timeout (seconds)',
            description: 'How long an invited agent may stay unanswered before the meeting marks them timed out.',
        },
        meeting_invite_arrival_timeout_seconds: {
            order: 76,
            label: 'Meeting Invite Arrival Timeout (seconds)',
            description: 'How long an accepted agent may take to arrive at the meeting room before they are marked timed out.',
        },
        thought_bubble_duration_ms: {
            order: 80,
            label: 'Thought Bubble Duration (ms)',
            description: 'How long agent thought bubbles display above agents on the canvas. Set to 0 to disable.',
        },
        social_idle_threshold_minutes: {
            order: 10,
            label: 'Idle Threshold (minutes)',
            description: 'How long an agent must stay idle before the system considers starting optional social behavior.',
        },
        social_cooldown_minutes: {
            order: 20,
            label: 'Social Cooldown (minutes)',
            description: 'Minimum time between automatic social prompts for the same agent.',
        },
        social_proximity_tiles: {
            order: 30,
            label: 'Proximity Radius (tiles)',
            description: 'How close agents must be on the map to count as nearby for social triggers.',
        },
        context_recent_work_artifacts: {
            order: 10,
            label: 'Recent Work Artifacts',
            description: 'How many recent work outputs or artifacts are included as reference material in the prompt.',
        },
        context_recent_completed_tasks: {
            order: 20,
            label: 'Recent Completed Tasks',
            description: 'How many recently completed task summaries are included as reference material in the prompt.',
        },
        standing_prefs_line_max_chars: {
            order: 30,
            label: 'Standing Pref Line Limit (chars)',
            description: 'Longest standing pref text an agent can save; that text is always shown whole in the prompt. Lowering it never hides existing prefs: longer ones are cut in the prompt and shown whole by pref list. Default 400.',
        },
        standing_prefs_section_max_chars: {
            order: 40,
            label: 'Standing Prefs Section Limit (chars)',
            description: 'Most characters of standing prefs injected into one agent turn, and the most total pref text one agent can store. Prefs past it are listed as \'more: N not shown\'. Must be at least the line limit. Default 4000.',
        },
        default_max_tokens: {
            order: 10,
            label: 'Default Max Completion Tokens',
            description: 'Global fallback output-token budget for one model completion when no provider-specific override is supplied. Default 16384.',
        },
        default_temperature: {
            order: 20,
            label: 'Default Temperature',
            description: 'Global fallback sampling temperature for model completions when no provider-specific override is supplied.',
        },
        llm_request_timeout_seconds: {
            order: 30,
            label: 'LLM Request Timeout (seconds)',
            description: 'Absolute limit for one model call, including a reply that is still producing tokens. Default 720 seconds (12 minutes). Idle silence is LLM Stall Timeout. On a decision turn, either abort counts toward Decision Repair Attempts, then the commitment is re-queued.',
        },
        llm_stall_timeout_seconds: {
            order: 31,
            label: 'LLM Stall Timeout (seconds)',
            description: 'Cancel a model call when an open stream produces no chunk for this many seconds, including the wait for the first chunk. Default 120. Chunks that keep arriving reset the timer, so a long reply is not cancelled before LLM Request Timeout. A call that has not opened a stream, and a provider path that cannot stream, use only that absolute limit.',
        },
        decision_repair_attempts: {
            order: 35,
            label: 'Decision Repair Attempts',
            description: 'How many times a decision turn may ask the model to replace a timeout, prose, invented keys, or broken JSON with one JSON envelope before the turn posts one thread note and re-queues the commitment.',
        },
        max_concurrent_agent_turns: {
            order: 36,
            label: 'Max concurrent model calls',
            description: 'How many model calls may run at once. Agent turns, System AI routes, and repairs share this budget. Default 2. Each agent still runs at most one turn. Repair wakes use a lane and wait behind a live channel lead. When a local server reports fewer parallel calls than this number, the connection test shows that as a health warning.',
        },
        system_ai_max_tokens: {
            order: 37,
            label: 'System AI Max Output Tokens',
            description: 'Output cap for every System AI completion: channel routing, chat fade, sticky slots, and CLI auto-approve. Reasoning models spend hidden reasoning tokens against this cap, so a small value cuts the answer off. Default 6144.',
        },
        compaction_mode: {
            order: 38,
            control: 'select',
            label: 'Compaction Mode',
            description: 'Off disables compaction. Pressure-only queues it when a context budget is tight. Compaction never runs every turn, and it never blocks the agent turn — it queues in the background.',
            options: [
                { value: 'off', label: 'Off' },
                { value: 'pressure_only', label: 'Pressure-only' },
            ],
        },
        compaction_task_budget_headroom_percent: {
            order: 39,
            label: 'Task Budget Headroom (%)',
            description: 'How much of the task context budget to leave free before pressure-only task sticky-slot fill may queue.',
        },
        compaction_chat_budget_headroom_percent: {
            order: 40,
            label: 'Chat Budget Headroom (%)',
            description: 'How much of the chat context budget to leave free before pressure-only compaction may queue.',
        },
        compaction_min_turns_between_runs: {
            order: 41,
            label: 'Min Turns Between Runs',
            description: 'Minimum agent turns that must pass between compaction runs. Compaction never runs every turn.',
        },
        compaction_cooldown_minutes: {
            order: 42,
            label: 'Cooldown (minutes)',
            description: 'Minimum minutes between compaction runs. Compaction queues in the background and never blocks the agent turn.',
        },
        managed_writer_max_batch_files: {
            order: 50,
            label: 'Batch Writer Max Files',
            description: 'Maximum number of files one managed batch-write request may generate before the runtime asks for smaller batches.',
        },
        managed_writer_max_sections_per_file: {
            order: 60,
            label: 'Writer Max Sections Per File',
            description: 'Maximum number of planned sections the managed writer may generate for one file before it requires a narrower scope.',
        },
        desk_preview_max_chars: {
            order: 10,
            label: 'Desk Preview Character Limit',
            description: 'Maximum number of characters loaded into the Desk file preview before the UI marks the preview as truncated.',
        },
    };

    const INPUT_CLASS = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white';

    function selectControl(setting, meta) {
        const options = meta.options || [];
        const known = options.some(opt => opt.value === setting.value);
        let html = options.map(opt => {
            const selected = setting.value === opt.value ? ' selected' : '';
            return `<option value="${BossModFormat.escapeAttribute(opt.value)}"${selected}>${BossModFormat.escapeHtml(opt.label)}</option>`;
        }).join('');
        if (setting.value && !known) {
            html += `<option value="${BossModFormat.escapeAttribute(setting.value)}" selected>${BossModFormat.escapeHtml(setting.value)}</option>`;
        }
        return `<select data-setting-key="${BossModFormat.escapeAttribute(setting.key)}"
                        data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"
                        class="${INPUT_CLASS}">${html}</select>`;
    }

    function settingControl(setting) {
        const meta = SETTING_META[setting.key] || {};
        if (meta.control === 'select') return selectControl(setting, meta);
        return `<input type="text"
                        data-setting-key="${BossModFormat.escapeAttribute(setting.key)}"
                        data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"
                        value="${BossModFormat.escapeAttribute(setting.value)}"
                        class="${INPUT_CLASS}">`;
    }

    async function render(el) {
        container = el;
        SettingsView.bindRepaint('system', () => render(container));
        let settings = [];
        try {
            const res = await apiFetch('/api/settings');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            settings = await res.json();
        } catch (err) {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load settings.</p>';
            return;
        }

        // Group by category, only show non-advanced categories
        const groups = {};
        const shownCats = new Set(CATEGORIES.map(c => c.key));
        for (const s of settings) {
            if (!shownCats.has(s.category)) continue;
            if (!SETTING_META[s.key]) continue;
            if (s.key === 'steps_per_tick') continue;
            if (!groups[s.category]) groups[s.category] = [];
            groups[s.category].push(s);
        }

        for (const key of Object.keys(groups)) {
            groups[key].sort((a, b) => {
                const aOrder = SETTING_META[a.key]?.order ?? 999;
                const bOrder = SETTING_META[b.key]?.order ?? 999;
                if (aOrder !== bOrder) return aOrder - bOrder;
                return a.key.localeCompare(b.key);
            });
        }

        const availableCategories = CATEGORIES.filter(cat => (groups[cat.key] || []).length > 0);
        if (!availableCategories.some(cat => cat.key === activeCategory)) {
            activeCategory = availableCategories[0]?.key || 'simulation';
        }

        const activeItems = groups[activeCategory] || [];
        const activeCategoryMeta = CATEGORIES.find(cat => cat.key === activeCategory);

        let html = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">System Settings</h2>
                <p class="text-sm text-bm-muted mt-0.5">Configure runtime behavior, model output limits, and Desk browsing.</p>
            </div>
            <div class="max-w-7xl">
                <div class="mb-5 flex flex-wrap gap-2">`;

        for (const cat of availableCategories) {
            const active = cat.key === activeCategory;
            html += `
                    <button
                        type="button"
                        data-system-category="${BossModFormat.escapeAttribute(cat.key)}"
                        class="system-category-tab px-4 py-2 rounded-lg text-sm font-medium border transition-colors
                               ${active ? 'bg-bm-accent text-white border-bm-accent shadow-sm' : 'bg-white text-bm-text border-bm-border hover:bg-slate-50'}">
                        ${BossModFormat.escapeHtml(cat.label)}
                    </button>`;
        }

        html += `
                </div>
                <section class="border border-bm-border rounded-xl bg-white p-5 shadow-sm">
                    <div class="mb-4">
                        <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide">${BossModFormat.escapeHtml(activeCategoryMeta?.label || 'Settings')}</h3>
                        <p class="text-xs text-bm-muted mt-1">${BossModFormat.escapeHtml(CATEGORY_DESCRIPTIONS[activeCategory] || '')}</p>
                    </div>
                    <div class="grid grid-cols-1 2xl:grid-cols-2 gap-4">`;

        for (const s of activeItems) {
            const meta = SETTING_META[s.key] || {};
            const label = meta.label || s.key;
            const description = meta.description || 'System setting.';
            html += `
                    <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                        <label class="block text-sm font-medium mb-1">${BossModFormat.escapeHtml(label)}</label>
                        <p class="text-xs text-bm-muted mb-1.5">${BossModFormat.escapeHtml(description)}</p>
                        ${settingControl(s)}
                    </div>`;
        }

        html += `
                    </div>
                </section>
            </div>`;
        el.innerHTML = html;

        el.querySelectorAll('[data-system-category]').forEach(btn => {
            btn.addEventListener('click', () => {
                activeCategory = btn.dataset.systemCategory;
                render(container);
            });
        });

        el.querySelectorAll('.setting-input').forEach(input => {
            input.addEventListener('change', async (e) => {
                const key = e.target.dataset.settingKey;
                const category = e.target.dataset.settingCategory;
                const value = e.target.value;
                try {
                    await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`, {
                        method: 'PUT',
                    });
                    BossModOperatorInvalidate.notifyLocal(['system']);
                    e.target.classList.add('border-emerald-400');
                    setTimeout(() => e.target.classList.remove('border-emerald-400'), 1000);
                } catch {
                    e.target.classList.add('border-red-400');
                    setTimeout(() => e.target.classList.remove('border-red-400'), 1000);
                }
            });
        });
    }

    return { render };
})();

