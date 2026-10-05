/**
 * BossMod AI — Settings → System Settings, the setting catalog.
 *
 * The tabs, their descriptions, and every setting's label, description,
 * order, tab and control kind. Split out of settings-system.js at the
 * data-versus-rendering seam: this module is data only, and
 * settings-system.js renders and saves it. Loaded before settings-system.js.
 */

const BossModSystemSettingsMeta = (() => {
    const CATEGORIES = [
        { key: 'simulation', label: 'Simulation' },
        { key: 'social',     label: 'Social Triggers' },
        { key: 'context',    label: 'Context Window' },
        { key: 'threads',    label: 'Threads' },
        { key: 'llm',        label: 'AI Output' },
        { key: 'desk',       label: 'Desk Settings' },
    ];

    const CATEGORY_DESCRIPTIONS = {
        simulation: 'Movement speed, simulation cadence, and recovery behavior for the office runtime.',
        social: 'Controls when idle agents may start optional social behavior based on time and proximity.',
        context: 'Controls how much recent conversation and work history is included in each agent turn.',
        threads: 'Controls who the System AI wakes in shared threads and how quiet threads are checked for unstarted work.',
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
        schedule_max_sleep_seconds: {
            order: 77,
            label: 'Schedule Clock Re-check (seconds)',
            description: 'Longest the schedule clock waits before re-reading the wall clock; it also catches up after the computer sleeps. Must be less than the on-time window.',
        },
        schedule_fire_grace_seconds: {
            order: 78,
            label: 'Schedule On-time Window (seconds)',
            description: 'How late a scheduled run may start and still run; later (for example after the computer slept) it is recorded as missed.',
        },
        thought_bubble_duration_ms: {
            order: 80,
            label: 'Thought Bubble Duration (ms)',
            description: 'How long agent thought bubbles display above agents on the canvas. Set to 0 to disable.',
        },
        // Stored under 'advanced' (db/settings.py); shown with the runtime cadence settings.
        runtime_command_fallback_poll_seconds: {
            order: 82,
            tab: 'simulation',
            label: 'Runtime Command Fallback Poll (seconds)',
            description: 'The app signals the runtime the moment it queues a command such as Pause. If that signal is ever lost, the runtime still checks its queue this often. Default 5.',
        },
        runtime_heartbeat_seconds: {
            order: 84,
            tab: 'simulation',
            label: 'Runtime Heartbeat Interval (seconds)',
            description: 'How often the runtime records that it is alive. A runtime silent for more than three intervals is reported as unhealthy. Default 5.',
        },
        world_state_coalesce_ms: {
            order: 86,
            tab: 'simulation',
            label: 'Office Update Batching (ms)',
            description: 'Office and roster changes that land within this window are sent to the screen as one update. Higher values save work on slower computers; lower values show changes sooner. Default 150.',
        },
        telegram_dispatch_queue_size: {
            order: 88,
            tab: 'simulation',
            label: 'Telegram Event Queue Size',
            description: 'How many runtime events may wait to be forwarded to Telegram. When Telegram is slow and the queue is full, new events are skipped for Telegram (and logged) so the app never waits on it. Applies after the app restarts. Default 200.',
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
            description: 'Most characters of standing prefs injected into one agent turn, and the most total pref text one agent can store. Prefs past it are listed as \'more: N not shown\'. Must leave room for at least one full pref line. Default 4000.',
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
        system_ai_timeout_seconds: {
            order: 37,
            label: 'System AI Timeout (seconds)',
            description: 'Longest one System AI call may take: thread routing, idle check, chat fade, sticky slots, and CLI auto-approve. A call that runs out falls back or is retried. Slow local models need more. Default 180.',
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
        channel_router_transcript_messages: {
            order: 10,
            tab: 'threads',
            label: 'Router Transcript Lines',
            description: 'How many earlier thread lines the System AI router reads before choosing who speaks next. 0 sends no history. Default 10.',
        },
        channel_response_round_cap: {
            order: 20,
            tab: 'threads',
            label: 'Round Cap per Message',
            description: 'Safety ceiling on response rounds for one operator message. Empty routes, pause, and demotion normally stop a thread long before this. Default 64.',
        },
        channel_idle_check_enabled: {
            order: 30,
            tab: 'threads',
            control: 'switch',
            label: 'Idle Check',
            description: 'When a thread goes quiet, the System AI checks whether an idle member committed to work they have not started, and privately wakes them with their own words. Nothing is posted unless they act.',
        },
        channel_idle_check_delay_seconds: {
            order: 40,
            tab: 'threads',
            label: 'Idle Check Delay (seconds)',
            description: 'How long a thread must be silent before it is checked. Any new line restarts the wait. Default 45.',
        },
        channel_idle_check_max_age_minutes: {
            order: 50,
            tab: 'threads',
            label: 'Idle Check Max Age (minutes)',
            description: 'A thread quiet longer than this is dormant and is not checked. Default 30.',
        },
        channel_idle_check_max_wakes: {
            order: 60,
            tab: 'threads',
            label: 'Idle Check Max Wakes',
            description: 'Most members one check may wake. Each member is woken at most once between operator messages. Default 2.',
        },
        channel_idle_check_max_attempts: {
            order: 65,
            tab: 'threads',
            label: 'Idle Check Attempts',
            description: 'How many times one quiet period is retried when the System AI gives no usable answer (busy, timed out, or malformed). Default 3.',
        },
        channel_idle_check_interval_seconds: {
            order: 70,
            tab: 'threads',
            label: 'Idle Check Scan Interval (seconds)',
            description: 'How often the runtime looks for quiet threads. Default 5.',
        },
        desk_preview_max_chars: {
            order: 10,
            label: 'Desk Preview Character Limit',
            description: 'Maximum number of characters loaded into the Desk file preview before the UI marks the preview as truncated.',
        },
    };

    return { CATEGORIES, CATEGORY_DESCRIPTIONS, SETTING_META };
})();
