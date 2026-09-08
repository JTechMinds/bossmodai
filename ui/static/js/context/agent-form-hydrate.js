/**
 * BossMod AI — apply a pack import onto the Add agent form.
 *
 * Catalog browse and Advanced URL import both land here. The fields a
 * pack may fill are Specialty, Description, What-done, personality
 * hint, and tools hints. Name, Color, and AI connections are never
 * written. Import never sends agent_id, so a live hire cannot be
 * overwritten from this path.
 */
const BossModAgentFormHydrate = (() => {

    /**
     * @param {string} sha
     * @returns {string}
     */
    function shortSha(sha) {
        return String(sha || '').replace(/[^0-9a-f]/gi, '').slice(0, 7);
    }

    /**
     * @param {string} title
     * @param {string} sha
     * @returns {string}
     */
    function fromPackLine(title, sha) {
        return `From pack: ${title} · pinned ${shortSha(sha)}`;
    }

    /**
     * Fill specialty / description / what-done. Leaves name, color, and
     * connection selects untouched.
     *
     * @param {HTMLElement} formRoot
     * @param {object} fields
     * @returns {void}
     */
    function applyHireFields(formRoot, fields) {
        const role = formRoot.querySelector('input[name="role"]');
        const description = formRoot.querySelector('textarea[name="description"]');
        const done = formRoot.querySelector('[name="done_fail_bar"]');
        if (role) role.value = fields.role || '';
        if (description) description.value = fields.description || '';
        if (done) done.value = fields.done_fail_bar || '';
        const hint = fields.personality_hint;
        const personality = formRoot.querySelector('select[name="personality_id"]');
        if (hint && personality) {
            const options = personality.options
                ? Array.from(personality.options)
                : Array.from(personality.children || []).filter((node) => node.tagName === 'OPTION');
            const match = options.find((option) => (
                String(option.textContent || '').trim() === String(hint).trim()
            ));
            if (match) {
                personality.value = match.value || match.getAttribute('value') || '';
            }
        }
    }

    /**
     * @param {HTMLElement} formRoot
     * @param {object} imported
     * @param {{title: string, banner: HTMLElement, toolsHint: HTMLElement}} view
     * @returns {void}
     */
    function applyImport(formRoot, imported, view) {
        const fields = imported.hire_fields || {};
        applyHireFields(formRoot, fields);
        const sha = (imported.pin && imported.pin.commit_sha) || '';
        view.banner.classList.remove('hidden');
        view.banner.textContent = fromPackLine(view.title, sha);
        const tools = Array.isArray(fields.tools_hint) ? fields.tools_hint : [];
        if (tools.length) {
            view.toolsHint.classList.remove('hidden');
            view.toolsHint.textContent = `Tools hint: ${tools.join(', ')}`;
        } else {
            view.toolsHint.classList.add('hidden');
            view.toolsHint.textContent = '';
        }
        const advanced = formRoot.querySelector('#advanced-content');
        const chevron = formRoot.querySelector('#advanced-chevron');
        if (advanced && advanced.classList.contains('hidden')) {
            advanced.classList.remove('hidden');
            if (chevron) chevron.style.transform = 'rotate(90deg)';
        }
    }

    /**
     * Advanced URL import. Confirm only when the repo is not allowlisted.
     *
     * @param {HTMLElement} formRoot
     * @param {{banner: HTMLElement, toolsHint: HTMLElement}} view
     * @returns {void}
     */
    function bindUrlImport(formRoot, view) {
        const button = formRoot.querySelector('#btn-import-pack-url');
        const input = formRoot.querySelector('#pack-url-input');
        const status = formRoot.querySelector('#pack-url-import-status');
        if (!button || !input) return;

        async function run(confirm) {
            const url = String(input.value || '').trim();
            if (!url) return;
            if (status) {
                status.classList.remove('hidden');
                status.textContent = 'Importing…';
            }
            try {
                const imported = await BossModAgentApi.importPack(
                    confirm ? { url, confirm: true } : { url },
                );
                const title = (imported.catalog && imported.catalog.title)
                    || (imported.hire_fields && imported.hire_fields.role)
                    || 'pack';
                applyImport(formRoot, imported, { title, ...view });
                if (status) status.classList.add('hidden');
            } catch (err) {
                if (err && err.code === 'trust_required') {
                    if (status) status.classList.add('hidden');
                    BossModOverlays.createModal({
                        title: 'Trust this pack URL?',
                        body: 'This repo is not on the allowlist. Import only if the source is trusted.',
                        actions: [
                            { label: 'Import anyway', tone: 'danger', onSelect: () => { void run(true); } },
                            { label: 'Cancel', tone: 'quiet' },
                        ],
                    });
                    return;
                }
                if (status) {
                    status.classList.remove('hidden');
                    status.textContent = err?.message || 'Pack import failed.';
                }
            }
        }

        button.addEventListener('click', () => { void run(false); });
    }

    return {
        shortSha,
        fromPackLine,
        applyHireFields,
        applyImport,
        bindUrlImport,
    };
})();
