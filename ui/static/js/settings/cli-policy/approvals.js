/**
 * BossMod AI — CLI Policy Approvals tab.
 *
 * Pending command-approval requests and the recent decisions, with the approve
 * and reject actions. Ported from cli-policy-section.js unchanged.
 *
 * This resolves through the same two routes the needs-you queue does, and it
 * deliberately does not call `needs-store.js` to do it. Two things differ, not
 * just the wiring: `BossModNeeds.resolve()` sends no request body, so a
 * rejection note the operator typed here would be dropped, and the store only
 * ever holds pending needs while this tab also lists resolved ones. The store
 * instance is also built inside the shell's boot and reaches places through
 * `ctx`; Settings is a full-screen takeover outside that, so there is no
 * instance to call without adding a new injection path.
 */
const BossModCliPolicyApprovals = (() => {
    const esc = BossModFormat.escapeHtml;
    const escAttr = BossModFormat.escapeAttribute;
    const { icons, agentName, statusBadge } = BossModCliPolicyShared;

    /**
     * Render the Approvals tab and bind the approve / reject controls.
     *
     * @param {Element} el  The tab content element.
     * @param {object} options
     * @param {() => void} options.onResolved  Called after a decision lands, so
     *   the section can re-read its pending badge. Injected rather than reached
     *   for, because the badge lives in the section's shell.
     * @returns {Promise<void>} A failed load leaves an error line and binds
     *   nothing.
     */
    async function renderApprovalsTab(el, { onResolved }) {
        let approvals = [];
        try {
            const res = await apiFetch('/api/cli-policy/approvals?limit=50');
            // apiFetch resolves on 4xx/5xx. Without this the error body parses
            // as the payload and the tab renders as "no approvals".
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            approvals = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load approvals.</p>';
            return;
        }

        // Partition into pending and resolved
        const pending = approvals.filter(a => a.status === 'pending');
        const resolved = approvals.filter(a => a.status !== 'pending');

        let html = '';

        // Pending section
        html += `<h3 class="text-sm font-semibold mb-3">Pending Requests <span class="text-bm-muted font-normal">(${pending.length})</span></h3>`;

        if (pending.length === 0) {
            html += `
                <div class="text-center py-8 text-bm-muted border border-bm-border rounded-xl bg-white mb-6">
                    <i data-lucide="check-circle-2" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
                    <p class="text-sm">No pending approvals.</p>
                </div>`;
        } else {
            html += '<div class="space-y-3 mb-6">';
            for (const req of pending) {
                html += renderApprovalCard(req, true);
            }
            html += '</div>';
        }

        // Resolved section
        if (resolved.length > 0) {
            html += `<h3 class="text-sm font-semibold mb-3">Recent Decisions <span class="text-bm-muted font-normal">(${resolved.length})</span></h3>`;
            html += '<div class="space-y-2">';
            for (const req of resolved) {
                html += renderApprovalCard(req, false);
            }
            html += '</div>';
        }

        el.innerHTML = html;
        icons(el);

        // Approve buttons
        el.querySelectorAll('[data-approve]').forEach(btn => {
            btn.addEventListener('click', async () => {
                try {
                    await apiFetchOk(`/api/cli-policy/approvals/${btn.dataset.approve}/approve`, { method: 'POST' });
                    renderApprovalsTab(el, { onResolved });
                    onResolved();
                } catch (err) {
                    alert(err.message || 'Failed to approve.');
                }
            });
        });

        // Reject buttons — show inline note input
        el.querySelectorAll('[data-reject-show]').forEach(btn => {
            btn.addEventListener('click', () => {
                const noteRow = document.getElementById(`reject-note-${btn.dataset.rejectShow}`);
                if (noteRow) noteRow.classList.toggle('hidden');
            });
        });

        // Reject confirm
        el.querySelectorAll('[data-reject-confirm]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const reqId = btn.dataset.rejectConfirm;
                const noteInput = document.getElementById(`reject-note-input-${reqId}`);
                const note = noteInput ? noteInput.value.trim() : '';
                try {
                    await apiFetchOk(`/api/cli-policy/approvals/${reqId}/reject`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ decision_note: note || null }),
                    });
                    renderApprovalsTab(el, { onResolved });
                    onResolved();
                } catch (err) {
                    alert(err.message || 'Failed to reject.');
                }
            });
        });
    }

    function renderApprovalCard(req, isPending) {
        const createdAt = new Date(req.created_at);
        const timeStr = createdAt.toLocaleString();
        const agent = agentName(req.agent_id);

        let html = `
            <div class="border border-bm-border rounded-xl p-4 bg-white">
                <div class="flex items-start justify-between gap-3">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                            <span class="text-sm font-medium">${esc(agent)}</span>
                            ${statusBadge(req.status)}
                            <span class="text-[10px] text-bm-muted">${esc(timeStr)}</span>
                        </div>
                        <code class="block text-sm font-mono bg-bm-bg px-2 py-1 rounded mt-1 truncate">${esc(req.command)}</code>
                        ${req.cwd ? `<p class="text-[10px] text-bm-muted mt-1">cwd: ${esc(req.cwd)}</p>` : ''}
                        ${req.decision_note ? `<p class="text-xs text-bm-muted mt-1 italic">Note: ${esc(req.decision_note)}</p>` : ''}
                    </div>`;

        if (isPending) {
            html += `
                    <div class="flex items-center gap-2 shrink-0">
                        <button data-approve="${escAttr(req.id)}"
                                class="px-3 py-1.5 bg-emerald-500 text-white rounded-lg text-xs font-medium hover:opacity-90">
                            Approve
                        </button>
                        <button data-reject-show="${escAttr(req.id)}"
                                class="px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:opacity-90">
                            Reject
                        </button>
                    </div>`;
        }

        html += `
                </div>`;

        if (isPending) {
            html += `
                <div id="reject-note-${escAttr(req.id)}" class="hidden mt-3 pt-3 border-t border-bm-border">
                    <div class="flex items-center gap-2">
                        <input type="text" id="reject-note-input-${escAttr(req.id)}"
                               placeholder="Rejection note (optional)"
                               class="flex-1 px-3 py-1.5 bg-bm-bg border border-bm-border rounded-lg text-xs text-bm-text">
                        <button data-reject-confirm="${escAttr(req.id)}"
                                class="px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:opacity-90">
                            Confirm Reject
                        </button>
                    </div>
                </div>`;
        }

        html += '</div>';
        return html;
    }

    return { renderApprovalsTab };
})();
