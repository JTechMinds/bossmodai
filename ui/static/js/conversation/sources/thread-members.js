/**
 * BossMod AI — the "Agents in thread" dialog.
 *
 * One place to see who is in a thread, take someone out, and add someone from
 * the thread's own floor. The thread header's "N participants" and its
 * "Add people…" both open it, so the two doors cannot drift into two screens.
 *
 * It paints from the members the source hands back on every `render()` and
 * owns no copy of them: the source holds the channel, this is only its view.
 * It does not touch the network either — remove and add are injected, so the
 * requests (thread-requests.js, thread-seat.js) stay where they are tested.
 * The row and door shapes are the floor settings' (overlays.css .floor-item,
 * .menu-door), the same members-list-with-an-Add-door.
 */
const BossModThreadMembers = (() => {
    const { h, clear } = BossModDom;

    /** Why Remove is off on the last member; shown, not only a disabled button. */
    const LAST_MEMBER_NOTE = 'A thread needs at least one agent. Add someone before removing the last one.';

    /**
     * Open the dialog.
     *
     * @param {object} deps
     * @param {() => object[]} deps.members  The thread's current members
     *   (`{id, name, role?, color?}`), read on every render.
     * @param {(agentId: string) => Promise<void>} deps.remove  Takes one member
     *   out; rejects with the server's reason.
     * @param {() => Promise<object[]>} deps.openTasks  The thread's open tasks
     *   (`{assigned_to, ...}`). Read before every removal; rejects when the
     *   read fails, and then nothing is removed.
     * @param {() => Promise<void>} deps.add  Picks a teammate from the thread's
     *   floor and seats them. Resolves quietly when the picker is cancelled;
     *   rejects when nobody is left to add or the seat is refused.
     * @param {() => void} [deps.onClose]  Called once the dialog has closed,
     *   however it closed, so the source can forget it.
     * @returns {{render: () => void, close: () => void, element: HTMLElement}}
     * @throws {Error} When `members`, `remove`, `openTasks` or `add` is not a function.
     */
    function open(deps) {
        const members = deps && deps.members;
        const remove = deps && deps.remove;
        const add = deps && deps.add;
        const openTasks = deps && deps.openTasks;
        const onClose = deps && deps.onClose;
        if (typeof members !== 'function') throw new Error('[thread-members] deps.members is required');
        if (typeof remove !== 'function') throw new Error('[thread-members] deps.remove is required');
        if (typeof add !== 'function') throw new Error('[thread-members] deps.add is required');
        if (typeof openTasks !== 'function') throw new Error('[thread-members] deps.openTasks is required');

        let busy = false;
        let failure = '';
        // The member whose removal waits on the operator, with the tasks that
        // stay assigned to them: `{id, count}`. At most one at a time.
        let confirming = null;

        const list = h('ul', { class: 'floor-item-list' });
        const note = h('p', { class: 'field-hint' });
        const error = h('p', { class: 'context-error', role: 'alert' });
        const door = h('button', { class: 'menu-door', type: 'button', onclick: () => { void run(add); } },
            h('i', { 'data-lucide': 'plus', 'aria-hidden': 'true' }),
            h('span', {}, 'Add an agent'));
        const body = h('div', { class: 'thread-members' }, list, note, error, door);

        /**
         * Run one change behind the in-flight gate. A refusal is shown in the
         * dialog (the header's toast sits under the scrim) and logged; the
         * list is repainted either way, from whatever the source now holds.
         *
         * @param {() => Promise<void>} change
         * @returns {Promise<void>}
         */
        async function run(change) {
            if (busy) return;
            busy = true;
            failure = '';
            render();
            try {
                await change();
            } catch (err) {
                console.error('[thread-members] the change failed', err);
                failure = (err && err.message) || 'That did not work. Try again.';
            } finally {
                busy = false;
                render();
            }
        }

        /**
         * Start a removal. A member with no open tasks from this thread goes at
         * once; one with some is parked on an inline confirmation, because
         * their tasks keep their name and the operator should know that first.
         *
         * @param {object} member
         * @returns {Promise<void>}
         */
        async function requestRemove(member) {
            const tasks = await openTasks();
            const assigned = tasks.filter((task) => task && task.assigned_to === member.id);
            if (!assigned.length) {
                await remove(member.id);
                return;
            }
            confirming = { id: member.id, count: assigned.length };
        }

        /** The confirmed removal. The prompt stays up if the server refuses. */
        async function confirmRemove() {
            await remove(confirming.id);
            confirming = null;
        }

        function confirmRow(member, count) {
            const name = member.name || 'Agent';
            const tasks = count === 1 ? '1 open task' : `${count} open tasks`;
            return h('li', { class: 'floor-item thread-member-confirm' },
                BossModAvatar.create({ name, color: member.color || null, size: 'sm' }),
                h('div', { class: 'floor-item-who' },
                    h('span', { class: 'floor-item-name' }, name),
                    h('span', { class: 'floor-item-meta' },
                        `${name} still has ${tasks} from this thread. They stay assigned to ${name}.`)),
                h('div', { class: 'thread-member-confirm-actions' },
                    h('button', {
                        class: 'btn btn-sm btn-danger',
                        type: 'button',
                        disabled: busy,
                        onclick: () => { void run(confirmRemove); },
                    }, 'Remove anyway'),
                    h('button', {
                        class: 'btn btn-sm',
                        type: 'button',
                        disabled: busy,
                        onclick: () => { confirming = null; render(); },
                    }, 'Keep')));
        }

        function row(member, onlyOne) {
            if (confirming && confirming.id === member.id) return confirmRow(member, confirming.count);
            const name = member.name || 'Agent';
            const label = `Remove ${name}`;
            const button = h('button', {
                class: 'menu-trigger thread-member-remove',
                type: 'button',
                'data-size': 'inline',
                'aria-label': label,
                'data-tooltip': label,
                disabled: busy || onlyOne || !!confirming,
                onclick: () => { void run(() => requestRemove(member)); },
            }, h('i', { 'data-lucide': 'x', 'aria-hidden': 'true' }));
            return h('li', { class: 'floor-item' },
                BossModAvatar.create({ name, color: member.color || null, size: 'sm' }),
                h('div', { class: 'floor-item-who' },
                    h('span', { class: 'floor-item-name' }, name),
                    member.role ? h('span', { class: 'floor-item-meta' }, member.role) : null),
                button);
        }

        /** Repaint from the source's current members. */
        function render() {
            const current = members();
            // Someone else took them out meanwhile: nothing left to confirm.
            if (confirming && !current.some((member) => member.id === confirming.id)) confirming = null;
            clear(list);
            current.forEach((member) => list.append(row(member, current.length <= 1)));
            note.textContent = current.length <= 1 ? LAST_MEMBER_NOTE : '';
            note.hidden = current.length > 1;
            error.textContent = failure;
            error.hidden = !failure;
            door.disabled = busy || !!confirming;
            BossModIcons.paint(body, 'thread-members');
        }

        const modal = BossModOverlays.createModal({
            title: 'Agents in thread',
            size: 'panel',
            body,
            // No footer: Close would repeat the head's ✕.
            actions: [],
            // Focus would otherwise land on the first Remove and draw its ring.
            focusBody: false,
            closeOnBackdrop: true,
            onClose: () => { if (typeof onClose === 'function') onClose(); },
        });
        render();

        return { render, close: () => modal.close(), element: modal.element };
    }

    /**
     * The source's handle on the dialog: one at a time, repainted when the
     * channel changes and closed when the room is sealed or the source ends.
     * Remove and add adopt the channel the server answers with; adding offers
     * only agents on the thread's own floor (thread-seat.js).
     *
     * @param {object} deps
     * @param {string} deps.threadId
     * @param {object} deps.seat  From BossModThreadSeat.createThreadSeat.
     * @param {object} deps.requests  From BossModThreadRequests.createThreadRequests.
     * @param {object} deps.archive  From BossModThreadArchive.createThreadArchive;
     *   its `openTasks` is read before a removal.
     * @param {(pending: Promise<object>) => Promise<void>} deps.adopt  Takes
     *   the channel a request answered with and repaints the header from it.
     * @param {() => object[]} deps.members  The source's current members.
     * @param {() => object|null} deps.channel  The source's channel, null until loaded.
     * @returns {{open: () => Promise<void>, render: () => void, close: () => void}}
     */
    function createController({ threadId, seat, requests, archive, adopt, members, channel }) {
        let dialog = null;
        return {
            /**
             * Open the dialog, or do nothing when it is already open.
             * @throws {Error} When the thread has not loaded: its floor is
             *   unknown, and guessing one would offer the wrong people.
             */
            async open() {
                if (dialog) return;
                if (!channel()) throw new Error('This thread is still loading. Try again in a moment.');
                dialog = open({
                    members,
                    remove: (agentId) => adopt(requests.removeMember(agentId)),
                    openTasks: async () => (await archive.openTasks(threadId)).tasks,
                    add: async () => {
                        const updated = await seat.pickAndSeat(threadId, members(), channel().floor_id);
                        if (updated) await adopt(Promise.resolve(updated));
                    },
                    onClose: () => { dialog = null; },
                });
            },
            render() { if (dialog) dialog.render(); },
            close() { if (dialog) dialog.close(); },
        };
    }

    return { open, createController };
})();
