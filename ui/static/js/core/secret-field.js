/**
 * BossMod AI — one masked secret control.
 *
 * The bullets are paint. A password input can show them while the Save click
 * still reads an empty string: the paste already committed on `input`.
 * Save reads that text. A bullet placeholder is not a token, and an empty
 * control stays empty — it does not invent a secret.
 */
const BossModSecretField = (() => {
    const MASK_CLASS = 'bm-secret-masked';

    function placeholderOf(input) {
        if (!input) return '';
        const prop = input.placeholder;
        if (typeof prop === 'string' && prop) return prop;
        if (typeof input.getAttribute === 'function') {
            return input.getAttribute('placeholder') || '';
        }
        return '';
    }

    function isMask(text, placeholder) {
        const value = String(text || '').trim();
        if (!value) return true;
        const mask = String(placeholder || '').trim();
        if (mask && value === mask) return true;
        return /^[•●]+$/.test(value);
    }

    function remember(input) {
        if (!input || !input.dataset) return;
        const live = String(input.value || '');
        if (isMask(live, placeholderOf(input))) {
            delete input.dataset.secretValue;
            return;
        }
        input.dataset.secretValue = live;
    }

    function bind(input) {
        if (!input || !input.dataset || input.dataset.secretBound === 'true') return input;
        input.dataset.secretBound = 'true';
        input.addEventListener('input', () => remember(input));
        input.addEventListener('change', () => remember(input));
        remember(input);
        return input;
    }

    function read(input) {
        if (!input) return '';
        const placeholder = placeholderOf(input);
        const live = String(input.value || '');
        if (!isMask(live, placeholder)) return live.trim();
        const snap = input.dataset ? String(input.dataset.secretValue || '') : '';
        if (!isMask(snap, placeholder)) return snap.trim();
        return '';
    }

    function clear(input) {
        if (!input) return;
        input.value = '';
        if (input.dataset) delete input.dataset.secretValue;
    }

    /**
     * Reveal or cover the committed text. The value stays the paste.
     * @param {HTMLInputElement} input
     * @param {HTMLButtonElement|null} button
     */
    function toggle(input, button) {
        if (!input) return;
        const reveal = input.classList.contains(MASK_CLASS);
        input.classList.toggle(MASK_CLASS, !reveal);
        if (input.type === 'password') input.type = 'text';
        if (button) {
            button.textContent = reveal ? 'Hide' : 'Show';
            button.setAttribute('aria-pressed', reveal ? 'true' : 'false');
        }
    }

    return { MASK_CLASS, bind, read, clear, toggle };
})();
