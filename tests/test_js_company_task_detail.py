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
# The one "open a workspace path" module: the deliverable cards, chat links
# and links inside a viewed file all open through it.
OPENER = JS / "places" / "files" / "path-opener.js"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_deliverable_cards_keep_original_path_and_agent_id() -> None:
    """The path and the agent id it was recorded against travel together.

    The signature gained `api` because the helper takes the authenticated fetch
    by injection now instead of reaching for the global; `path` and `agentId`
    are still both parameters and are still passed through unmodified, which is
    the whole property.
    """
    cards = (TASKS / "task-deliverables.js").read_text(encoding="utf-8")
    assert "'data-agent-id': agentId" in cards
    # The opener moved to its own module, and openDeliverablePath became
    # openPath there: the same parameters, passed through unmodified.
    source = OPENER.read_text(encoding="utf-8")
    assert "function openPath(api, path, agentId)" in source
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
eval(`${fs.readFileSync(process.argv[1], "utf8")}\n;global.BossModPathOpener = BossModPathOpener;\n`);

const requests = [];
const viewed = [];
/** Each open's options, so the links it was handed can be followed. */
const viewerOpts = [];
global.BossModFileViewer = {
    open: async (path, opts) => {
        viewed.push({ path, agentId: opts.agentId, linksFor: typeof opts.linksFor === "function" });
        viewerOpts.push(opts);
    },
};
const DESK = {
    "/projects/x.md": { kind: "file", path: "/projects/x.md", company_path: "/floor-1/x.md" },
    "/me/x.md": { kind: "file", path: "/me/x.md", company_path: null },
    "/me/docs/runbook.md": { kind: "file", path: "/me/docs/runbook.md", company_path: null },
};
const COMPANY = {
    "/floor-1/reports": { kind: "directory", path: "/floor-1/reports" },
    "/floor-1/y.md": { kind: "file", path: "/floor-1/y.md" },
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
    const { openPath, linkPathFor } = global.BossModPathOpener;
    await openPath(api, "/projects/x.md", "a1");
    await openPath(api, "/me/x.md", "a1");
    let noAgentError = null;
    try {
        await openPath(api, "/projects/x.md", "");
    } catch (err) {
        noAgentError = err.message;
    }
    const beforeFolder = requests.length;
    let folderError = null;
    try {
        await openPath(api, "/floor-1/reports", "a1");
    } catch (err) {
        folderError = err.message;
    }
    const folderRequests = requests.splice(beforeFolder);

    // Follow a link out of each viewed file: the company file's and the
    // agent's own /me file's, through the linksFor each open was handed.
    const companyLinks = viewerOpts[0].linksFor("/floor-1/x.md");
    const deskLinks = viewerOpts[1].linksFor("/me/docs/e.md");
    const beforeLinks = requests.length;
    const viewedBeforeLinks = viewed.length;
    await companyLinks.open(companyLinks.resolve("y.md"));
    await deskLinks.open(deskLinks.resolve("runbook.md"));
    const followed = {
        companyResolvesMe: companyLinks.resolve("/me/a.md"),
        deskResolvesProjects: deskLinks.resolve("/projects/a.md"),
        requests: requests.splice(beforeLinks),
        viewed: viewed.splice(viewedBeforeLinks),
    };

    const linkPaths = {
        sibling: linkPathFor("/f1/p/docs/e.md", "runbook.md", false),
        dotSlash: linkPathFor("/f1/p/docs/e.md", "./a.md", false),
        parent: linkPathFor("/f1/p/docs/e.md", "../evidence/x.tap", false),
        clampsAtRoot: linkPathFor("/f1/p/docs/e.md", "../../../../x", false),
        deskProjects: linkPathFor("/me/docs/e.md", "/projects/a.md", true),
        companyProjects: linkPathFor("/f1/p/docs/e.md", "/projects/a.md", false),
        companyMe: linkPathFor("/f1/p/docs/e.md", "/me", false),
        companyHostPath: linkPathFor("/f1/p/docs/e.md", "/home/u/r.md", false),
    };
    process.stdout.write(`${JSON.stringify({
        requests, viewed, noAgentError, folderRequests, folderError, followed, linkPaths,
    })}\n`);
})().catch((err) => { console.error(err); process.exit(1); });
"""


def _open_script_payload() -> dict:
    """Run _OPEN_SCRIPT against the real path-opener.js and parse its report."""
    result = subprocess.run(
        ["node", "-e", _OPEN_SCRIPT, str(OPENER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_deliverable_open_resolves_agent_virtual_paths_through_the_desk() -> None:
    """`/projects` and `/me` resolve in the agent's namespace, never the company root.

    A `/projects` file opens at the company path the desk returns, so the
    viewer's image preview and Save use company endpoints. A `/me` file has no
    company path and opens through the desk endpoint. With no agent recorded,
    the open rejects instead of guessing whose namespace it is. A company
    folder whose open-folder request fails rejects with the server's text.

    The viewer is told the namespace by `agentId` now rather than handed the
    desk's URLs: `''` is the company workspace, `a1` is a1's desk. Every open
    hands it `linksFor`, so links inside the file open too.
    """
    payload = _open_script_payload()
    assert payload["requests"] == [
        {"url": "/api/agents/a1/desk?path=%2Fprojects%2Fx.md", "method": "GET"},
        {"url": "/api/agents/a1/desk?path=%2Fme%2Fx.md", "method": "GET"},
    ]
    assert payload["viewed"] == [
        {"path": "/floor-1/x.md", "agentId": "", "linksFor": True},
        {"path": "/me/x.md", "agentId": "a1", "linksFor": True},
    ]
    assert payload["noAgentError"] == "That path belongs to an agent, but no agent is recorded for it."
    # A company folder whose open-folder POST fails rejects with the server's
    # reason, so the card can mark itself failed instead of doing nothing.
    assert payload["folderRequests"] == [
        {"url": "/api/company/files?path=%2Ffloor-1%2Freports", "method": "GET"},
        {"url": "/api/company/files/open-folder", "method": "POST"},
    ]
    assert payload["folderError"] == "desk_open_folder_handler_invalid"


def test_links_inside_a_viewed_file_open_as_the_same_agent() -> None:
    """A link in a viewed file opens through openPath, in the file's namespace.

    openFile hands the viewer a `linksFor` whose links open with the agentId
    the file was opened with: a company file's link opens in the company
    workspace, and a `/me` file's link resolves through that agent's desk.
    In the company namespace `/me` names nobody's desk, so it stays text; in
    a desk, `/projects` is kept for that agent's desk to resolve.
    """
    followed = _open_script_payload()["followed"]
    assert followed["companyResolvesMe"] is None
    assert followed["deskResolvesProjects"] == "/projects/a.md"
    assert followed["requests"] == [
        {"url": "/api/company/files?path=%2Ffloor-1%2Fy.md", "method": "GET"},
        {"url": "/api/agents/a1/desk?path=%2Fme%2Fdocs%2Frunbook.md", "method": "GET"},
    ]
    assert followed["viewed"] == [
        {"path": "/floor-1/y.md", "agentId": "", "linksFor": True},
        {"path": "/me/docs/runbook.md", "agentId": "a1", "linksFor": True},
    ]


def test_link_paths_resolve_beside_the_viewed_file() -> None:
    """linkPathFor: a relative link sits in the viewed file's folder.

    `.` is dropped and `..` pops a segment but never climbs above `/`, as
    core/bm_cli/virtual_fs.py normalize_cli_path does. An absolute link is
    kept, except `/me` and `/projects` in the company namespace, where no
    agent owns the file. The server is still the jail for whatever is kept.
    """
    assert _open_script_payload()["linkPaths"] == {
        "sibling": "/f1/p/docs/runbook.md",
        "dotSlash": "/f1/p/docs/a.md",
        "parent": "/f1/p/evidence/x.tap",
        "clampsAtRoot": "/x",
        "deskProjects": "/projects/a.md",
        "companyProjects": None,
        "companyMe": None,
        "companyHostPath": "/home/u/r.md",
    }


def test_deliverable_open_does_not_remap_host_paths_through_me() -> None:
    source = OPENER.read_text(encoding="utf-8")
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
    # Cancel is a status action on Edit mode's status row now, beside Resume
    # and Mark complete; the detail still only asks.
    assert "button('ct-cancel-task-btn', 'Cancel task…', actions.cancel, true)" in detail
    assert "/api/tasks" not in detail, (
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
