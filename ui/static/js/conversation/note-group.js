/**
 * BossMod AI — collapsible runs of action receipts in the transcript.
 *
 * An agent that does a dozen sequential things (Browsing, Writing, ...) would
 * otherwise bury the real messages under a dozen hairline `.note` rows. A run
 * of NOTE_GROUP_THRESHOLD or more consecutive groupable notes is wrapped in a
 * `.note-group`: the first and the last stay on screen (what started the run,
 * and where it is now); the middle sits behind a "Show N more actions" toggle.
 *
 * This module is DOM-only and owns no transcript state. The notes inside a
 * group keep their `data-message-key`, so the transcript's key bookkeeping and
 * key lookups keep working; the group itself carries no key.
 */
const BossModNoteGroup = (() => {
    const { h } = BossModDom;

    /** Minimum consecutive groupable notes that form a group. */
    const NOTE_GROUP_THRESHOLD = 4;

    let nextGroupId = 0;

    /**
     * Is this node an action receipt eligible for grouping? The green Done
     * receipt and the compaction divider are milestones, not receipts, so
     * they always stay visible on their own.
     * @param {Node|null} node
     * @returns {boolean}
     */
    function isGroupable(node) {
        return Boolean(node && node.nodeType === 1 && node.classList.contains('note')
            && !node.classList.contains('note-ok')
            && !node.classList.contains('conversation-notes'));
    }

    /** One of the group's four direct parts, by class. */
    function part(groupEl, className) {
        const found = Array.from(groupEl.children).find((c) => c.classList.contains(className));
        if (!found) throw new Error(`[note-group] group is missing .${className}`);
        return found;
    }

    /** Sync the toggle's label, visibility and aria state to the overflow. */
    function refresh(groupEl) {
        const overflow = part(groupEl, 'note-group-overflow');
        const toggle = part(groupEl, 'note-group-toggle');
        const count = overflow.children.length;
        toggle.hidden = count === 0;
        const expanded = !overflow.hidden;
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        toggle.textContent = expanded
            ? 'Show less'
            : `Show ${count} more ${count === 1 ? 'action' : 'actions'}`;
    }

    /**
     * Build a collapsible group from already-rendered notes. The nodes are
     * moved into the group (and out of wherever they were).
     * @param {Array<{node: HTMLElement, key: string}>} entries  In display order.
     * @returns {HTMLElement} The `.note-group` container.
     * @throws {Error} When given fewer than NOTE_GROUP_THRESHOLD entries.
     */
    function buildNoteGroup(entries) {
        if (!Array.isArray(entries) || entries.length < NOTE_GROUP_THRESHOLD) {
            throw new Error(`[note-group] a group needs at least ${NOTE_GROUP_THRESHOLD} notes`);
        }
        nextGroupId += 1;
        const overflowId = `note-group-overflow-${nextGroupId}`;
        const first = h('div', { class: 'note-group-first' }, entries[0].node);
        const last = h('div', { class: 'note-group-last' }, entries[entries.length - 1].node);
        const overflow = h('div', { class: 'note-group-overflow', id: overflowId },
            ...entries.slice(1, -1).map((e) => e.node));
        overflow.hidden = true;
        const toggle = h('button', {
            class: 'note-group-toggle',
            type: 'button',
            'aria-controls': overflowId,
        });
        const group = h('div', { class: 'note-group', 'data-note-group': 'true' },
            first, toggle, overflow, last);
        toggle.addEventListener('click', () => {
            overflow.hidden = !overflow.hidden;
            refresh(group);
        });
        refresh(group);
        return group;
    }

    /**
     * Add a note as the group's new last; the previous last joins the end of
     * the overflow so the run's order is preserved.
     * @param {HTMLElement} groupEl
     * @param {{node: HTMLElement, key: string}} entry
     * @returns {void}
     */
    function extendGroup(groupEl, entry) {
        const last = part(groupEl, 'note-group-last');
        part(groupEl, 'note-group-overflow').append(...Array.from(last.children));
        last.append(entry.node);
        refresh(groupEl);
    }

    /**
     * Replace consecutive notes already in a list with one group, in place.
     * @param {HTMLElement} listEl
     * @param {HTMLElement[]} noteNodes  Adjacent children of listEl.
     * @returns {HTMLElement} The new group.
     */
    function promoteToGroup(listEl, noteNodes) {
        // A marker holds the run's position: building the group moves the
        // nodes out of the list, which would otherwise lose where they were.
        const marker = h('span');
        listEl.insertBefore(marker, noteNodes[0]);
        const group = buildNoteGroup(noteNodes.map((node) => ({
            node,
            key: node.getAttribute('data-message-key') || '',
        })));
        marker.replaceWith(group);
        return group;
    }

    /**
     * Remove a note from a group by key, dissolving the group when it falls
     * below the threshold.
     * @param {HTMLElement} groupEl
     * @param {string} key
     * @returns {boolean} true when the group dissolved into plain notes.
     */
    function removeFromGroup(groupEl, key) {
        const target = Array.from(groupEl.querySelectorAll('[data-message-key]'))
            .find((n) => n.getAttribute('data-message-key') === key);
        if (target) target.remove();
        const first = part(groupEl, 'note-group-first');
        const overflow = part(groupEl, 'note-group-overflow');
        const last = part(groupEl, 'note-group-last');
        const notes = [...first.children, ...overflow.children, ...last.children];
        if (notes.length < NOTE_GROUP_THRESHOLD) {
            groupEl.replaceWith(...notes);
            return true;
        }
        // Each end slot must keep exactly one note, refilled from the nearest
        // overflow neighbour so the run's order holds.
        if (first.children.length === 0) first.append(overflow.children[0]);
        if (last.children.length === 0) last.append(overflow.children[overflow.children.length - 1]);
        if (first.children.length !== 1 || last.children.length !== 1) {
            throw new Error('[note-group] an end slot could not be refilled');
        }
        refresh(groupEl);
        return false;
    }

    return {
        NOTE_GROUP_THRESHOLD,
        isGroupable,
        buildNoteGroup,
        extendGroup,
        promoteToGroup,
        removeFromGroup,
    };
})();
