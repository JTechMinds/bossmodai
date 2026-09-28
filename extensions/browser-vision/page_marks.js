// Browser Vision — in-page inspection for element marks.
//
// One self-contained function, evaluated by browser_host.py in the page and
// in every frame. It reads the DOM only (no model, no guessing):
//   {mode: "collect"}         → the frame's visible, hit-testable controls;
//   {mode: "describe", x, y}  → what a click at (x, y) would land on;
//   {mode: "element", x, y}   → the <select> at (x, y), as a handle, or null.
// Coordinates are this frame's viewport CSS px.
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

    function walk(root, out) {
        for (const el of root.querySelectorAll('*')) {
            if (el.matches(SELECTOR)) out.push(el);
            if (el.shadowRoot) walk(el.shadowRoot, out);
        }
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

    function controlAt(x, y) {
        const hit = deepElementFromPoint(x, y);
        if (!hit) return { hit: null, control: null };
        let el = hit;
        while (el && !el.matches(SELECTOR)) el = parentOf(el);
        if (!el) {
            const label = hit.closest && hit.closest('label');
            if (label && label.control) el = label.control;
        }
        return { hit, control: el };
    }

    if (arg.mode === 'collect') {
        const all = [];
        walk(document, all);
        const candidates = new Set(all);
        const out = [];
        for (const el of all) {
            let ancestor = parentOf(el);
            while (ancestor && !candidates.has(ancestor)) ancestor = parentOf(ancestor);
            if (ancestor && sameBox(ancestor, el)) continue;
            const rect = visibleRect(el);
            if (!rect) continue;
            const point = clickPoint(el, rect);
            if (!point) continue;
            const kind = kindOf(el);
            out.push({ kind, name: nameOf(el), rect, point, state: stateOf(el, kind) });
        }
        return out;
    }
    if (arg.mode === 'describe') {
        const { hit, control } = controlAt(arg.x, arg.y);
        if (!hit) return null;
        if (hit.tagName === 'IFRAME' || hit.tagName === 'FRAME') return { frame: true };
        if (control) return { kind: kindOf(control), name: nameOf(control) };
        return { tag: hit.tagName };
    }
    if (arg.mode === 'element') {
        const { control } = controlAt(arg.x, arg.y);
        return control && control.tagName === 'SELECT' ? control : null;
    }
    throw new Error(`unknown mode ${arg.mode}`);
}
