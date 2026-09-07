/**
 * BossMod AI — global keyboard shortcuts.
 *
 * Ctrl/Cmd 1-6 reach the six places, and Escape backs out of the outermost
 * shell surface. Every shortcut has a visible control behind it; none of them
 * is the only way to do anything.
 */
const BossModShortcuts = (() => {

    /**
     * Bind the shortcuts to the document.
     *
     * @param {object} deps
     * @param {string[]} deps.placeIds  Nav order; index 0 answers to Ctrl/Cmd+1.
     * @param {(placeId: string) => void} deps.navigate
     * @param {() => void} deps.onEscape  Closes the outermost shell surface.
     * @returns {() => void} disposer
     */
    function bind(deps) {
        const { placeIds, navigate, onEscape } = deps;

        function onKeydown(event) {
            if (event.key === 'Escape') {
                // A modal binds its own Escape handler when it opens — after
                // this one — and is still in the document while this runs, so
                // Escape belongs to the innermost surface first.
                if (document.querySelector('[role="dialog"]')) return;
                onEscape();
                return;
            }
            if (event.altKey || (!event.ctrlKey && !event.metaKey)) return;
            const index = Number(event.key) - 1;
            if (!Number.isInteger(index) || index < 0 || index >= placeIds.length) return;
            event.preventDefault();
            navigate(placeIds[index]);
        }

        document.addEventListener('keydown', onKeydown);
        return () => document.removeEventListener('keydown', onKeydown);
    }

    return { bind };
})();
