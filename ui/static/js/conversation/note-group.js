/**
 * BossMod AI — collapsible runs of action receipts in the transcript.
 *
 * An agent that does a dozen sequential things (Browsing, Writing, ...) would
 * otherwise bury the real messages under a dozen hairline `.note` rows. A run
 * of NOTE_GROUP_THRESHOLD or more consecutive groupable notes is wrapped in a
 * `.note-group`: the first VISIBLE_COUNT stay on screen, the rest sit behind a
 * "Show N more actions" toggle.
 *
 * This module is DOM-only and owns no transcript state. The notes inside a
 * group keep their `data-message-key`, so the transcript's key bookkeeping and
 * key lookups keep working; the group itself carries no key.
 */
const BossModNoteGroup = (() => {
    const { h } = BossModDom;

    /** Minimum consecutive groupable notes that form a group. */
    const NOTE_GROUP_THRESHOLD = 4;

    /** Notes always shown; the rest collapse behind the toggle. */
    const VISIBLE_COUNT = 2;

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

    /** One of the group's three direct parts, by class. */
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
        const visible = h('div', { class: 'note-group-visible' },
            ...entries.slice(0, VISIBLE_COUNT).map((e) => e.node));
        const overflow = h('div', { class: 'note-group-overflow', id: overflowId },
            ...entries.slice(VISIBLE_COUNT).map((e) => e.node));
        overflow.hidden = true;
        const toggle = h('button', {
            class: 'note-group-toggle',
            type: 'button',
            'aria-controls': overflowId,
        });
        const group = h('div', { class: 'note-group', 'data-note-group': 'true' },
            visible, toggle, overflow);
        toggle.addEventListener('click', () => {
            overflow.hidden = !overflow.hidden;
            refresh(group);
        });
        refresh(group);
        return group;
    }

    /**
     * Add a note to the end of an existing group's overflow.
     * @param {HTMLElement} groupEl
     * @param {{node: HTMLElement, key: string}} entry
     * @returns {void}
     */
    function extendGroup(groupEl, entry) {
        part(groupEl, 'note-group-overflow').append(entry.node);
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
        const notes = Array.from(groupEl.querySelectorAll('.note'));
        if (notes.length < NOTE_GROUP_THRESHOLD) {
            groupEl.replaceWith(...notes);
            return true;
        }
        // Keep the visible slice full so a removal there doesn't leave one row.
        const visible = part(groupEl, 'note-group-visible');
        const overflow = part(groupEl, 'note-group-overflow');
        while (visible.children.length < VISIBLE_COUNT && overflow.children.length) {
            visible.append(overflow.children[0]);
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
