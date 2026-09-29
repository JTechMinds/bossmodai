/**
 * BossMod AI — one paged, read-only data table.
 *
 * The app's only table seam. It is built on Tabulator (vendored 6.5.3, see
 * js/vendor/VENDOR_SOURCES.md), and nothing outside this file names
 * `Tabulator`, so swapping or upgrading the library is a one-file change.
 * css/data-table.css re-skins Tabulator's classes with tokens.css only.
 *
 * Paging is the server's: Tabulator's remote pagination asks for a page
 * number and a size, and this wrapper turns that into the caller's
 * `loadPage({skip, top})`, whose `has_more` becomes "one more page exists".
 *
 * Accessibility, closed here rather than left to the library:
 *   - the grid's accessible name is `caption` (aria-label on the grid);
 *   - the first cell of every row is a real <button>, so rows are reached
 *     with Tab and activated with Enter/Space; its click bubbles to the row,
 *     so pointer and keyboard share the one `rowClick` path;
 *   - an emphasised row (e.g. unread) carries a visible dot and "Unread: "
 *     leads its activator's accessible name, not weight alone. `rowLabel`
 *     must contain the first cell's visible text (SC 2.5.3, label in name);
 *   - every cell is set as TEXT (a formatter returning a text node), never
 *     HTML: the values are other people's mail subjects.
 *
 * Loading and error are this wrapper's states over the table (Tabulator's
 * own loader overlay is off); empty is Tabulator's placeholder.
 */
const BossModDataTable = (() => {
    const { h } = BossModDom;

    const COPY = Object.freeze({
        loading: 'Loading…',
        failed: 'Couldn’t load this list.',
        retry: 'Try again',
        unread: 'Unread: ',
    });

    /**
     * The real library, behind the one seam. Tests inject a stand-in.
     *
     * @param {HTMLElement} element
     * @param {object} options  Tabulator options.
     * @returns {object} The Tabulator instance.
     */
    function tabulatorFactory(element, options) {
        return new Tabulator(element, options);
    }

    /**
     * Build a table.
     *
     * @param {object} options
     * @param {string} options.caption  The table's accessible name.
     * @param {Array<{key: string, label: string}>} options.columns  At least
     *   one; the first column's cell is the row's activator.
     * @param {(row: object) => string} options.rowLabel  The activator's
     *   accessible name for a row, e.g. "Open message: Hello".
     * @param {(row: object) => void} options.onActivate  Called with the
     *   row (`{id, cells, emphasis}`) on click or Enter/Space.
     * @param {({skip: number, top: number}) => Promise<{rows: object[],
     *   has_more: boolean}>} options.loadPage  The caller's fetcher; it
     *   rejects with an Error whose message is shown.
     * @param {number} options.pageSize  Rows per page (a positive integer).
     * @param {string} options.emptyText  Shown when the first page is empty.
     * @param {(el: HTMLElement, opts: object) => object} [options.tableFactory]
     *   Test seam; defaults to the vendored Tabulator.
     * @returns {{element: HTMLElement, reload: () => void, destroy: () => void}}
     *   Mount `element` in the document BEFORE the first layout matters:
     *   Tabulator measures its host.
     * @throws {Error} On a missing or malformed option.
     */
    function create(options) {
        const {
            caption, columns, rowLabel, onActivate, loadPage, pageSize, emptyText,
            tableFactory = tabulatorFactory,
        } = options || {};
        if (!caption || typeof caption !== 'string') throw new Error('[data-table] caption is required');
        if (!Array.isArray(columns) || !columns.length) throw new Error('[data-table] columns must be a non-empty array');
        columns.forEach((column) => {
            if (!column || !column.key || !column.label) throw new Error('[data-table] every column needs a key and a label');
        });
        if (typeof rowLabel !== 'function') throw new Error('[data-table] rowLabel must be a function');
        if (typeof onActivate !== 'function') throw new Error('[data-table] onActivate must be a function');
        if (typeof loadPage !== 'function') throw new Error('[data-table] loadPage must be a function');
        if (!Number.isInteger(pageSize) || pageSize < 1) throw new Error('[data-table] pageSize must be a positive integer');
        if (!emptyText || typeof emptyText !== 'string') throw new Error('[data-table] emptyText is required');

        const statusEl = h('p', { class: 'data-table-status', role: 'status' });
        const errorText = h('p', { class: 'data-table-error-text' });
        const errorEl = h('div', { class: 'data-table-error', role: 'alert' },
            errorText,
            h('button', { class: 'btn btn-sm', type: 'button', onclick: () => reload() }, COPY.retry));
        errorEl.hidden = true;
        const gridEl = h('div', { class: 'data-table-grid', 'aria-label': caption });
        const element = h('div', { class: 'data-table' }, statusEl, errorEl, gridEl);

        let destroyed = false;

        function showLoading() {
            statusEl.textContent = COPY.loading;
            errorEl.hidden = true;
        }

        function showLoaded() {
            statusEl.textContent = '';
        }

        function showError(message) {
            statusEl.textContent = '';
            errorText.textContent = message || COPY.failed;
            errorEl.hidden = false;
        }

        function textCell(key) {
            return (cell) => document.createTextNode(String((cell.getRow().getData().cells || {})[key] ?? ''));
        }

        function activatorCell(key) {
            return (cell) => {
                const row = cell.getRow().getData();
                return h('button', {
                    class: 'data-table-row-btn',
                    type: 'button',
                    'aria-label': (row.emphasis ? COPY.unread : '') + rowLabel(row),
                },
                row.emphasis ? h('span', { class: 'data-table-dot', 'aria-hidden': 'true' }) : null,
                String((row.cells || {})[key] ?? ''));
            };
        }

        const tableColumns = columns.map((column, index) => ({
            title: column.label,
            field: `cells.${column.key}`,
            headerSort: false,
            resizable: false,
            titleFormatter: () => document.createTextNode(column.label),
            formatter: index === 0 ? activatorCell(column.key) : textCell(column.key),
        }));

        const table = tableFactory(gridEl, {
            layout: 'fitColumns',
            // Every row of the page stays in the DOM: a page is small, assistive
            // tech then sees all its rows, and a hidden host (a layer opened
            // over the list) does not empty the virtual renderer, which would
            // drop the row button focus has to return to.
            renderVertical: 'basic',
            columns: tableColumns,
            placeholder: emptyText,
            dataLoader: false,
            pagination: true,
            paginationMode: 'remote',
            paginationSize: pageSize,
            // Tabulator only asks its request function when a URL is set; this
            // one names the request, the function below makes it.
            ajaxURL: 'bossmod:data-table',
            ajaxRequestFunc: (_url, _config, params) => request(params),
            rowFormatter: (row) => {
                row.getElement().classList.toggle('is-emphasis', Boolean(row.getData().emphasis));
            },
        });

        async function request(params) {
            const page = Number(params && params.page) || 1;
            const size = Number(params && params.size) || pageSize;
            showLoading();
            try {
                const result = await loadPage({ skip: (page - 1) * size, top: size });
                if (!destroyed) showLoaded();
                return { data: result.rows, last_page: result.has_more ? page + 1 : page };
            } catch (err) {
                if (!destroyed) showError(String((err && err.message) || err));
                throw err;
            }
        }

        table.on('rowClick', (_event, row) => onActivate(row.getData()));

        /** Re-read from the first page. @returns {void} */
        function reload() {
            if (destroyed) return;
            // A failure reaches request() above, which shows it; Tabulator
            // itself resolves setData() either way.
            table.setData();
        }

        return {
            element,
            reload,
            /** Tear the table down; later loads paint nothing. @returns {void} */
            destroy() {
                destroyed = true;
                table.destroy();
            },
        };
    }

    return { create };
})();
