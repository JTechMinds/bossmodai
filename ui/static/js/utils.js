/**
 * BossMod AI — Shared frontend utilities.
 *
 * Common helpers used across multiple modules to avoid duplication.
 */

const BossModUtils = (() => {

    // ─── HTML escaping ───

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ─── Agent data normalization ───

    function normalizeAgent(w) {
        return {
            id: w.id,
            name: w.name,
            role: w.role || null,
            description: w.description || null,
            done_fail_bar: w.done_fail_bar || null,
            x: w.x ?? 0,
            y: w.y ?? 0,
            color: w.color || '#3b82f6',
            status: w.status || 'idle',
            currentActivityKind: w.currentActivityKind || null,
            boundTaskId: w.boundTaskId || null,
            idle_since: w.idle_since || null,
        };
    }

    const SPECIALTY_PAIRS = [
        { work: ['write', 'draft', 'document', 'docs', 'copy', 'author', 'edit'], roles: ['writer', 'writing', 'editor', 'docs', 'author'], family: 'write' },
        { work: ['review', 'audit', 'test', 'qa', 'inspect'], roles: ['reviewer', 'auditor', 'qa', 'tester', 'review'], family: 'review' },
        { work: ['implement', 'code', 'build', 'fix', 'debug'], roles: ['engineer', 'developer', 'coder', 'eng'], family: 'implement' },
        { work: ['research', 'analyze', 'analysis'], roles: ['researcher', 'analyst'], family: 'research' },
        { work: ['design', 'mockup', 'ux'], roles: ['designer', 'design', 'ux'], family: 'design' },
    ];
    const SPECIALTY_CONFLICTS = {
        write: ['review'],
        review: ['write', 'design'],
        design: ['review', 'implement'],
        implement: ['design'],
    };
    const FINISH_LINE_DEFAULTS = {
        write: 'A named draft or document exists. Empty done does not count.',
        review: 'A checkable allow/deny (or tests/artifact) exists. Empty done does not count.',
        implement: 'Tests evidence or a named artifact exists. Empty done does not count.',
        research: 'A named findings note exists. Empty done does not count.',
        design: 'A named mockup or design file exists. Empty done does not count.',
        coordinate: 'A named plan or status note exists. Empty done does not count.',
    };
    const FALLBACK_FINISH_LINE = 'A checkable claim exists (tests, artifact, or allow/deny). Empty done does not count.';

    function inferWorkFamily(title, description) {
        const text = `${title || ''} ${description || ''}`.toLowerCase();
        if (!text.trim()) return null;
        const hits = [];
        for (const pair of SPECIALTY_PAIRS) {
            if (pair.work.some(word => text.includes(word))) hits.push(pair.family);
        }
        const unique = [...new Set(hits)];
        return unique.length === 1 ? unique[0] : null;
    }

    function specialtyFamily(role) {
        const text = (role || '').toLowerCase();
        if (!text) return null;
        if (['lead', 'pm', 'manager', 'coordinator', 'owner', 'director'].some(word => text.includes(word))) {
            return 'coordinate';
        }
        const hits = [];
        for (const pair of SPECIALTY_PAIRS) {
            if (pair.roles.some(word => text.includes(word))) hits.push(pair.family);
        }
        const unique = [...new Set(hits)];
        return unique.length === 1 ? unique[0] : null;
    }

    function suggestFinishLine(specialty, description) {
        const family = specialtyFamily(specialty) || inferWorkFamily(null, description);
        return FINISH_LINE_DEFAULTS[family] || FALLBACK_FINISH_LINE;
    }

    function specialtyMatch(role, title, description) {
        const work = inferWorkFamily(title, description);
        const family = specialtyFamily(role);
        if (!work || !family || family === 'coordinate') return 'unknown';
        if (family === work) return 'match';
        if ((SPECIALTY_CONFLICTS[family] || []).includes(work)) return 'mismatch';
        return 'unknown';
    }

    function specialtyRank(agent, title, description) {
        const status = specialtyMatch(agent?.role, title, description);
        if (status === 'match') return 0;
        if (status === 'mismatch') return 2;
        return 1;
    }

    const WORK_FAMILY_LABELS = {
        write: 'writing',
        review: 'review/audit',
        implement: 'implementation',
        research: 'research',
        design: 'design',
    };

    function specialtyWarningMessage(agent, title, description) {
        const status = specialtyMatch(agent?.role, title, description);
        if (status !== 'mismatch') return '';
        const name = agent?.name || 'This assignee';
        const role = agent?.role || 'unspecified specialty';
        const work = inferWorkFamily(title, description);
        const workLabel = WORK_FAMILY_LABELS[work] || 'this work';
        return `${name} is "${role}"; this work looks like ${workLabel}. Prefer a matching teammate, or assign anyway (you will be asked to confirm).`;
    }

    function doneClaimGuidance(task) {
        if (task?.done_claim_guidance) return task.done_claim_guidance;
        const bar = (task?.assigned_to_done_fail_bar || '').trim();
        const hasFiles = Boolean(task?.work_contract?.deliverables?.length);
        let base;
        if (hasFiles) {
            base = 'Complete requires the work-contract file path to exist (that file is the checkable claim).';
        } else {
            base = 'Complete/deliver requires a checkable claim: tests evidence, an artifact path that exists, or an allow/deny proof summary. Empty done is rejected.';
        }
        return bar ? `${base} What done looks like for this agent: ${bar}` : base;
    }

    function formatDoneClaim(claim) {
        if (!claim || typeof claim !== 'object') return '';
        const type = String(claim.type || 'proof').trim() || 'proof';
        const path = String(claim.path || '').trim();
        const evidence = String(claim.evidence || '').trim();
        if (path && evidence) return `${type} — ${path} — ${evidence}`;
        if (path) return `${type} — ${path}`;
        if (evidence) return `${type} — ${evidence}`;
        return type;
    }

    // ─── Status color mappings ───

    const STATUS_CONFIG = {
        waiting:       { hex: '#2563eb', classes: 'bg-blue-50 text-blue-700',      dot: 'bg-blue-500' },
        blocked:       { hex: '#ef4444', classes: 'bg-red-50 text-red-700',        dot: 'bg-red-500' },
        work_active:   { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500' },
        social_active: { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
        in_transit:    { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500' },
        idle:          { hex: '#94a3b8', classes: 'bg-slate-50 text-slate-600',     dot: 'bg-slate-400' },
    };

    const ACTIVITY_CONFIG = {
        assignment:   { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500', label: 'assignment' },
        break:        { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'break' },
        conversation: { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'conversation' },
        meeting:      { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500', label: 'meeting' },
        movement:     { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500', label: 'moving' },
        social:       { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'social' },
        work:         { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500', label: 'working' },
    };

    const DEFAULT_STATUS = STATUS_CONFIG.idle;

    function getDisplayState(status, currentActivityKind = null) {
        return ACTIVITY_CONFIG[currentActivityKind] || STATUS_CONFIG[status] || DEFAULT_STATUS;
    }

    function getStatusColor(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).hex;
    }

    function getStatusClasses(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).classes;
    }

    function getStatusDot(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).dot;
    }

    function getStatusLabel(status, currentActivityKind = null) {
        if (currentActivityKind && ACTIVITY_CONFIG[currentActivityKind]) {
            return ACTIVITY_CONFIG[currentActivityKind].label;
        }
        return status || 'idle';
    }

    // ─── Formatting helpers ───

    function formatRelativeTime(isoString) {
        if (!isoString) return '';
        const now = Date.now();
        const then = new Date(isoString).getTime();
        if (isNaN(then)) return '';
        const diffMs = now - then;
        if (diffMs < 0) return 'just now';
        const seconds = Math.floor(diffMs / 1000);
        if (seconds < 60) return 'just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `${days}d ago`;
        const months = Math.floor(days / 30);
        if (months < 12) return `${months}mo ago`;
        return `${Math.floor(months / 12)}y ago`;
    }

    function formatNumber(n) {
        if (n == null || isNaN(n)) return '0';
        const num = Number(n);
        if (num >= 1_000_000) return (num / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (num >= 1_000) return (num / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
        return String(num);
    }

    // ─── Modal factory ───

    function createModal({ maxWidth = 'max-w-lg', onClose = null } = {}) {
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4';

        const panel = document.createElement('div');
        panel.className = `w-full ${maxWidth} max-h-[85vh] flex flex-col rounded-xl border border-bm-border bg-white shadow-xl`;
        overlay.appendChild(panel);

        function close() {
            overlay.remove();
            document.removeEventListener('keydown', onKeyDown);
            if (typeof onClose === 'function') onClose();
        }

        function onKeyDown(e) {
            if (e.key === 'Escape') close();
        }

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });

        document.addEventListener('keydown', onKeyDown);
        document.body.appendChild(overlay);

        return { overlay, panel, close };
    }

    // ─── Overlay panel open/close ───

    function openOverlay(overlayId) {
        const overlay = document.getElementById(overlayId);
        if (!overlay) return;
        overlay.classList.remove('hidden');
        overlay.offsetHeight; // force reflow
        overlay.classList.add('open');
    }

    function closeOverlay(overlayId, panelId) {
        const overlay = document.getElementById(overlayId);
        const panel = document.getElementById(panelId);
        if (!overlay || !panel) return;

        overlay.classList.remove('open');

        let hidden = false;
        const hide = () => {
            if (hidden) return;
            hidden = true;
            if (!overlay.classList.contains('open')) {
                overlay.classList.add('hidden');
            }
        };
        panel.addEventListener('transitionend', hide, { once: true });
        setTimeout(hide, 400);
    }

    // ─── Async load generation ───

    function createLoadGeneration() {
        let current = 0;
        return {
            next() {
                current += 1;
                return current;
            },
            isCurrent(id) {
                return id === current;
            },
        };
    }

    // ─── Composer send (clear only after ack) ───

    function setComposerError(el, message) {
        if (!el) return;
        const text = String(message || '').trim();
        el.textContent = text;
        el.classList.toggle('hidden', !text);
    }

    function createComposerSendGate() {
        let inFlight = false;

        function busy() {
            return inFlight;
        }

        async function submit({
            input,
            sendBtn,
            send,
            applyIdleState,
            onSuccess,
            onError,
            canSubmit,
        } = {}) {
            if (inFlight) return { submitted: false, ok: false, reason: 'in-flight' };
            if (typeof canSubmit === 'function' && !canSubmit()) {
                return { submitted: false, ok: false, reason: 'blocked' };
            }
            const draft = String(input && input.value != null ? input.value : '').trim();
            if (!draft) return { submitted: false, ok: false, reason: 'empty' };
            if (typeof send !== 'function') {
                return { submitted: false, ok: false, reason: 'blocked' };
            }

            inFlight = true;
            if (sendBtn) sendBtn.disabled = true;
            if (input) input.disabled = true;

            try {
                await send(draft);
                if (input) {
                    input.value = '';
                    if (input.style) input.style.height = 'auto';
                }
                if (typeof onSuccess === 'function') onSuccess(draft);
                return { submitted: true, ok: true };
            } catch (err) {
                if (typeof onError === 'function') onError(err, draft);
                return { submitted: true, ok: false, error: err };
            } finally {
                inFlight = false;
                if (typeof applyIdleState === 'function') {
                    applyIdleState();
                } else {
                    if (sendBtn) sendBtn.disabled = false;
                    if (input) input.disabled = false;
                }
            }
        }

        return { busy, submit };
    }

    // ─── Agent-scoped chat typing indicator ───

    function createChatTypingController({ getMessagesEl, isActiveChat } = {}) {
        let typingAgentId = null;

        function ownerId() {
            return typingAgentId;
        }

        function removeEl() {
            const messagesEl = typeof getMessagesEl === 'function' ? getMessagesEl() : null;
            const fromList = messagesEl && typeof messagesEl.querySelector === 'function'
                ? messagesEl.querySelector('#chat-typing-indicator')
                : null;
            const el = fromList || (typeof document !== 'undefined'
                ? document.getElementById('chat-typing-indicator')
                : null);
            if (el) el.remove();
        }

        function sync() {
            const messagesEl = typeof getMessagesEl === 'function' ? getMessagesEl() : null;
            const active = Boolean(
                typingAgentId
                && messagesEl
                && typeof isActiveChat === 'function'
                && isActiveChat(typingAgentId)
            );
            if (!active) {
                removeEl();
                return false;
            }
            if (typeof messagesEl.querySelector === 'function'
                && messagesEl.querySelector('#chat-typing-indicator')) {
                return true;
            }
            if (typeof document === 'undefined' || typeof document.createElement !== 'function') {
                return false;
            }
            const indicator = document.createElement('div');
            indicator.id = 'chat-typing-indicator';
            indicator.dataset.agentId = String(typingAgentId);
            indicator.className = 'chat-msg from-agent mb-2 text-bm-muted italic';
            indicator.textContent = 'Thinking...';
            messagesEl.appendChild(indicator);
            messagesEl.scrollTop = messagesEl.scrollHeight;
            return true;
        }

        function show(agentId) {
            if (!agentId) return false;
            typingAgentId = agentId;
            return sync();
        }

        function hide(agentId) {
            if (agentId != null && typingAgentId != null
                && String(typingAgentId) !== String(agentId)) {
                return false;
            }
            typingAgentId = null;
            removeEl();
            return true;
        }

        return { show, hide, sync, ownerId };
    }

    return {
        escapeHtml,
        normalizeAgent,
        inferWorkFamily,
        specialtyFamily,
        suggestFinishLine,
        specialtyMatch,
        specialtyRank,
        specialtyWarningMessage,
        doneClaimGuidance,
        formatDoneClaim,
        getStatusColor,
        getStatusClasses,
        getStatusDot,
        getStatusLabel,
        formatRelativeTime,
        formatNumber,
        createModal,
        openOverlay,
        closeOverlay,
        createLoadGeneration,
        setComposerError,
        createComposerSendGate,
        createChatTypingController,
    };
})();
