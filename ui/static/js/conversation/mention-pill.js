/**
 * BossMod AI — the mention pill and the menu a click opens.
 *
 * The mark is a letter avatar plus a regular-weight name on a soft tint.
 * Colour lives on the avatar; the name inherits body ink so it stays readable.
 * One builder, one menu, no hard-jump to Desk on the click. Missing or fired
 * agents stay text, or open nothing if a pill outlives them.
 */
const BossModMentionPills = (() => {
    const { h } = BossModDom;

    const SKIP_TAGS = new Set(['PRE', 'CODE', 'A', 'BUTTON', 'TEXTAREA', 'INPUT', 'SCRIPT', 'STYLE']);

    let openMenuHandle = null;

    /**
     * One mention pill. Soft tint from the same derivation the avatar uses;
     * the name inherits, so it stays body-readable rather than tint ink.
     *
     * @param {object} agent
     * @param {{onClick?: Function, editable?: boolean}} [options]
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
            style: `background:${tint.bg}`,
        };
        if (opts.editable) attrs.contenteditable = 'false';
        const children = [
            BossModAvatar.create({ name, color: who.color || null, size: 'chip' }),
            h('span', { class: 'mention-pill-name' }, name),
        ];
        if (!interactive) return h('span', attrs, children);
        const pill = h('button', {
            ...attrs,
            type: 'button',
            'aria-label': `${name} mention`,
            onclick: opts.onClick,
        }, children);
        // Host is the positioned ancestor the menu hangs off, so a click
        // cannot park the panel under the whole paragraph. A menu must not
        // live inside the button (nested interactive).
        return h('span', { class: 'mention-host' }, pill);
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
            painted += appendTokens(parent, value, agents, (agent) => renderPill(agent, {
                onClick: (event) => openPillMenu(event, agent, options),
            }), node);
            node.remove();
        }
        return painted;
    }

    function appendTokens(parent, text, agents, makePill, before) {
        const value = String(text || '');
        const hits = BossModMentions.scanMentions(value, agents);
        const insert = (node) => {
            if (before) parent.insertBefore(node, before);
            else parent.append(node);
        };
        if (!hits.length) {
            if (value && !before) insert(document.createTextNode(value));
            return 0;
        }
        let cursor = 0;
        let painted = 0;
        for (const hit of hits) {
            if (hit.start > cursor) {
                insert(document.createTextNode(value.slice(cursor, hit.start)));
            }
            insert(makePill(hit.agent));
            painted += 1;
            cursor = hit.end;
        }
        if (cursor < value.length) insert(document.createTextNode(value.slice(cursor)));
        return painted;
    }

    /**
     * Paint a composer field from `@Name` text. Pills are not buttons — a
     * contenteditable that held a button would steal the caret. Typing after
     * a pick must leave the pill in the tree (do not rebuild on every key).
     *
     * @param {HTMLElement} root
     * @param {string} text
     * @param {object} [ctx]
     * @returns {number} Pills created.
     */
    function paintDraft(root, text, ctx) {
        if (!root) return 0;
        const options = ctx || {};
        const agents = options.agents
            ? BossModMentions.liveAgents(options.agents)
            : BossModMentions.currentAgents();
        root.replaceChildren();
        return appendTokens(root, text, agents, (agent) => renderPill(agent, {
            editable: options.editable !== false,
        }));
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
        const container = mentionHost(opts.anchor) || opts.container;
        if (!opts.anchor || !container) {
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
            container,
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
        const anchor = fromEvent || ctx.anchor;
        const container = mentionHost(anchor)
            || ctx.container
            || (anchor && anchor.closest && anchor.closest('.msg'))
            || document.body;
        return openMenu({ agent: live, agents: pool, anchor, container });
    }

    function mentionHost(node) {
        if (!node) return null;
        if (node.classList && node.classList.contains('mention-host')) return node;
        return node.closest ? node.closest('.mention-host') : null;
    }

    return { renderPill, linkify, paintDraft, openMenu };
})();
