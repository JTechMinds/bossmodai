"""Task detail: deliverable links must open files and host folders.

Re-pointed in Phase 3A from company-task-detail.js / company-tasks.js to the
Tasks place's modules. Every assertion below guards the same property it always did;
where a literal changed it is because the markup is now built with h() rather
than concatenated into a string, and the note on each says so.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
TASKS = JS / "places" / "tasks"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_deliverable_cards_keep_original_path_and_agent_id() -> None:
    """The path and the agent id it was recorded against travel together.

    The signature gained `api` because the helper takes the authenticated fetch
    by injection now instead of reaching for the global; `path` and `agentId`
    are still both parameters and are still passed through unmodified, which is
    the whole property.
    """
    source = (TASKS / "task-deliverables.js").read_text(encoding="utf-8")
    assert "function openDeliverablePath(api, path, agentId)" in source
    assert "'data-agent-id': agentId" in source
    assert "isAgentVirtualPath(target)" in source
    assert "/api/agents/${encodeURIComponent(agentId)}/desk?path=" in source
    assert "/api/company/files?path=" in source
    assert "/api/company/files/open-folder" in source
    assert "payload.kind === 'file'" in source
    assert "BossModFileViewer.open" in source


# Loads the real module against a recording api and viewer, and reports what
# each open asked for. Paths are argv so the script stays a literal.
_OPEN_SCRIPT = r"""
const fs = require("fs");
const { installDom } = require(process.argv[1]);
installDom();
eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModTaskDeliverables = BossModTaskDeliverables;\n`);

const requests = [];
const viewed = [];
global.BossModFileViewer = {
    open: async (path, opts) => { viewed.push({ path, apiUrl: opts.apiUrl || null }); },
};
const DESK = {
    "/projects/x.md": { kind: "file", path: "/projects/x.md", company_path: "/floor-1/x.md" },
    "/me/x.md": { kind: "file", path: "/me/x.md", company_path: null },
};
const COMPANY = {
    "/floor-1/reports": { kind: "directory", path: "/floor-1/reports" },
};
async function api(url, init) {
    requests.push({ url, method: (init && init.method) || "GET" });
    // The host refuses the company open-folder request, the way it does when
    // the operator's folder handler is not usable.
    if (url === "/api/company/files/open-folder") {
        return { ok: false, async json() { return {}; },
                 async text() { return "desk_open_folder_handler_invalid"; } };
    }
    const path = decodeURIComponent(String(url).split("path=")[1] || "");
    const listing = String(url).startsWith("/api/company/files?") ? COMPANY[path] : DESK[path];
    return { ok: true, async json() { return listing; }, async text() { return ""; } };
}

(async () => {
    const { openDeliverablePath } = global.BossModTaskDeliverables;
    await openDeliverablePath(api, "/projects/x.md", "a1");
    await openDeliverablePath(api, "/me/x.md", "a1");
    let noAgentError = null;
    try {
        await openDeliverablePath(api, "/projects/x.md", "");
    } catch (err) {
        noAgentError = err.message;
    }
    const beforeFolder = requests.length;
    let folderError = null;
    try {
        await openDeliverablePath(api, "/floor-1/reports", "a1");
    } catch (err) {
        folderError = err.message;
    }
    const folderRequests = requests.splice(beforeFolder);
    process.stdout.write(`${JSON.stringify({ requests, viewed, noAgentError, folderRequests, folderError })}\n`);
})().catch((err) => { console.error(err); process.exit(1); });
"""


def test_deliverable_open_resolves_agent_virtual_paths_through_the_desk() -> None:
    """`/projects` and `/me` resolve in the agent's namespace, never the company root.

    A `/projects` file opens at the company path the desk returns, so the
    viewer's image preview and Save use company endpoints. A `/me` file has no
    company path and opens through the desk endpoint. With no agent recorded,
    the open rejects instead of guessing whose namespace it is. A company
    folder whose open-folder request fails rejects with the server's text.
    """
    tests = Path(__file__).resolve().parent
    result = subprocess.run(
        [
            "node", "-e", _OPEN_SCRIPT,
            str(tests / "js_fake_dom.cjs"),
            str(JS / "core" / "dom.js"),
            str(TASKS / "task-deliverables.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["requests"] == [
        {"url": "/api/agents/a1/desk?path=%2Fprojects%2Fx.md", "method": "GET"},
        {"url": "/api/agents/a1/desk?path=%2Fme%2Fx.md", "method": "GET"},
    ]
    assert payload["viewed"] == [
        {"path": "/floor-1/x.md", "apiUrl": None},
        {"path": "/me/x.md", "apiUrl": "/api/agents/a1/desk?path=%2Fme%2Fx.md"},
    ]
    assert payload["noAgentError"] == "That path belongs to an agent, but no agent is recorded for it."
    # A company folder whose open-folder POST fails rejects with the server's
    # reason, so the card can mark itself failed instead of doing nothing.
    assert payload["folderRequests"] == [
        {"url": "/api/company/files?path=%2Ffloor-1%2Freports", "method": "GET"},
        {"url": "/api/company/files/open-folder", "method": "POST"},
    ]
    assert payload["folderError"] == "desk_open_folder_handler_invalid"


def test_deliverable_open_does_not_remap_host_paths_through_me() -> None:
    source = (TASKS / "task-deliverables.js").read_text(encoding="utf-8")
    assert "'/agents/' + storageKey" not in source
    assert "virtualPath.slice(3)" not in source


def test_file_viewer_open_surfaces_load_failures() -> None:
    """Re-pointed in Phase 3B: the shared viewer is places/files/file-viewer.js.

    A deliverable card marks itself failed from this rejection, so a viewer
    that swallowed the load error would leave a card that silently does
    nothing when clicked.
    """
    source = (JS / "places" / "files" / "file-viewer.js").read_text(encoding="utf-8")
    assert "throw err;" in source


def test_cancel_task_button_and_confirm_copy() -> None:
    """The cancel button, its confirm copy, and the open/finished distinction.

    The detail half keeps the button, its label, and its id. `setCancelCallback`
    is gone and the assertion is stronger for it: the panel is handed `actions`
    (BossModTaskActions) at construction and is asserted never to name the
    cancel route itself, so there is exactly one place in Tasks that can end a
    task.

    The table half moved to the Tasks place's modules. `statusFilter`, its three
    chips, and `matchesBoardFilter` were the mechanism by which the old table
    let the operator see active work separately from finished work; four
    columns, with closed work marked inside Done, are that mechanism now, and
    the assertions below pin the same distinction — open, done, and
    closed-without-completing are three counts, and `cancelled` is terminal.
    """
    detail = (TASKS / "task-detail.js").read_text(encoding="utf-8")
    assert "option('ct-cancel-task-btn', 'Cancel task', actions.cancelOne)" in detail
    assert "/api/tasks/cancel" not in detail, (
        "the detail asks for a cancel; the Tasks place performs it"
    )

    cancel = (TASKS / "tasks-cancel.js").read_text(encoding="utf-8")
    toolbar = (TASKS / "tasks-toolbar.js").read_text(encoding="utf-8")
    columns = (TASKS / "tasks-columns.js").read_text(encoding="utf-8")
    data = (TASKS / "tasks-data.js").read_text(encoding="utf-8")

    assert "Cancel this task?" in cancel
    assert "Cancel ${ids.length} tasks?" in cancel
    assert "Cancel selected" in toolbar
    assert "/api/tasks/cancel" in cancel

    assert "TERMINAL_STATUSES" in columns
    assert "'cancelled'" in columns
    assert "{ id: 'backlog'" in columns
    assert "{ id: 'done'" in columns
    # open / done / closed are three separate counts, so "active" and
    # "done/cancelled" are still distinguishable — without ever calling a
    # cancelled task done.
    for field in ("totals.open", "totals.done", "totals.closed"):
        assert field in data, f"the summary lost {field}"
    # The words a closed-without-completing task is marked with. They moved
    # from the place's summary into the column map, beside the statuses they
    # describe, so every surface that marks one reads the same phrase.
    assert "const CLOSED_NOTE = 'closed without completing';" in columns
