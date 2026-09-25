/**
 * BossMod AI — Settings form chrome shared across sections.
 *
 * Primary Save / Create actions use the same accent button as AI Connections
 * and Personalities forms (`settings-connections-form.js`, etc.).
 */
const BossModSettingsChrome = (() => {
    const PRIMARY_ACTION = (
        'px-4 py-2 bg-bm-accent text-white rounded-lg '
        + 'hover:bg-bm-accent-hover transition-colors text-sm font-medium'
    );

    return { PRIMARY_ACTION };
})();
