/**
 * BossMod AI — one click interceptor for http(s) anchors.
 *
 * Markdown (transcript, file viewer) and a contenteditable composer both
 * paint `https://…` as a real `<a>`. In a browser `target="_blank"` is enough.
 * In the Tauri webview it is not: the link looks live and the click goes
 * nowhere, and following the href in-app would replace the shell with
 * github.com. So every http(s) click — transcript, composer, marketplace
 * byline, anywhere — is caught once, here, and handed to the desktop's
 * `open_external_url` command (or `window.open` when there is no Tauri).
 *
 * javascript: and file: never reach that command. Relative paths, hashes,
 * and mailto: are left for the openers that already own them.
 */
const BossModExternalOpen = (() => {

    const COMMAND = 'open_external_url';

    function desktopApi() {
        return globalThis.__TAURI__ || null;
    }

    /**
     * An absolute http(s) URL, or null.
     *
     * The attribute has to *start* with http:// or https://. Resolving a
     * relative `/files` or `#settings` against the document base would make
     * those look like http(s) and steal them from the openers that already
     * handle company files and Settings.
     *
     * @param {string|null|undefined} href
     * @returns {string|null}
     */
    function httpUrlFrom(href) {
        const raw = String(href == null ? '' : href).trim();
        const lower = raw.toLowerCase();
        if (!lower.startsWith('http://') && !lower.startsWith('https://')) return null;
        let url;
        try {
            url = new URL(raw);
        } catch (err) {
            console.warn('[external-open] unparseable link', raw, err);
            return null;
        }
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
        if (!url.hostname) return null;
        return url.href;
    }

    /**
     * Hand an http(s) URL to the desktop opener, or to a new browser tab.
     *
     * Never assigns `location`. That is the whole bug this exists to stop.
     *
     * @param {string} href
     * @returns {boolean} true when this module took the URL.
     */
    function openHttpUrl(href) {
        const url = httpUrlFrom(href);
        if (!url) return false;
        const api = desktopApi();
        if (api && api.core && typeof api.core.invoke === 'function') {
            void api.core.invoke(COMMAND, { url }).catch((err) => {
                console.error('[external-open] could not open in the browser', err);
            });
            return true;
        }
        if (typeof window.open === 'function') {
            window.open(url, '_blank', 'noopener,noreferrer');
            return true;
        }
        return false;
    }

    /**
     * Capture-phase click handler. Left-click on an http(s) `<a>` only.
     *
     * @param {MouseEvent|object} event
     * @returns {boolean} true when the click was taken.
     */
    function handleClick(event) {
        if (!event || event.defaultPrevented) return false;
        if (event.button != null && event.button !== 0) return false;
        const node = event.target;
        if (!node || typeof node.closest !== 'function') return false;
        const anchor = node.closest('a');
        if (!anchor) return false;
        const url = httpUrlFrom(anchor.getAttribute('href'));
        if (!url) return false;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();
        return openHttpUrl(url);
    }

    /**
     * Listen on the document so transcript and composer share one handler.
     *
     * @returns {() => void} Unbind.
     */
    function install() {
        document.addEventListener('click', handleClick, true);
        return function destroy() {
            document.removeEventListener('click', handleClick, true);
        };
    }

    return { COMMAND, httpUrlFrom, openHttpUrl, handleClick, install };
})();
