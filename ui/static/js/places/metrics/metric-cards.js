/**
 * BossMod AI — the Metrics numbers: the health panel, the four stat cards, the
 * health grid, and the communication line.
 *
 * Pure rendering over the metrics dashboard payload. It formats
 * nothing itself: `formatNumber`, `formatDuration` and `formatTokenCount` are
 * BossModFormat's, because company-metrics.js kept private copies of the last
 * two and a second opinion about what "< 1m" means is a second number on screen.
 *
 * Colour comes from data attributes resolved in places.css, never from a
 * hardcoded utility class — the dock-era cards named Tailwind greens and reds
 * directly, which put five colours outside tokens.css.
 */
const BossModMetricCards = (() => {
    const { h } = BossModDom;
    const U = BossModFormat;

    /** Thresholds ported verbatim from company-metrics.js. */
    const WARN_PERCENT = 5;
    const CRITICAL_PERCENT = 15;

    /**
     * How healthy an error rate is.
     *
     * @param {number} rate  0..1, as the endpoint reports it.
     * @returns {{label: string, tone: 'ok'|'warn'|'bad', percent: string}}
     */
    function errorRateHealth(rate) {
        const percent = (rate || 0) * 100;
        const text = percent.toFixed(1);
        if (percent < WARN_PERCENT) return { label: 'Healthy', tone: 'ok', percent: text };
        if (percent <= CRITICAL_PERCENT) return { label: 'Warning', tone: 'warn', percent: text };
        return { label: 'Critical', tone: 'bad', percent: text };
    }

    /**
     * The numbers behind the verdict (spec 6.5).
     *
     * This was `healthLine()`, which led with the verdict word because it was
     * rendered alone in the header. The verdict is the panel's bold line now,
     * so saying it twice would be the panel disagreeing with itself about how
     * many times a thing needs saying. Every NUMBER it reported is still here.
     *
     * @param {object} data  The dashboard payload.
     * @returns {string}
     */
    function healthDetail(data) {
        const health = errorRateHealth((data.errors || {}).rate);
        const uptime = (data.uptime || {}).seconds;
        const agents = (data.agents || {}).total || 0;
        return `${health.percent}% errors · `
            + `${U.formatDuration(uptime == null ? null : uptime)} uptime · `
            + `${U.formatNumber(agents)} agent${agents === 1 ? '' : 's'}`;
    }

    /**
     * The verdict, as the first thing on the page.
     *
     * A new container, not new data. The tone is carried three ways — the
     * light, the verdict word, and the numbers beneath it — so that none of
     * them is the only carrier (SC 1.4.1) and the light can stay decoration.
     *
     * @param {object} data  The dashboard payload.
     * @returns {HTMLElement}
     */
    function renderHealthPanel(data) {
        const health = errorRateHealth((data.errors || {}).rate);
        return h('section', { class: 'metric-health' },
            h('span', {
                class: 'metric-health-light',
                'data-health': health.tone,
                'aria-hidden': 'true',
            }),
            h('div', { class: 'metric-health-copy' },
                h('p', { class: 'metric-health-verdict' }, health.label),
                h('p', { class: 'metric-health-detail' }, healthDetail(data))));
    }

    function card(label, value, extra) {
        return h('article', { class: 'metric-card' },
            h('p', { class: 'metric-card-label' }, label),
            h('p', { class: 'metric-card-value' }, value),
            extra);
    }

    /**
     * The four headline numbers.
     *
     * @param {object} data
     * @returns {HTMLElement}
     */
    function renderStatCards(data) {
        const tasks = data.tasks || {};
        const agents = data.agents || {};
        const tokens = data.tokens || {};
        const health = errorRateHealth((data.errors || {}).rate);

        return h('div', { class: 'metric-cards' },
            card('Tasks Completed', U.formatNumber(tasks.completed || 0),
                h('p', { class: 'metric-card-sub' },
                    `+${U.formatNumber(tasks.completed_today || 0)} today`)),
            card('Active Agents', U.formatNumber(agents.active || 0),
                h('p', { class: 'metric-card-sub' }, `${U.formatNumber(agents.idle || 0)} idle`)),
            card('Tokens Today', U.formatNumber(tokens.today || 0),
                h('p', { class: 'metric-card-sub' },
                    `${U.formatTokenCount(tokens.total || 0)} all-time`)),
            card('Error Rate', `${health.percent}%`,
                h('p', { class: 'metric-card-sub metric-card-health', 'data-health': health.tone },
                    h('span', { class: 'status-dot', 'aria-hidden': 'true' }),
                    health.label)));
    }

    function healthCell(value, label, tone) {
        return h('article', { class: 'metric-cell', 'data-tone': tone },
            h('p', { class: 'metric-cell-value' }, value),
            h('p', { class: 'metric-cell-label' }, label));
    }

    /**
     * Errors, invalid decisions, tool calls, uptime.
     *
     * @param {object} data
     * @returns {HTMLElement}
     */
    function renderHealthGrid(data) {
        const errors = data.errors || {};
        const tools = data.tool_calls || {};
        const uptime = data.uptime || {};
        return h('div', { class: 'metric-grid' },
            healthCell(U.formatNumber(errors.today || 0), 'Errors Today', 'bad'),
            healthCell(U.formatNumber(errors.invalid_decisions || 0), 'Invalid Decisions', 'warn'),
            healthCell(U.formatNumber(tools.total || 0), 'Tool Calls', 'info'),
            // The endpoint reports null when the runtime has never started;
            // formatDuration renders that as '--' rather than as zero uptime.
            healthCell(U.formatDuration(uptime.seconds == null ? null : uptime.seconds),
                'Uptime', 'ok'));
    }

    /**
     * The communication stats row.
     *
     * @param {object} comms
     * @returns {HTMLElement}
     */
    function renderCommunication(comms) {
        const items = [
            [comms.messages_sent || 0, 'messages sent'],
            [comms.agent_conversations || 0, 'agent conversations'],
            [comms.active_channels || 0, 'active channels'],
            [comms.meetings_held || 0, 'meetings held'],
        ];
        const row = h('div', { class: 'metric-comms' });
        items.forEach(([value, label], index) => {
            if (index > 0) row.append(h('span', { class: 'metric-comms-sep' }, '·'));
            row.append(h('span', { class: 'metric-comms-item' },
                h('strong', {}, U.formatNumber(value)), ` ${label}`));
        });
        return h('section', { class: 'metric-panel' },
            h('h2', { class: 'metric-panel-title' }, 'Communication'), row);
    }

    return {
        errorRateHealth, renderHealthPanel, renderStatCards, renderHealthGrid,
        renderCommunication,
    };
})();
