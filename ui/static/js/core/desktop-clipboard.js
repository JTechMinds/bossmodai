/**
 * BossMod AI — the desktop shell's native clipboard image read.
 *
 * In the Tauri WebKitGTK webview a pasted screenshot reaches the page's
 * `paste` event as neither a file nor text. The shell reads the system
 * clipboard itself (`read_clipboard_image_png` in
 * desktop/src/clipboard_image.rs) and returns PNG bytes. In a plain browser
 * there is no shell, and `available()` says so.
 */
const BossModDesktopClipboard = (() => {

    const COMMAND = 'read_clipboard_image_png';

    function invoker() {
        const api = globalThis.__TAURI__;
        const core = api && api.core;
        return core && typeof core.invoke === 'function' ? core.invoke : null;
    }

    /**
     * True inside the desktop shell, where the native read exists.
     * @returns {boolean}
     */
    function available() {
        return invoker() !== null;
    }

    function stamp(at) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${at.getFullYear()}${pad(at.getMonth() + 1)}${pad(at.getDate())}`
            + `-${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`;
    }

    /**
     * Read the clipboard image as a PNG file ready to upload.
     *
     * @returns {Promise<File>} `pasted-image-<YYYYMMDD-HHMMSS>.png`, image/png.
     * @throws {Error} When called outside the desktop shell, or with the
     *   shell's reason when the clipboard holds no readable image.
     */
    async function readImageFile() {
        const invoke = invoker();
        if (!invoke) throw new Error('[desktop-clipboard] the desktop shell is not available');
        let bytes;
        try {
            bytes = await invoke(COMMAND);
        } catch (err) {
            // Tauri rejects with the command's error string, not an Error.
            throw new Error(typeof err === 'string' ? err : ((err && err.message) || String(err)));
        }
        if (!(bytes instanceof ArrayBuffer)) {
            throw new Error('[desktop-clipboard] the shell returned no image bytes');
        }
        return new File([bytes], `pasted-image-${stamp(new Date())}.png`, { type: 'image/png' });
    }

    return { available, readImageFile };
})();
