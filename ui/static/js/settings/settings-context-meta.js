/**
 * BossMod AI — Settings → System Settings → Context Window, the setting catalog.
 *
 * Every Context Window setting's label and description, plus the two fields
 * the generic catalog needs: `tab` for the two work settings stored under
 * `simulation` (db/settings.py), and `control` for the conversation-memory
 * switch. Data only: where each setting sits on the tab is
 * settings-context-panel.js's, and settings-system-meta.js merges this into
 * its catalog. Loaded before settings-system-meta.js.
 *
 * The labels are the ones the server's messages name (core/context_budget.py
 * SETTING_LABELS), so a refused save points at a field the operator can find.
 */

const BossModContextSettingsMeta = (() => {
    const SETTING_META = {
        context_window_tokens: {
            label: 'Context window (tokens)',
            description: 'The most your agents\' model can take in one request, prompt and reply together. If agents use different models, use the smallest window among them. Default 128000.',
        },
        context_reply_percent: {
            label: 'Reply (% of usable window)',
            description: 'The longest reply the model may write in one request. Default 12.',
        },
        context_result_percent: {
            label: 'Largest command result (% of usable window)',
            description: 'The most one command\'s output may add, a file read included. Longer output is cut and the agent is told how to read on. Default 5.',
        },
        context_history_percent: {
            label: 'History (% of usable window)',
            description: 'How much of a DM, thread or meeting an agent sees. Default 20.',
        },
        context_asked_percent: {
            label: 'When you ask (% of usable window)',
            description: 'When you message an agent mid-task, how much of its work it sees while it answers: its summary first, then its newest steps. Default 25.',
        },
        context_task_log_percent: {
            label: 'Task log (% of usable window)',
            description: 'While an agent works, how much of the task\'s log it sees: your comments on the task, its status lines and teammates\' results, newest first. Default 5.',
        },
        context_work_percent: {
            label: 'Work (% of usable window)',
            description: 'How much of its own work an agent keeps in view while working, including what you told it during the work. Past this, its older steps are summarized. Default 60.',
        },
        conversation_compaction_enabled: {
            control: 'switch',
            label: 'Conversation memory',
            description: 'When a DM or thread gets long, older messages are summarized into notes in the background, so agents keep the gist without losing the recent messages.',
        },
        conversation_compaction_start_percent: {
            label: 'Start summarizing at (% of history)',
            description: 'Summarizing starts in the background once a conversation reaches this share of History. Must be higher than Keep recent messages. Default 80.',
        },
        conversation_compaction_keep_percent: {
            label: 'Keep recent messages (% of history)',
            description: 'How much of the newest conversation always stays word for word. Default 50.',
        },
        conversation_notes_max_percent: {
            label: 'Notes size (% of history)',
            description: 'Longest the notes may be. Must be lower than Keep recent messages. Default 10.',
        },
        conversation_history_ceiling_percent: {
            label: 'Hard limit (% of history)',
            description: 'A safety net if summarizing keeps failing: older messages past this are left out of agents\' context. From 100 to 300. Default 125.',
        },
        // Stored under 'simulation' (db/settings.py); shown on Context Window.
        work_snapshot_trim_percent: {
            tab: 'context',
            label: 'Work kept after summarizing (% of work)',
            description: 'How much recent work stays in view after summarizing, the summary included. Must be higher than Work summary size. Default 60.',
        },
        work_snapshot_summary_percent: {
            tab: 'context',
            label: 'Work summary size (% of work)',
            description: 'Longest the summary of older work may be. Must be lower than Work kept after summarizing. Default 20.',
        },
        context_recent_work_artifacts: {
            label: 'Recent Work Artifacts',
            description: 'How many recent work outputs or artifacts are included as reference material in the prompt.',
        },
        context_recent_completed_tasks: {
            label: 'Recent Completed Tasks',
            description: 'How many recently completed task summaries are included as reference material in the prompt.',
        },
        standing_prefs_line_max_chars: {
            label: 'Memory Line Limit (chars)',
            description: 'Longest memory an agent can save. Agents are asked to aim for about 120 characters; this is the hard stop. Lowering it never hides existing memories: longer ones are cut in the prompt and shown whole by memory list. Default 400.',
        },
        standing_prefs_section_max_chars: {
            label: 'Memory Section Limit (chars)',
            description: 'Most characters of memory injected into one agent turn, and the most total memory text one agent can store. Memories past it are listed as \'more: N not shown\'. Must leave room for at least one full memory line. Default 4000.',
        },
    };

    return { SETTING_META };
})();
