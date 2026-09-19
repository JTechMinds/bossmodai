/**
 * BossMod AI — closed-enum communication contract for hire and packs.
 *
 * Four tiny fields only. The Python owner is
 * core/agent_loop/communication_contract.py; this copy exists so the hire
 * form can default and validate without a round-trip. Tests lock the two
 * lists together.
 */
const BossModCommunication = (() => {

    const KEYS = Object.freeze(['tone', 'density', 'jargon', 'audience']);
    const ENUMS = Object.freeze({
        tone: Object.freeze(['precise-but-scannable', 'product-clear', 'direct', 'warm']),
        density: Object.freeze(['scannable', 'compact', 'thorough']),
        jargon: Object.freeze(['none', 'light', 'field']),
        audience: Object.freeze(['operator', 'implementer', 'mixed']),
    });
    const LABELS = Object.freeze({
        tone: 'Tone',
        density: 'Density',
        jargon: 'Jargon',
        audience: 'Audience',
    });

    const AUDITOR = Object.freeze({
        tone: 'precise-but-scannable', density: 'scannable',
        jargon: 'field', audience: 'operator',
    });
    const PLANNER = Object.freeze({
        tone: 'product-clear', density: 'scannable',
        jargon: 'light', audience: 'mixed',
    });
    const FAMILY = Object.freeze({
        review: AUDITOR,
        coordinate: PLANNER,
        write: Object.freeze({
            tone: 'product-clear', density: 'scannable', jargon: 'none', audience: 'operator',
        }),
        implement: Object.freeze({
            tone: 'direct', density: 'compact', jargon: 'field', audience: 'implementer',
        }),
        research: Object.freeze({
            tone: 'precise-but-scannable', density: 'thorough', jargon: 'field', audience: 'operator',
        }),
        design: Object.freeze({
            tone: 'product-clear', density: 'scannable', jargon: 'light', audience: 'operator',
        }),
    });
    const FALLBACK = Object.freeze({
        tone: 'direct', density: 'scannable', jargon: 'light', audience: 'operator',
    });

    const AUDITOR_RE = /\b(auditor|audit|reviewer|review|qa|tester|test)\b/;
    const PLANNER_RE = /\b(planner|planning|plan|pm)\b/;

    /**
     * @param {string|null|undefined} specialty
     * @returns {{tone: string, density: string, jargon: string, audience: string}}
     */
    function defaultFor(specialty) {
        const text = String(specialty || '').toLowerCase();
        if (AUDITOR_RE.test(text)) return AUDITOR;
        if (PLANNER_RE.test(text)) return PLANNER;
        const family = typeof BossModSpecialty !== 'undefined' && BossModSpecialty.specialtyFamily
            ? BossModSpecialty.specialtyFamily(specialty)
            : null;
        return (family && FAMILY[family]) || FALLBACK;
    }

    /**
     * @param {object|null|undefined} value
     * @param {string|null|undefined} specialty
     * @returns {{tone: string, density: string, jargon: string, audience: string}}
     */
    function resolve(value, specialty) {
        const defaults = defaultFor(specialty);
        const incoming = value && typeof value === 'object' ? value : {};
        const resolved = {};
        KEYS.forEach((key) => {
            const token = String(incoming[key] || '').trim().toLowerCase();
            resolved[key] = ENUMS[key].includes(token) ? token : defaults[key];
        });
        return resolved;
    }

    return { KEYS, ENUMS, LABELS, AUDITOR, PLANNER, FALLBACK, defaultFor, resolve };
})();
