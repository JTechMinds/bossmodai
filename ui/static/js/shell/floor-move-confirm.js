/**
 * BossMod AI — the move confirm layer for people and threads.
 *
 * Asks the server what a move would do (`move-plan`) and shows it in groups,
 * so nothing moves that is not on screen:
 *
 *   1. **Moving** — the threads chosen, everyone in them, and people picked
 *      on their own.
 *   2. **Same people, also moving** — other threads whose members are all
 *      moving. Checked by default; unchecking one re-asks the server with it
 *      excluded, so the lists below stay true. It keeps its row, unchecked.
 *   3. **Stays behind, loses people** — threads that keep someone and lose
 *      someone. Informational.
 *   4. **Open tasks left behind** — only when there are any: open work of a
 *      moving agent bound to a thread that stays.
 *
 * The move sends back the plan's fingerprint. If anything changed since the
 * plan was shown, the server answers `plan_changed`; this re-reads the plan
 * and says so rather than moving what the operator did not see.
 */
const BossModFloorMoveConfirm = (() => {
    const { h, clear } = BossModDom;
    const CONFIRM_ID = 'floor-move-confirm';
    const CHANGED = 'Something changed — review again.';

    /**
     * @param {number} count
     * @param {string} one
     * @param {string} many
     * @returns {string}
     */
    function plural(count, one, many) {
        return `${count} ${count === 1 ? one : many}`;
    }

    /**
     * One titled group of lines.
     *
     * @param {string} key  Names the heading id.
     * @param {string} title
     * @param {HTMLElement[]} items  `<li>` nodes.
     * @returns {HTMLElement}
     */
    function group(key, title, items) {
        const headingId = `floor-move-group-${key}`;
        return h('section', { class: 'floor-move-group', 'data-group': key, 'aria-labelledby': headingId },
            h('h3', { class: 'floor-section-title', id: headingId }, title),
            h('ul', { class: 'floor-move-lines' }, items));
    }

    function line(name, detail) {
        return h('li', { class: 'floor-move-line' },
            h('span', { class: 'floor-item-name' }, name),
            detail ? h('span', { class: 'floor-item-meta' }, detail) : null);
    }

    /**
     * Open the confirm layer.
     *
     * @param {object} deps
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @param {{id: string, name: string}} deps.target  Where everything goes.
     * @param {{agentIds: string[], channelIds: string[]}} deps.choice
     * @param {(floorId: string) => string} deps.floorName
     * @param {() => void} deps.onMoved  Called after this layer closes on success.
     * @returns {{close: () => void}}
     */
    function open({ floorApi, target, choice, floorName, onMoved }) {
        const excluded = new Set();
        // Every companion the server has offered, so an unchecked one keeps its row.
        const companions = new Map();
        const content = h('div', { class: 'floor-move-confirm' });
        const notice = h('p', { class: 'context-error', role: 'alert' });
        const body = h('div', { class: 'floor-move' }, notice, content);
        let plan = null;
        let busy = false;

        const layer = BossModOverlays.createModal({
            title: `Move to ${target.name}`,
            size: 'panel',
            body,
            actions: actions(),
            closeOnBackdrop: false,
        });

        function currentChoice() {
            return { ...choice, excludeCompanionIds: Array.from(excluded) };
        }

        function actions() {
            const threads = plan ? plan.threads.length + plan.companions.length : 0;
            const label = !plan
                ? 'Move'
                : `Move ${plural(plan.agents.length, 'person', 'people')}, ${plural(threads, 'thread', 'threads')}`;
            return [
                { label: 'Cancel', tone: 'quiet' },
                {
                    label: busy ? 'Moving…' : label,
                    tone: 'primary',
                    id: CONFIRM_ID,
                    keepOpen: true,
                    onSelect: () => { void apply(); },
                },
            ];
        }

        function syncActions() {
            layer.setActions(actions());
            const button = layer.element.querySelector(`#${CONFIRM_ID}`);
            if (!button) throw new Error('[floor-move-confirm] the Move action did not render');
            button.disabled = busy || plan === null;
        }

        function say(node) {
            clear(content);
            content.append(node);
        }

        async function load() {
            plan = null;
            syncActions();
            say(h('p', { class: 'field-hint' }, 'Working out what moves…'));
            let next;
            try {
                next = await floorApi.planMove(target.id, currentChoice());
            } catch (err) {
                console.error('[floor-move-confirm] could not plan the move', err);
                say(h('div', { class: 'floor-section-error' },
                    h('p', { class: 'context-error', role: 'alert' },
                        err.message || 'The move could not be planned.'),
                    h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { void load(); } }, 'Retry')));
                return;
            }
            plan = next;
            plan.companions.forEach((thread) => companions.set(thread.id, thread));
            render();
            syncActions();
        }

        function companionRow(thread) {
            const box = h('input', {
                type: 'checkbox',
                value: thread.id,
                onchange: () => {
                    if (box.checked) excluded.delete(thread.id);
                    else excluded.add(thread.id);
                    notice.textContent = '';
                    void load();
                },
            });
            box.checked = !excluded.has(thread.id);
            return h('li', { class: 'floor-move-line' },
                h('label', { class: 'floor-move-companion' },
                    box,
                    h('span', { class: 'floor-item-who' },
                        h('span', { class: 'floor-item-name' }, thread.name),
                        h('span', { class: 'floor-item-meta' }, thread.member_names.join(', ')))));
        }

        function render() {
            const moving = [
                ...plan.threads.map((thread) => line(thread.name, `Thread · ${thread.member_names.join(', ')}`)),
                ...plan.agents.map((agent) => line(agent.name,
                    `${agent.reason === 'picked' ? 'Picked' : 'In a moving thread'} · from ${floorName(agent.from_floor_id)}`)),
            ];
            const groups = [group('moving', 'Moving', moving)];
            if (companions.size) {
                groups.push(group('companions', 'Same people, also moving',
                    Array.from(companions.values()).map(companionRow)));
            }
            if (plan.split_threads.length) {
                groups.push(group('split', 'Stays behind, loses people', plan.split_threads.map((thread) => {
                    const leave = `${thread.leaving_names.join(', ')} ${thread.leaving_names.length === 1 ? 'leaves' : 'leave'}`;
                    const stay = thread.staying_names.length
                        ? `${thread.staying_names.join(', ')} ${thread.staying_names.length === 1 ? 'stays' : 'stay'}`
                        : 'no one stays';
                    return line(thread.name, `${leave}; ${stay}`);
                })));
            }
            if (plan.stranded_tasks.length) {
                groups.push(group('stranded', 'Open tasks left behind', plan.stranded_tasks.map(
                    (task) => line(task.title, `Stays with ${task.thread_name}`),
                )));
            }
            say(h('div', { class: 'floor-move-groups' }, groups));
        }

        async function apply() {
            if (busy || !plan) return;
            busy = true;
            notice.textContent = '';
            syncActions();
            try {
                await floorApi.applyMove(target.id, currentChoice(), plan.fingerprint);
            } catch (err) {
                busy = false;
                if (err.code === 'plan_changed') {
                    console.warn('[floor-move-confirm] the plan changed before the move', err);
                    await load();
                    notice.textContent = CHANGED;
                    return;
                }
                console.error('[floor-move-confirm] could not move', err);
                notice.textContent = err.message || 'The move failed.';
                syncActions();
                return;
            }
            layer.close();
            onMoved();
        }

        void load();
        return { close: () => layer.close() };
    }

    return { open };
})();
