/**
 * BossMod AI — the Add agent browse door.
 *
 * Create is one dialog with two doors. Browse packs reads the pinned
 * catalog, then hydrates through POST /api/agent-packs/import — the
 * same #54 import, not a second hire pipeline. Start blank is today's
 * empty form. Name, Color, and AI connections stay operator-owned.
 *
 * Built with BossModDom.h: catalog titles and author names are remote
 * data, so they cannot go through a markup-string exemption.
 */
const BossModAgentFormCatalog = (() => {
    const { h, clear } = BossModDom;
    const HYDRATE = BossModAgentFormHydrate;

    const COPY = Object.freeze({
        browse: 'Browse packs',
        blank: 'Start blank',
        loading: 'Loading packs…',
        empty: 'No packs yet. Start blank, or check the catalog repo.',
        fail: 'Couldn’t load packs. Start blank, or try again.',
        tryAgain: 'Try again',
    });

    /**
     * @param {string} slug
     * @returns {string}
     */
    function categoryLabel(slug) {
        return String(slug || '')
            .split('-')
            .filter(Boolean)
            .map((part) => part.replace(/^[a-z]/, (letter) => letter.toUpperCase()))
            .join(' ');
    }

    /**
     * Wrap the form host with doors + browse. The form stays a sibling so
     * `buildFormHTML` replacing the form host cannot wipe the catalog.
     *
     * @param {HTMLElement} formHost
     * @returns {{body: HTMLElement, bindForm: (formRoot: HTMLElement) => void}}
     */
    function createAddBody(formHost) {
        const catalogEl = h('div', { class: 'agent-add-catalog', id: 'agent-add-catalog' });
        const body = h('div', { class: 'agent-add-body' }, catalogEl, formHost);
        const api = mount(catalogEl);
        return { body, bindForm: api.bindForm };
    }

    /**
     * Render the two doors and load the catalog into `host`.
     *
     * @param {HTMLElement} host
     * @returns {{bindForm: (formRoot: HTMLElement) => void}}
     */
    function mount(host) {
        let formRoot = null;
        const browsePanel = h('div', { class: 'pack-browse', id: 'pack-browse' });
        const banner = h('p', { class: 'pack-from-banner hidden', id: 'pack-from-banner', role: 'status' });
        const toolsHint = h('p', { class: 'pack-tools-hint hidden', id: 'pack-tools-hint' });
        const browseBtn = h('button', {
            class: 'agent-add-door',
            type: 'button',
            id: 'agent-add-browse',
            'aria-pressed': 'true',
            onclick: () => setDoor('browse'),
        }, COPY.browse);
        const blankBtn = h('button', {
            class: 'agent-add-door',
            type: 'button',
            id: 'agent-add-blank',
            'aria-pressed': 'false',
            onclick: () => setDoor('blank'),
        }, COPY.blank);

        host.append(
            h('div', { class: 'agent-add-doors', role: 'tablist', 'aria-label': 'Add agent' }, browseBtn, blankBtn),
            browsePanel,
            banner,
            toolsHint,
        );

        /**
         * @param {'browse'|'blank'} next
         * @returns {void}
         */
        function setDoor(next) {
            browseBtn.setAttribute('aria-pressed', next === 'browse' ? 'true' : 'false');
            blankBtn.setAttribute('aria-pressed', next === 'blank' ? 'true' : 'false');
            browsePanel.classList.toggle('hidden', next === 'blank');
            if (next === 'blank') clearPackFields();
        }

        function clearPackFields() {
            banner.classList.add('hidden');
            banner.textContent = '';
            toolsHint.classList.add('hidden');
            toolsHint.textContent = '';
            if (!formRoot) return;
            HYDRATE.applyHireFields(formRoot, { role: '', description: '', done_fail_bar: '' });
        }

        async function loadCatalog() {
            clear(browsePanel);
            browsePanel.append(h('p', { class: 'pack-catalog-status', role: 'status' }, COPY.loading));
            let data;
            try {
                data = await BossModAgentApi.fetchCatalog();
            } catch {
                showFail();
                return;
            }
            const categories = Array.isArray(data?.categories) ? data.categories : [];
            const packs = categories.flatMap((group) => group.packs || []);
            if (!packs.length) {
                clear(browsePanel);
                browsePanel.append(h('p', { class: 'pack-catalog-status', role: 'status' }, COPY.empty));
                return;
            }
            renderCategories(categories, data);
        }

        function showFail() {
            clear(browsePanel);
            browsePanel.append(
                h('p', { class: 'pack-catalog-status', role: 'alert' }, COPY.fail),
                h('button', {
                    class: 'agent-add-retry',
                    type: 'button',
                    id: 'pack-catalog-retry',
                    onclick: () => { void loadCatalog(); },
                }, COPY.tryAgain),
            );
        }

        /**
         * @param {object[]} categories
         * @param {object} catalog
         * @returns {void}
         */
        function renderCategories(categories, catalog) {
            clear(browsePanel);
            const pin = catalog.commit_sha || catalog.pin_short || '';
            categories.forEach((group) => {
                const cards = h('div', { class: 'pack-cards' });
                (group.packs || []).forEach((pack) => cards.append(packCard(pack, pin)));
                browsePanel.append(
                    h('section', { class: 'pack-category' },
                        h('h3', { class: 'pack-category-title' }, categoryLabel(group.id || '')),
                        cards),
                );
            });
        }

        /**
         * @param {object} pack
         * @param {string} pin
         * @returns {HTMLElement}
         */
        function packCard(pack, pin) {
            const author = pack.pack_author || null;
            const authorNode = author && author.url
                ? h('a', {
                    class: 'pack-author-link',
                    href: author.url,
                    target: '_blank',
                    rel: 'noopener noreferrer',
                    onclick: (event) => event.stopPropagation(),
                }, author.name)
                : (author && author.name
                    ? h('span', { class: 'pack-author-plain' }, author.name)
                    : null);
            return h('div', { class: 'pack-card' },
                h('button', {
                    class: 'pack-card-pick',
                    type: 'button',
                    'data-pack-id': pack.id,
                    onclick: () => { void pickPack(pack, pin); },
                }, pack.title || pack.id),
                authorNode);
        }

        /**
         * @param {object} pack
         * @param {string} pin
         * @returns {Promise<void>}
         */
        async function pickPack(pack, pin) {
            if (!formRoot) return;
            try {
                const imported = await BossModAgentApi.importPack({ id: pack.id, ref: pin });
                HYDRATE.applyImport(formRoot, imported, {
                    title: (imported.catalog && imported.catalog.title) || pack.title,
                    banner,
                    toolsHint,
                });
            } catch (err) {
                banner.classList.remove('hidden');
                banner.textContent = err?.message || COPY.fail;
            }
        }

        function bindForm(root) {
            formRoot = root;
            HYDRATE.bindUrlImport(root, { banner, toolsHint });
        }

        void loadCatalog();
        return { bindForm };
    }

    return { COPY, createAddBody, mount };
})();
