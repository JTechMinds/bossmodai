/**
 * BossMod AI — specialty families and the honesty rules around "done".
 *
 * The third of the three modules utils.js became. Two related jobs, one
 * vocabulary: inferring what family of work some text describes and what
 * family an agent's role is (only to seed hire-form defaults such as the
 * suggested finish line — never to judge an assignment), and stating what a
 * checkable done claim looks like.
 *
 * The done-claim copy is behaviourally load-bearing — `test_role_contracts.py`
 * asserts it reaches the Tasks place's task detail and the desk's task cards, and
 * `core/tasking/board.py` produces the server half of the same rule. This is
 * the client's single copy of it.
 */
const BossModSpecialty = (() => {

    const SPECIALTY_PAIRS = [
        { work: ['write', 'draft', 'document', 'docs', 'copy', 'author', 'edit'], roles: ['writer', 'writing', 'editor', 'docs', 'author'], family: 'write' },
        { work: ['review', 'audit', 'test', 'qa', 'inspect'], roles: ['reviewer', 'auditor', 'qa', 'tester', 'review'], family: 'review' },
        { work: ['implement', 'code', 'build', 'fix', 'debug'], roles: ['engineer', 'developer', 'coder', 'eng'], family: 'implement' },
        { work: ['research', 'analyze', 'analysis'], roles: ['researcher', 'analyst'], family: 'research' },
        { work: ['design', 'mockup', 'ux'], roles: ['designer', 'design', 'ux'], family: 'design' },
    ];
    const FINISH_LINE_DEFAULTS = {
        write: 'A named draft or document exists. Empty done does not count.',
        review: 'A checkable allow/deny (or tests/artifact) exists. Empty done does not count.',
        implement: 'Tests evidence or a named artifact exists. Empty done does not count.',
        research: 'A named findings note exists. Empty done does not count.',
        design: 'A named mockup or design file exists. Empty done does not count.',
        coordinate: 'A named plan or status note exists. Empty done does not count.',
    };
    const FALLBACK_FINISH_LINE = 'A checkable claim exists (tests, artifact, or allow/deny). Empty done does not count.';

    /**
     * The family of work a task's wording describes.
     *
     * @param {string|null} title
     * @param {string|null} description
     * @returns {string|null} null when the text names none or more than one —
     *   an ambiguous guess is worse than no guess, because everything
     *   downstream treats a family as a fact.
     */
    function inferWorkFamily(title, description) {
        const text = `${title || ''} ${description || ''}`.toLowerCase();
        if (!text.trim()) return null;
        const hits = [];
        for (const pair of SPECIALTY_PAIRS) {
            if (pair.work.some(word => text.includes(word))) hits.push(pair.family);
        }
        const unique = [...new Set(hits)];
        return unique.length === 1 ? unique[0] : null;
    }

    /**
     * The family an agent's role belongs to.
     *
     * @param {string|null} role
     * @returns {string|null} 'coordinate' for lead-shaped roles; null when
     *   ambiguous.
     */
    function specialtyFamily(role) {
        const text = (role || '').toLowerCase();
        if (!text) return null;
        if (['lead', 'pm', 'manager', 'coordinator', 'owner', 'director'].some(word => text.includes(word))) {
            return 'coordinate';
        }
        const hits = [];
        for (const pair of SPECIALTY_PAIRS) {
            if (pair.roles.some(word => text.includes(word))) hits.push(pair.family);
        }
        const unique = [...new Set(hits)];
        return unique.length === 1 ? unique[0] : null;
    }

    /**
     * A starting done/fail bar for a role, for the hire form to offer.
     *
     * @param {string|null} specialty
     * @param {string|null} description
     * @returns {string} Always a usable sentence; there is no empty suggestion.
     */
    function suggestFinishLine(specialty, description) {
        const family = specialtyFamily(specialty) || inferWorkFamily(null, description);
        return FINISH_LINE_DEFAULTS[family] || FALLBACK_FINISH_LINE;
    }

    /**
     * What "complete" requires for one task.
     *
     * The server's own guidance wins when it sent any; otherwise this
     * reconstructs it from the work contract and the assignee's done/fail bar,
     * so the operator sees the same rule the engine will enforce.
     *
     * @param {object} task
     * @returns {string}
     */
    function doneClaimGuidance(task) {
        if (task?.done_claim_guidance) return task.done_claim_guidance;
        const bar = (task?.assigned_to_done_fail_bar || '').trim();
        const hasFiles = Boolean(task?.work_contract?.deliverables?.length);
        let base;
        if (hasFiles) {
            base = 'Complete requires the work-contract file path to exist (that file is the checkable claim).';
        } else {
            base = 'Complete/deliver requires a checkable claim: tests evidence, an artifact path that exists, or an allow/deny proof summary. Empty done is rejected.';
        }
        return bar ? `${base} What done looks like for this agent: ${bar}` : base;
    }

    /**
     * One done claim on a single line.
     *
     * @param {object|null} claim
     * @returns {string} '' when there is no claim; the type alone when it
     *   carries neither a path nor evidence.
     */
    function formatDoneClaim(claim) {
        if (!claim || typeof claim !== 'object') return '';
        const type = String(claim.type || 'proof').trim() || 'proof';
        const path = String(claim.path || '').trim();
        const evidence = String(claim.evidence || '').trim();
        if (path && evidence) return `${type} — ${path} — ${evidence}`;
        if (path) return `${type} — ${path}`;
        if (evidence) return `${type} — ${evidence}`;
        return type;
    }

    return {
        inferWorkFamily,
        specialtyFamily,
        suggestFinishLine,
        doneClaimGuidance,
        formatDoneClaim,
    };
})();
