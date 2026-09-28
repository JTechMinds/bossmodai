// Browser Vision — in-page inspection for element marks.
//
// One self-contained function, evaluated by browser_host.py in the page and
// in every frame. It reads the DOM only (no model, no guessing):
//   {mode: "collect"}         → the frame's visible, hit-testable controls;
//   {mode: "describe", x, y}  → what a click at (x, y) would land on;
//   {mode: "element", x, y}   → the <select> at (x, y), as a handle, or null.
// Coordinates are this frame's viewport CSS px.
//
// Candidates come from three rules, each only adding to the set:
//   1. the control selector below (links, buttons, fields, ARIA roles, …);
//   2. popup-list rows: the repeated children of a floating container
//      (position absolute/fixed, numeric z-index >= 1), kind "option" —
//      autocomplete rows (Google Places .pac-item) have no role and get
//      their handlers from script, so rule 1 cannot see them;
//   3. cursor:pointer boxes: the outermost one only, since cursor is
//      inherited by every span inside a clickable card.
(arg) => {
    const SELECTOR = [
        'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
        '[contenteditable=""]', '[contenteditable=true]',
        '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=tab]',
        '[role=menuitem]', '[role=option]', '[role=switch]', '[role=combobox]',
        '[role=textbox]', '[role=searchbox]',
        '[onclick]', '[tabindex]:not([tabindex="-1"])',
    ].join(',');
    const TEXT_TYPES = new Set(['', 'text', 'search', 'email', 'url', 'tel', 'password', 'number',
        'date', 'datetime-local', 'month', 'week', 'time']);
    const BUTTON_TYPES = new Set(['button', 'submit', 'reset', 'image']);
    const ROLE_KIND = {
        button: 'button', link: 'link', checkbox: 'checkbox', radio: 'radio', tab: 'tab',
        menuitem: 'menuitem', option: 'option', switch: 'switch', textbox: 'textbox', searchbox: 'textbox',
    };
    const MIN_PX = 4;
    const SAME_BOX_PX = 2;
    const NAME_MAX = 40;
    const FLOATING_POSITIONS = new Set(['absolute', 'fixed']);

    const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
    const clip = (s) => (s.length > NAME_MAX ? `${s.slice(0, NAME_MAX - 1)}…` : s);
    const parentOf = (el) => el.parentElement || (el.getRootNode() && el.getRootNode().host) || null;

    function kindOf(el) {
        const tag = el.tagName;
        const role = (el.getAttribute('role') || '').trim().toLowerCase();
        if (tag === 'SELECT') return 'select';
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'INPUT') {
            const type = (el.getAttribute('type') || '').toLowerCase();
            if (type === 'checkbox') return 'checkbox';
            if (type === 'radio') return 'radio';
            if (BUTTON_TYPES.has(type)) return 'button';
            if (TEXT_TYPES.has(type)) return 'textbox';
            return 'other';
        }
        if (ROLE_KIND[role]) return ROLE_KIND[role];
        if (tag === 'A' && el.hasAttribute('href')) return 'link';
        if (tag === 'BUTTON' || tag === 'SUMMARY') return 'button';
        if (el.isContentEditable) return 'textbox';
        return 'other';
    }

    function labelText(el) {
        if (el.labels && el.labels.length) return Array.from(el.labels).map((l) => l.innerText || l.textContent).join(' ');
        const around = el.closest && el.closest('label');
        return around ? (around.innerText || around.textContent) : '';
    }

    function nameOf(el) {
        const isButtonInput = el.tagName === 'INPUT' && BUTTON_TYPES.has((el.getAttribute('type') || '').toLowerCase());
        const img = el.querySelector && el.querySelector('img[alt]');
        const tries = [
            () => el.getAttribute('aria-label'),
            () => (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map((id) => {
                const root = el.getRootNode();
                const node = (root.getElementById && root.getElementById(id)) || document.getElementById(id);
                return node ? node.textContent : '';
            }).join(' '),
            () => labelText(el),
            // A select's text is every option; its current choice says more.
            () => (el.tagName === 'SELECT' ? (el.selectedOptions[0] ? el.selectedOptions[0].text : '') : el.innerText),
            () => el.getAttribute('placeholder'),
            () => el.getAttribute('title'),
            () => el.getAttribute('alt') || (img ? img.getAttribute('alt') : ''),
            () => (isButtonInput || el.tagName === 'BUTTON' ? el.value : ''),
        ];
        for (const attempt of tries) {
            const text = clean(attempt());
            if (text) return clip(text);
        }
        return '';
    }

    function stateOf(el, kind) {
        if (kind === 'checkbox' || kind === 'radio' || kind === 'switch') {
            const checked = el.tagName === 'INPUT' ? el.checked : el.getAttribute('aria-checked') === 'true';
            return checked ? 'checked' : 'unchecked';
        }
        if (kind === 'textbox') {
            const own = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ? el.value : el.textContent;
            return String(own || '').length ? 'filled' : 'empty';
        }
        return null;
    }

    function visibleRect(el) {
        const r = el.getBoundingClientRect();
        const x0 = Math.max(0, r.left);
        const y0 = Math.max(0, r.top);
        const x1 = Math.min(window.innerWidth, r.right);
        const y1 = Math.min(window.innerHeight, r.bottom);
        if (x1 - x0 < MIN_PX || y1 - y0 < MIN_PX) return null;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) === 0) return null;
        if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return null;
        return [x0, y0, x1, y1];
    }

    function hitsElement(el, x, y) {
        const root = el.getRootNode();
        const hit = root.elementFromPoint ? root.elementFromPoint(x, y) : document.elementFromPoint(x, y);
        if (!hit) return false;
        if (hit === el || el.contains(hit)) return true;
        const label = hit.closest && hit.closest('label');
        return Boolean(label && (label.control === el || (el.labels && Array.from(el.labels).includes(label))));
    }

    function clickPoint(el, rect) {
        const [x0, y0, x1, y1] = rect;
        const w = x1 - x0;
        const h = y1 - y0;
        const points = [
            [x0 + w / 2, y0 + h / 2],
            [x0 + w / 4, y0 + h / 4], [x0 + 3 * w / 4, y0 + h / 4],
            [x0 + w / 4, y0 + 3 * h / 4], [x0 + 3 * w / 4, y0 + 3 * h / 4],
        ];
        return points.find(([x, y]) => hitsElement(el, x, y)) || null;
    }

    // Every element in document order, descending into open shadow roots.
    function walk(root, out) {
        for (const el of root.querySelectorAll('*')) {
            out.push(el);
            if (el.shadowRoot) walk(el.shadowRoot, out);
        }
    }

    // A visible, positioned element stacked above the page (rule 2).
    function isFloating(el) {
        const style = getComputedStyle(el);
        if (!FLOATING_POSITIONS.has(style.position)) return false;
        const z = Number(style.zIndex); // "auto" → NaN
        return Number.isFinite(z) && z >= 1 && visibleRect(el) !== null;
    }

    // A floating container's list rows: its children (or, through a single
    // wrapper, the wrapper's children) that repeat a sibling's tag and class
    // and have text. Visibility and hit testing are checked with every
    // candidate later.
    function floatingRows(container) {
        let rows = Array.from(container.children);
        if (rows.length === 1) rows = Array.from(rows[0].children);
        const shape = (el) => `${el.tagName}|${el.getAttribute('class') || ''}`;
        return rows.filter((row) => clean(row.innerText)
            && rows.some((other) => other !== row && shape(other) === shape(row)));
    }

    // The full candidate set (rules 1–3) for this frame, and which of them
    // are popup rows (named by their text, kind "option").
    function candidateSet() {
        const every = [];
        walk(document, every);
        const candidates = new Set(every.filter((el) => el.matches(SELECTOR)));
        const rows = new Set();
        for (const el of every) {
            if (!isFloating(el)) continue;
            for (const row of floatingRows(el)) {
                if (!candidates.has(row)) rows.add(row);
            }
        }
        for (const row of rows) candidates.add(row);
        const pointer = new Map();
        const isPointer = (el) => {
            if (!pointer.has(el)) pointer.set(el, getComputedStyle(el).cursor === 'pointer');
            return pointer.get(el);
        };
        // Document order puts ancestors first. An element whose nearest
        // cursor:pointer ancestor is a candidate — or was itself left out for
        // that reason — is inside a box already marked, so it is left out too.
        const inside = new Set();
        for (const el of every) {
            if (candidates.has(el) || !isPointer(el)) continue;
            let ancestor = parentOf(el);
            while (ancestor && !isPointer(ancestor)) ancestor = parentOf(ancestor);
            if (ancestor && (candidates.has(ancestor) || inside.has(ancestor))) {
                inside.add(el);
                continue;
            }
            candidates.add(el);
        }
        return { ordered: every.filter((el) => candidates.has(el)), candidates, rows };
    }

    function kindAndName(el, rows) {
        if (rows.has(el)) return { kind: 'option', name: clip(clean(el.innerText)) };
        return { kind: kindOf(el), name: nameOf(el) };
    }

    function sameBox(a, b) {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        return Math.abs(ra.left - rb.left) <= SAME_BOX_PX && Math.abs(ra.top - rb.top) <= SAME_BOX_PX
            && Math.abs(ra.right - rb.right) <= SAME_BOX_PX && Math.abs(ra.bottom - rb.bottom) <= SAME_BOX_PX;
    }

    function deepElementFromPoint(x, y) {
        let el = document.elementFromPoint(x, y);
        while (el && el.shadowRoot) {
            const inner = el.shadowRoot.elementFromPoint(x, y);
            if (!inner || inner === el) break;
            el = inner;
        }
        return el;
    }

    // What is at (x, y) and the candidate it belongs to (same rules as marks,
    // so click feedback names a popup row the way the legend does).
    function controlAt(x, y, candidates) {
        const hit = deepElementFromPoint(x, y);
        if (!hit) return { hit: null, control: null };
        let el = hit;
        while (el && !candidates.has(el)) el = parentOf(el);
        if (!el) {
            const label = hit.closest && hit.closest('label');
            if (label && label.control) el = label.control;
        }
        return { hit, control: el };
    }

    if (arg.mode === 'collect') {
        const { ordered, candidates, rows } = candidateSet();
        const out = [];
        for (const el of ordered) {
            let ancestor = parentOf(el);
            while (ancestor && !candidates.has(ancestor)) ancestor = parentOf(ancestor);
            if (ancestor && sameBox(ancestor, el)) continue;
            const rect = visibleRect(el);
            if (!rect) continue;
            const point = clickPoint(el, rect);
            if (!point) continue;
            const { kind, name } = kindAndName(el, rows);
            out.push({ kind, name, rect, point, state: stateOf(el, kind) });
        }
        return out;
    }
    if (arg.mode === 'describe') {
        const { candidates, rows } = candidateSet();
        const { hit, control } = controlAt(arg.x, arg.y, candidates);
        if (!hit) return null;
        if (hit.tagName === 'IFRAME' || hit.tagName === 'FRAME') return { frame: true };
        if (control) return kindAndName(control, rows);
        return { tag: hit.tagName };
    }
    if (arg.mode === 'element') {
        const { candidates } = candidateSet();
        const { control } = controlAt(arg.x, arg.y, candidates);
        return control && control.tagName === 'SELECT' ? control : null;
    }
    throw new Error(`unknown mode ${arg.mode}`);
}
