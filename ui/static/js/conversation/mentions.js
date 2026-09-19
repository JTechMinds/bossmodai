/**
 * BossMod AI — live-agent @mention data.
 *
 * Who is live, how `@query` filters, how a pick becomes `@Name`, and the
 * injected Focus / Desk / insert actions. Painting and the composer picker
 * live beside this file so each stays one job.
 *
 * Fail-closed: a name that is not a live hire does not resolve, and a fired
 * agent cannot be opened, desked, or mentioned again.
 */
const BossModMentions = (() => {
    const NAME_TRAIL = /[A-Za-z0-9._-]/;

    const OPEN_CHAT = 'Open Chat';
    const VIEW_DESK = 'View Desk';
    const MENTION_AGAIN = 'Mention again';

    let configured = emptyConfig();
    let mentionAgainInsert = null;

    function emptyConfig() {
        return {
            store: null,
            navigate: null,
            getAgents: null,
            onOpenChat: null,
            onViewDesk: null,
            onMentionAgain: null,
        };
    }

    /**
     * Inject the live roster and the two navigation actions.
     *
     * Tests pass callbacks. The Chat place passes `store` + `navigate` and
     * reuses the rail's Focus / Desk writes so a mention cannot invent a
     * third way to open either one. `null` clears the binding.
     *
     * @param {object|null} deps
     * @returns {void}
     */
    function configure(deps) {
        if (!deps) {
            configured = emptyConfig();
            return;
        }
        configured = {
            store: deps.store || null,
            navigate: typeof deps.navigate === 'function' ? deps.navigate : null,
            getAgents: typeof deps.getAgents === 'function' ? deps.getAgents : null,
            onOpenChat: typeof deps.onOpenChat === 'function' ? deps.onOpenChat : null,
            onViewDesk: typeof deps.onViewDesk === 'function' ? deps.onViewDesk : null,
            onMentionAgain: typeof deps.onMentionAgain === 'function' ? deps.onMentionAgain : null,
        };
    }

    /**
     * Hired agents that still have an id and a name.
     * @param {object[]|null} roster
     * @returns {object[]}
     */
    function liveAgents(roster) {
        return (roster || []).filter((agent) => (
            agent && agent.id && String(agent.name || '').trim()
        ));
    }

    /**
     * The live roster the UI is currently looking at.
     * @returns {object[]}
     */
    function currentAgents() {
        if (configured.getAgents) return liveAgents(configured.getAgents());
        if (configured.store) return liveAgents(configured.store.getState().roster);
        return [];
    }

    /**
     * Filter live agents as the operator types after `@`.
     * @param {object[]} agents
     * @param {string} query
     * @returns {object[]}
     */
    function filterAgents(agents, query) {
        const live = liveAgents(agents);
        const needle = String(query || '').trim().toLowerCase();
        if (!needle) return live.slice();
        return live.filter((agent) => {
            const name = String(agent.name || '').toLowerCase();
            const role = String(agent.role || '').toLowerCase();
            return name.includes(needle) || role.includes(needle);
        });
    }

    /**
     * Resolve a live hire by id or name. Missing or fired is null.
     * @param {object[]} agents
     * @param {string} nameOrId
     * @returns {object|null}
     */
    function resolveLive(agents, nameOrId) {
        const key = String(nameOrId || '').trim().toLowerCase();
        if (!key) return null;
        return liveAgents(agents).find((agent) => (
            agent.id === nameOrId || String(agent.name).trim().toLowerCase() === key
        )) || null;
    }

    function mentionBoundary(text, index) {
        if (index >= text.length) return true;
        // Letters and digits continue a name (`@Jo` must not match Joey).
        // A trailing `.` or `-` is punctuation after a resolved live name.
        return !/[A-Za-z0-9]/.test(text.charAt(index));
    }

    /**
     * The `@query` at `caret`, or null when `@` is not starting a mention.
     * @param {string} text
     * @param {number} caret
     * @returns {{start: number, query: string}|null}
     */
    function findTrigger(text, caret) {
        const value = String(text || '');
        const atCaret = Number.isInteger(caret) ? caret : value.length;
        const before = value.slice(0, atCaret);
        const at = before.lastIndexOf('@');
        if (at < 0) return null;
        if (at > 0 && NAME_TRAIL.test(before.charAt(at - 1))) return null;
        const query = before.slice(at + 1);
        if (/\s/.test(query)) return null;
        return { start: at, query };
    }

    /**
     * Replace `@query` (or insert at the caret) with `@Name `.
     * @param {string} text
     * @param {number} caret
     * @param {string} name
     * @returns {{text: string, caret: number}}
     */
    function insertText(text, caret, name) {
        const who = String(name || '').trim();
        const value = String(text || '');
        const atCaret = Number.isInteger(caret) ? caret : value.length;
        if (!who) return { text: value, caret: atCaret };
        const trigger = findTrigger(value, atCaret);
        if (trigger) {
            const token = `@${who} `;
            return {
                text: value.slice(0, trigger.start) + token + value.slice(atCaret),
                caret: trigger.start + token.length,
            };
        }
        const needsSpace = atCaret > 0 && !/\s/.test(value.charAt(atCaret - 1));
        const token = `${needsSpace ? ' ' : ''}@${who} `;
        return {
            text: value.slice(0, atCaret) + token + value.slice(atCaret),
            caret: atCaret + token.length,
        };
    }

    /**
     * Write `insertText` back onto an input.
     * @param {HTMLElement} input
     * @param {string} name
     * @returns {{text: string, caret: number}}
     */
    function insertAtCaret(input, name) {
        const value = String(input && input.value != null ? input.value : '');
        const caret = input && Number.isInteger(input.selectionStart)
            ? input.selectionStart : value.length;
        const next = insertText(value, caret, name);
        if (input) {
            input.value = next.text;
            if (typeof input.setSelectionRange === 'function') {
                input.setSelectionRange(next.caret, next.caret);
            } else {
                input.selectionStart = next.caret;
                input.selectionEnd = next.caret;
            }
        }
        return next;
    }

    /**
     * Mentions of live agents in `text`, longest name first.
     * @param {string} text
     * @param {object[]} agents
     * @returns {Array<{start: number, end: number, agent: object}>}
     */
    function scanMentions(text, agents) {
        const value = String(text || '');
        const names = liveAgents(agents)
            .slice()
            .sort((a, b) => String(b.name).trim().length - String(a.name).trim().length);
        const found = [];
        let index = 0;
        while (index < value.length) {
            if (value.charAt(index) !== '@') {
                index += 1;
                continue;
            }
            const rest = value.slice(index + 1);
            const lowered = rest.toLowerCase();
            let matched = null;
            for (const agent of names) {
                const name = String(agent.name).trim();
                if (lowered.startsWith(name.toLowerCase()) && mentionBoundary(rest, name.length)) {
                    matched = { start: index, end: index + 1 + name.length, agent };
                    break;
                }
            }
            if (matched) {
                found.push(matched);
                index = matched.end;
            } else {
                index += 1;
            }
        }
        return found;
    }

    function openChat(agent) {
        if (configured.onOpenChat) {
            configured.onOpenChat(agent);
            return;
        }
        const store = configured.store;
        if (!store || !agent) return;
        store.setState({ conversationId: agent.id, conversationKind: 'agent' });
        if (configured.navigate && store.getState().place !== 'chat') configured.navigate('chat');
    }

    function viewDesk(agent) {
        if (configured.onViewDesk) {
            configured.onViewDesk(agent);
            return;
        }
        const store = configured.store;
        if (!store || !agent) return;
        store.setState({ contextMode: 'desk', deskAgentId: agent.id, deskPath: null });
        if (configured.navigate && store.getState().place !== 'chat') configured.navigate('chat');
    }

    function mentionAgain(agent) {
        if (configured.onMentionAgain) {
            configured.onMentionAgain(agent);
            return;
        }
        if (typeof mentionAgainInsert === 'function') mentionAgainInsert(agent);
    }

    /**
     * The composer picker registers the insert used by Mention again.
     * @param {Function|null} fn
     * @returns {void}
     */
    function setInsertHandler(fn) {
        mentionAgainInsert = typeof fn === 'function' ? fn : null;
    }

    return {
        OPEN_CHAT, VIEW_DESK, MENTION_AGAIN,
        configure, setInsertHandler,
        liveAgents, currentAgents, filterAgents, resolveLive,
        findTrigger, insertText, insertAtCaret, scanMentions,
        openChat, viewDesk, mentionAgain,
    };
})();
