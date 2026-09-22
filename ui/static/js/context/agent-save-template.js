/**
 * BossMod AI — Save as template: one form's role contract, into the library.
 *
 * The other half of the picker. A template was only ever something INSTALLED
 * from the marketplace, so an operator who tuned a specialty, a description
 * and a done bar by hand had nowhere to keep that work: the next agent started
 * from a blank form or from someone else's pack. This writes what is on the
 * form into the same library the picker reads, as a LOCAL template
 * (`source: 'local'`) — no pack, no URL, no pin, nothing fetched.
 *
 * THE ROLE CONTRACT, AND NOTHING ELSE. Specialty, description, what-done, the
 * personality by name, and the four communication enums: the fields a template
 * is allowed to fill (context/agent-form-template.js's `templateFields` is the
 * same list, read the other way). Never the name, the colour or a connection —
 * a template that could write those would overwrite the next operator's draft
 * and carry this machine's model choices onto a form that has its own.
 *
 * A LAYER over the form, not a second dialog: core/overlays.js stacks it in
 * the same frame, the form stays underneath with its draft, and the footer
 * action that opens it says `keepOpen` for exactly that reason. Two questions
 * live in the layer — the title and the category — because both are about the
 * LIBRARY rather than about the agent, and neither has a field on the form.
 *
 * Built with BossModDom.h: a title is operator text and a category is a slug.
 */
const BossModAgentSaveTemplate = (() => {
    const { h, clear } = BossModDom;
    const API = BossModAgentTemplatesApi;
    const ITEMS = BossModMarketplaceItems;
    // The inline confirm strip both marketplace views ask with. A save that
    // would overwrite is the same kind of question as an uninstall.
    const DETAIL = BossModMarketplaceDetail;
    const FIELDS = BossModAgentFields;

    /**
     * The shelf a template with no other home goes on. A slug, because the
     * picker and the marketplace group by one and title-case it for the eye.
     */
    const CUSTOM = 'custom';

    const COPY = Object.freeze({
        title: 'Save as template',
        titleField: 'Title',
        categoryField: 'Category',
        cancel: 'Cancel',
        save: 'Save',
        saving: 'Saving…',
        done: 'Done',
        replace: 'Replace',
        titleNeeded: 'Give the template a title.',
        libraryFailed: 'Couldn’t read your template library, so there is no '
            + 'category to file this under. Try again in a moment.',
        needs: (names) => `A template is a role contract: fill in ${names} `
            + 'and use Save as template again.',
        saved: (title, category) => `Saved “${title}” to your templates — `
            + `Add agent, ${category}, tagged Local.`,
        failed: 'Save failed.',
    });

    /** The refusal that sends the operator back to a field, by id. */
    const REFUSED_ID = 'agent-save-template-refused';
    /** The layer's own controls, named so a harness and a repaint can find them. */
    const SAVE_ID = 'agent-save-template-save';
    const DONE_ID = 'agent-save-template-done';
    const CANCEL_ACTION = Object.freeze({ label: COPY.cancel, tone: 'quiet' });

    /**
     * One open at a time. `open` reads the library before it can offer a
     * category, so a second click while that read is in flight would raise a
     * second layer over the first.
     */
    const opening = BossModGates.createInFlightGate();

    /**
     * The personality a form has chosen, by the name the library stores.
     *
     * @param {HTMLFormElement} form
     * @returns {string|null} null for "No personality" and for the kept
     *   option, which is one agent's own prompt text and names no personality
     *   a template could ask another machine for.
     */
    function personalityHint(form) {
        const select = form.querySelector('select[name="personality_id"]');
        const chosen = select ? String(select.value || '') : '';
        if (!chosen || chosen === FIELDS.KEPT_PERSONALITY) return null;
        const options = select.options
            ? Array.from(select.options)
            : Array.from(select.children || []).filter((node) => node.tagName === 'OPTION');
        const option = options.find(
            (node) => (node.value || node.getAttribute('value') || '') === chosen,
        );
        return (option && String(option.textContent || '').trim()) || null;
    }

    /**
     * The role contract a form is carrying right now.
     *
     * PURE, and read at the moment it is asked for: what gets saved is what is
     * on screen, not what the form was built from.
     *
     * @param {HTMLFormElement} form
     * @returns {{specialty: string, description: string,
     *   what_done_looks_like: string, personality_hint: string|null,
     *   communication: object}} Trimmed strings, and the four closed enums
     *   resolved the way context/agent-submit.js resolves them for the agent
     *   itself, so a template and an agent made from one form agree.
     */
    function fieldsFromForm(form) {
        const text = (selector) => {
            const node = form.querySelector(selector);
            return node ? String(node.value || '').trim() : '';
        };
        const specialty = text('input[name="role"]');
        return {
            specialty,
            description: text('textarea[name="description"]'),
            what_done_looks_like: text('[name="done_fail_bar"]'),
            personality_hint: personalityHint(form),
            communication: BossModCommunication.resolve({
                tone: text('[name="communication_tone"]'),
                density: text('[name="communication_density"]'),
                jargon: text('[name="communication_jargon"]'),
                audience: text('[name="communication_audience"]'),
            }, specialty),
        };
    }

    /**
     * What the layer opens with in its Title box: the specialty, else the
     * agent's name. Both are on the form; a template is filed by what it DOES,
     * so the specialty leads.
     *
     * @param {HTMLFormElement} form
     * @returns {string} '' when the form carries neither, which the layer then
     *   refuses to save until the operator types one.
     */
    function defaultTitle(form) {
        const text = (selector) => {
            const node = form.querySelector(selector);
            return node ? String(node.value || '').trim() : '';
        };
        return text('input[name="role"]') || text('input[name="name"]');
    }

    /**
     * Say — on the form, not in a layer that never opened — why nothing was
     * saved, and put the keyboard where the answer goes.
     *
     * @param {HTMLFormElement} form
     * @param {string} message
     * @param {string} [focusSelector]  The field to answer in.
     * @returns {void}
     */
    function refuse(form, message, focusSelector) {
        const old = form.querySelector(`#${REFUSED_ID}`);
        if (old) old.remove();
        form.prepend(h('p', { class: 'field-warn form-refusal', id: REFUSED_ID, role: 'alert' },
            message));
        const field = focusSelector ? form.querySelector(focusSelector) : null;
        if (field && field.focus) field.focus();
    }

    /**
     * Every category the library already uses, plus the two this save needs.
     *
     * @param {object[]} templates  Rows from the library.
     * @param {string} current  The category the layer opens on.
     * @returns {Array<{value: string, label: string}>} Title-cased by the
     *   projection both grids label their rows with, so a slug reads one way
     *   everywhere, and sorted so the list does not move between opens.
     */
    function categoryOptions(templates, current) {
        const slugs = new Set([CUSTOM, current]);
        templates.forEach((row) => { if (row.category) slugs.add(row.category); });
        return Array.from(slugs).sort()
            .map((slug) => ({ value: slug, label: ITEMS.categoryLabel(slug) || slug }));
    }

    /**
     * Open the Save as template layer over an agent form.
     *
     * Refuses to open at all — with a named message on the form — when the
     * contract is not there to save (a template with no specialty or no
     * description fills nothing), or when the library could not be read and
     * there is therefore no category list to file this under.
     *
     * @param {object} deps
     * @param {HTMLFormElement} deps.form  The form to read, right now.
     * @param {string} deps.defaultTitle  What the Title box opens with.
     * @param {string} deps.defaultCategory  The category it opens on.
     * @param {(template: object) => void} [deps.onSaved]  A save landed, for a
     *   caller that shows the library and must re-read it. The Edit role
     *   dialog shows none and passes nothing.
     * @returns {Promise<void>} Never rejects on a refused or failed SAVE: both
     *   are said in the layer. A second call while the first is still reading
     *   the library does nothing.
     * @throws {Error} Without a form, a title default or a category default.
     */
    async function open(deps) {
        const { form, defaultTitle: startTitle, defaultCategory, onSaved } = deps || {};
        if (!form) throw new Error('[save-template] deps.form is required');
        if (typeof startTitle !== 'string') {
            throw new Error('[save-template] deps.defaultTitle is required');
        }
        if (!defaultCategory) throw new Error('[save-template] deps.defaultCategory is required');
        await opening.run(async () => {
            const fields = fieldsFromForm(form);
            const missing = [];
            if (!fields.specialty) missing.push('a specialty');
            if (!fields.description) missing.push('a description');
            if (missing.length) {
                refuse(form, COPY.needs(missing.join(' and ')),
                    fields.specialty ? 'textarea[name="description"]' : 'input[name="role"]');
                return;
            }
            let library;
            try {
                library = await API.listTemplates();
            } catch (err) {
                console.error('[save-template] the library could not be read', err);
                refuse(form, COPY.libraryFailed);
                return;
            }
            const stale = form.querySelector(`#${REFUSED_ID}`);
            if (stale) stale.remove();
            raise(fields, Array.isArray(library) ? library : [], {
                startTitle, defaultCategory, onSaved,
            });
        });
    }

    /**
     * Build and open the layer itself.
     *
     * @param {object} fields  From `fieldsFromForm`, read before the library.
     * @param {object[]} library  Rows, for the categories they are filed under.
     * @param {object} view  `startTitle`, `defaultCategory`, `onSaved`.
     * @returns {void}
     */
    function raise(fields, library, view) {
        const titleInput = h('input', {
            class: 'field-input', id: 'agent-save-template-title', type: 'text',
            maxlength: '120', placeholder: COPY.titleField,
        });
        titleInput.value = view.startTitle;
        const category = BossModMenuSelect.create({
            label: COPY.categoryField,
            options: categoryOptions(library, view.defaultCategory),
            value: view.defaultCategory,
            // The control owns the choice; it is read back when Save runs.
            onChange: () => {},
        });
        const note = h('p', { class: 'save-template-note', role: 'alert' });
        note.hidden = true;
        const asking = h('div', { class: 'save-template-ask' });
        const body = h('div', { class: 'save-template' },
            h('div', { class: 'field' },
                h('label', { class: 'field-label', for: titleInput.id }, COPY.titleField),
                titleInput),
            h('div', { class: 'field' },
                h('span', { class: 'field-label' }, COPY.categoryField),
                category.element),
            note,
            asking);

        const modal = BossModOverlays.createModal({
            title: COPY.title,
            body,
            actions: [CANCEL_ACTION, {
                label: COPY.save, tone: 'primary', id: SAVE_ID, keepOpen: true,
                onSelect: () => { void submit(false); },
            }],
            // The layer's panel goes with it; the menu's press-outside listener
            // is the control's own and has to be taken down by hand.
            onClose: () => category.destroy(),
        });
        BossModIcons.paint(modal.element, 'agent-save-template');

        /** Say why nothing was saved, in the layer that asked. */
        function say(text) {
            note.textContent = text;
            note.hidden = false;
        }

        /** The Save button, while it is still in the row. */
        const saveButton = () => modal.element.querySelector(`#${SAVE_ID}`);

        /**
         * POST the contract, and answer what comes back.
         *
         * @param {boolean} replace  True only after the operator answered the
         *   taken-title question: it overwrites the template of that name.
         * @returns {Promise<void>}
         */
        async function submit(replace) {
            const title = titleInput.value.trim();
            if (!title) {
                say(COPY.titleNeeded);
                titleInput.focus();
                return;
            }
            clear(asking);
            const button = saveButton();
            if (button) {
                button.disabled = true;
                button.textContent = COPY.saving;
            }
            try {
                const template = await API.saveLocalTemplate({
                    title,
                    category: category.getValue(),
                    ...fields,
                    replace,
                });
                settle(template, title);
            } catch (err) {
                if (err && err.code === 'local_title_taken') {
                    ask(err.message);
                    return;
                }
                console.error('[save-template] the save failed', err);
                say((err && err.message) || COPY.failed);
            } finally {
                const live = saveButton();
                if (live) {
                    live.disabled = false;
                    live.textContent = COPY.save;
                }
            }
        }

        /**
         * The one question a save can raise: this title is taken.
         *
         * Inline, in the layer, rather than a third stacked dialog — the same
         * strip the marketplace asks its uninstall with.
         *
         * @param {string} message  The server's own sentence, which already
         *   names the title: one wording, and it is the one that refused.
         * @returns {void}
         */
        function ask(message) {
            note.hidden = true;
            clear(asking);
            asking.append(DETAIL.confirmStrip({
                text: message,
                id: 'agent-save-template-replace',
                label: COPY.replace,
                tone: 'danger',
                onConfirm: () => { void submit(true); },
                onCancel: () => { clear(asking); titleInput.focus(); },
            }));
            const confirm = asking.querySelector('#agent-save-template-replace');
            if (confirm) confirm.focus();
        }

        /**
         * It landed: the layer says where it went and offers one way out.
         *
         * @param {object} template  The stored row.
         * @param {string} title
         * @returns {void}
         */
        function settle(template, title) {
            clear(body);
            body.append(h('p', { class: 'save-template-done', role: 'status' },
                COPY.saved(title, ITEMS.categoryLabel(template.category) || template.category)));
            modal.setActions([{ label: COPY.done, tone: 'primary', id: DONE_ID }]);
            const done = modal.element.querySelector(`#${DONE_ID}`);
            if (done) done.focus();
            if (view.onSaved) view.onSaved(template);
        }
    }

    return { CUSTOM, COPY, fieldsFromForm, defaultTitle, open };
})();
