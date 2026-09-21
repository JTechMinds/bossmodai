/**
 * BossMod AI — what clicking an agent on the Office floor offers.
 *
 * A small dialog of doors into that one agent — their chat, their desk — as
 * tiles, because a tile reads as "go there" at a glance and the row of them
 * grows by an entry, not by a layout. The dialog does not know where a door
 * leads: the Office injects each one, and both are the roster's own routes
 * (shell/agent-routes.js), so a door means the same thing wherever it is.
 *
 * It is a modal from core/overlays.js — the one frame, trap, Esc and focus
 * restoration — and it is not a form, so an outside click closes it.
 */
const BossModOfficeAgentActions = (() => {
    const { h } = BossModDom;

    /**
     * Open the dialog for one agent.
     *
     * @param {object} deps
     * @param {{id: string, name: string, color?: string|null, status?: string,
     *   currentActivityKind?: string|null}} deps.agent  A roster entry.
     * @param {() => void} deps.onOpenChat  Runs after the dialog has closed.
     * @param {() => void} deps.onViewDesk  Runs after the dialog has closed.
     * @param {() => void} [deps.onClose]  Called once, however it closed.
     * @returns {{close: () => void}}
     * @throws {Error} When the agent, its id or name, or either door is
     *   missing — a dialog that opens on nobody, or offers a door that does
     *   nothing, is worse than no dialog.
     */
    function open(deps) {
        const { agent, onOpenChat, onViewDesk, onClose } = deps || {};
        if (!agent || !agent.id || !agent.name) {
            throw new Error('[office-agent-actions] deps.agent with an id and a name is required');
        }
        if (typeof onOpenChat !== 'function') throw new Error('[office-agent-actions] deps.onOpenChat is required');
        if (typeof onViewDesk !== 'function') throw new Error('[office-agent-actions] deps.onViewDesk is required');

        // The doors, in order. A new one is a new entry here and nothing else:
        // the grid lays out however many there are.
        const doors = [
            { id: 'office-agent-open-chat', label: 'Open chat', icon: 'message-square', run: onOpenChat },
            { id: 'office-agent-view-desk', label: 'View desk', icon: 'folder-open', run: onViewDesk },
        ];

        let modal = null;
        const tiles = h('div', { class: 'office-agent-tiles' },
            doors.map((door) => h('button', {
                class: 'btn office-agent-tile',
                type: 'button',
                id: door.id,
                // Close FIRST: every door navigates, and the dialog must not
                // outlive the place that raised it.
                onclick: () => { modal.close(); door.run(); },
            },
                h('i', { 'data-lucide': door.icon, 'aria-hidden': 'true' }),
                h('span', {}, door.label))));

        modal = BossModOverlays.createModal({
            title: agent.name,
            // The face, where the conversation header puts it: before the name.
            lead: BossModAvatar.create({ name: agent.name, color: agent.color, size: 'sm' }),
            subtitle: BossModAgentStatus.getStatusLabel(agent.status, agent.currentActivityKind),
            body: tiles,
            actions: [],
            closeOnBackdrop: true,
            onClose: () => { if (onClose) onClose(); },
        });
        // Scoped to this panel, never the document: painting wider would
        // rebuild every icon in the shell.
        BossModIcons.paint(modal.element, 'office-agent-actions');
        return { close: modal.close };
    }

    return { open };
})();
