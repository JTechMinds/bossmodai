/**
 * BossMod AI — the mention pill and the menu a click opens.
 *
 * Chat already paints an agent as a tinted chip-plus-name. Mentions reuse
 * that mark: one builder, one menu, no hard-jump to Desk on the click.
 * Missing or fired agents stay text, or open nothing if a pill outlives them.
 */
const BossModMentionPills = (() => {
    const { h } = BossModDom;

    const SKIP_TAGS = new Set(['PRE', 'CODE', 'A', 'BUTTON', 'TEXTAREA', 'INPUT', 'SCRIPT', 'STYLE']);

    let openMenuHandle = null;

    /**
     * One mention pill. Chip avatar plus the name, on the derived tint.
     *
     * @param {object} agent
     * @param {{onClick?: Function}} [options]
     * @returns {HTMLElement}
     */
    function renderPill(agent, options) {
        const opts = options || {};
        const who = agent || {};
        const name = String(who.name || '').trim() || 'Agent';
        const tint = BossModAvatar.tintFor(who.color || null);
        const interactive = typeof opts.onClick === 'function';
        const attrs = {
            class: 'mention-pill',
            'data-agent-id': who.id || null,
            'data-agent-name': name,
            style: `background:${tint.bg};color:${tint.ink}`,
        };
        const children = [
            BossModAvatar.create({ name, color: who.color || null, size: 'chip' }),
            h('span', { class: 'mention-pill-name' }, name),
        ];
        if (!interactive) return h('span', attrs, children);
        return h('button', {
            ...attrs,
            type: 'button',
            'aria-label': `${name} mention`,
            onclick: opts.onClick,
        }, children);
    }

    function skipTag(node) {
        return node && node.nodeType === 1 && SKIP_TAGS.has(node.tagName);
    }

    function collectText(root, into) {
        if (!root) return;
        if (root.nodeType === 3) {
            into.push(root);
            return;
        }
        if (root.nodeType !== 1 || skipTag(root)) return;
        const kids = Array.from(root.childNodes || []);
        for (const child of kids) collectText(child, into);
    }

    /**
     * Replace live `@Name` text with pills. Unknown or fired names stay text.
     * @param {HTMLElement} root
     * @param {object} [ctx]
     * @returns {number} Pills created.
     */
    function linkify(root, ctx) {
        const options = ctx || {};
        const agents = options.agents
            ? BossModMentions.liveAgents(options.agents)
            : BossModMentions.currentAgents();
        const nodes = [];
        collectText(root, nodes);
        let painted = 0;
        for (const node of nodes) {
            const value = String(node.textContent || '');
            const hits = BossModMentions.scanMentions(value, agents);
            if (!hits.length) continue;
            const parent = node.parentNode;
            if (!parent) continue;
            let cursor = 0;
            for (const hit of hits) {
                if (hit.start > cursor) {
                    parent.insertBefore(document.createTextNode(value.slice(cursor, hit.start)), node);
                }
                parent.insertBefore(renderPill(hit.agent, {
                    onClick: (event) => openPillMenu(event, hit.agent, options),
                }), node);
                painted += 1;
                cursor = hit.end;
            }
            if (cursor < value.length) {
                parent.insertBefore(document.createTextNode(value.slice(cursor)), node);
            }
            node.remove();
        }
        return painted;
    }

    function menuAction(id, label, onSelect) {
        return h('button', {
            class: 'menu-action',
            type: 'button',
            id,
            onclick: onSelect,
        }, label);
    }

    /**
     * The three-action menu. A missing or fired agent is a no-op.
     * @param {object} options
     * @returns {{close: Function, element: HTMLElement}|null}
     */
    function openMenu(options) {
        const opts = options || {};
        const pool = opts.agents || BossModMentions.currentAgents();
        const agent = BossModMentions.resolveLive(pool, (opts.agent && opts.agent.id) || '');
        if (!agent) return null;
        if (!opts.anchor || !opts.container) {
            throw new Error('[mention-pill] a pill menu needs an anchor and a container');
        }
        if (openMenuHandle) {
            openMenuHandle.close();
            openMenuHandle = null;
        }
        const items = [h('div', { class: 'menu-actions' },
            menuAction('mention-open-chat', BossModMentions.OPEN_CHAT, () => BossModMentions.openChat(agent)),
            menuAction('mention-view-desk', BossModMentions.VIEW_DESK, () => BossModMentions.viewDesk(agent)),
            menuAction('mention-again', BossModMentions.MENTION_AGAIN, () => BossModMentions.mentionAgain(agent)))];
        openMenuHandle = BossModOverlays.createMenu({
            anchor: opts.anchor,
            label: `${agent.name} mention`,
            items,
            container: opts.container,
            onClose: () => { openMenuHandle = null; },
        });
        openMenuHandle.element.setAttribute('data-menu', 'mention');
        return openMenuHandle;
    }

    function openPillMenu(event, agent, ctx) {
        if (event && event.preventDefault) event.preventDefault();
        if (event && event.stopPropagation) event.stopPropagation();
        const pool = ctx.agents || BossModMentions.currentAgents();
        const live = BossModMentions.resolveLive(pool, agent && agent.id);
        if (!live) return null;
        const fromEvent = event && event.currentTarget;
        const container = ctx.container
            || (ctx.anchor && ctx.anchor.closest && ctx.anchor.closest('.msg'))
            || (fromEvent && fromEvent.closest && fromEvent.closest('.msg'))
            || document.body;
        const anchor = fromEvent || ctx.anchor;
        return openMenu({ agent: live, agents: pool, anchor, container });
    }

    return { renderPill, linkify, openMenu };
})();
