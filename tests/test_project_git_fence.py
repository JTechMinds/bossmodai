"""Project workspaces stay outside the application git repository.

New projects get their own ``.git``. Agent git that resolves to the
application install, or that leaves the bound project via ``cd ..``,
``-C``, or an absolute path, is refused with an explicit reason.
Operator CLI Deny rows are not rewritten.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli import install_layout
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.project_git_fence import project_git_fence_reason
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.session import get_cli_cwd, set_cli_cwd
from db.cli_policy_rules import reconcile_hardened_seed_rules
from db.floors import LOBBY_ID


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _lobby_projects() -> Path:
    """The folder a Lobby agent sees as ``/projects``. New agents live in Lobby."""
    return floor_root(LOBBY_ID)


def _agent_and_state():
    agent = db.create_agent("Repo Clerk", role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_app(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    init = _git(path, "init")
    assert init.returncode == 0, init.stderr
    _git(path, "config", "user.email", "app@example.test")
    _git(path, "config", "user.name", "App")
    (path / "README.md").write_text("application\n", encoding="utf-8")
    assert _git(path, "add", "README.md").returncode == 0
    assert _git(path, "commit", "-m", "app").returncode == 0


def _branches(path: Path) -> str:
    listed = _git(path, "branch", "--list")
    assert listed.returncode == 0, listed.stderr
    return listed.stdout


def test_create_project_gets_its_own_git_outside_the_install() -> None:
    agent, state = _agent_and_state()
    created = execute_bm_cli(agent, state, "mkdir /projects/poc-own")
    assert created.ok is True, created.detail

    project = _lobby_projects() / "poc-own"
    install = install_layout.app_install_root().resolve()
    assert (project / ".git").is_dir()
    toplevel = _git(project, "rev-parse", "--show-toplevel")
    assert toplevel.returncode == 0, toplevel.stderr
    assert Path(toplevel.stdout.strip()).resolve() == project.resolve()
    assert install not in project.resolve().parents
    assert project.resolve() != install
    assert not (_lobby_projects() / ".git").exists()


def test_existing_in_tree_projects_are_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "bossmod-install"
    legacy = install / "artifacts" / "projects" / "legacy-poc"
    legacy.mkdir(parents=True)
    (legacy / "keep.txt").write_text("operator data\n", encoding="utf-8")
    separated = tmp_path / "bossmod-data" / "company"
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(separated))

    agent, state = _agent_and_state()
    created = execute_bm_cli(agent, state, "mkdir /projects/fresh-poc")
    assert created.ok is True, created.detail

    assert (legacy / "keep.txt").read_text(encoding="utf-8") == "operator data\n"
    assert not (legacy / ".git").exists()
    fresh = separated / LOBBY_ID / "fresh-poc"
    assert (fresh / ".git").is_dir()
    assert install.resolve() not in fresh.resolve().parents

    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(install / "artifacts" / "company"))
    with pytest.raises(ValueError, match="outside the BossMod application install"):
        _lobby_projects()
    assert (legacy / "keep.txt").read_text(encoding="utf-8") == "operator data\n"


def test_git_targeting_the_application_work_tree_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    before = _branches(install)
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)

    agent, state = _agent_and_state()
    set_cli_cwd(agent.id, str(install))

    checkout = execute_bm_cli(agent, state, "git checkout -b poc-branch")
    assert checkout.ok is False
    assert checkout.kind == "project_git_fence"
    assert "Blocked —" in checkout.detail
    assert "application install" in checkout.detail
    assert "application install" in checkout.prompt_content
    assert _branches(install) == before

    commit = execute_bm_cli(agent, state, 'git commit -m "poc"')
    assert commit.ok is False
    assert "application install" in commit.detail
    assert _branches(install) == before

    notes = db.list_notifications(agent_id=agent.id, limit=10)
    assert any("application install" in (item.content or "") for item in notes)

    approved = execute_approved_command(
        agent,
        state,
        "git checkout -b poc-branch",
        approval_request_id="already-approved",
        cwd=str(install),
    )
    assert approved.ok is False
    assert "application install" in approved.detail
    assert _branches(install) == before


def test_git_escape_via_parent_dash_c_and_absolute_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    other = tmp_path / "other-repo"
    _init_app(other)
    before_install = _branches(install)
    before_other = _branches(other)
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)

    agent, state = _agent_and_state()
    created = execute_bm_cli(agent, state, "mkdir /projects/bound-poc")
    assert created.ok is True, created.detail
    entered = execute_bm_cli(agent, state, "cd /projects/bound-poc")
    assert entered.ok is True, entered.detail
    project = _lobby_projects() / "bound-poc"

    parent = execute_bm_cli(agent, state, "cd ..")
    assert parent.ok is True
    from_parent = execute_bm_cli(agent, state, "git checkout -b escaped")
    assert from_parent.ok is False
    assert from_parent.kind == "project_git_fence"
    assert "Blocked —" in from_parent.detail
    assert "bound project" in from_parent.detail
    assert _branches(install) == before_install

    back = execute_bm_cli(agent, state, "cd /projects/bound-poc")
    assert back.ok is True
    dash_c = execute_bm_cli(agent, state, "git -C .. checkout -b escaped")
    assert dash_c.ok is False
    assert dash_c.kind == "project_git_fence"
    assert "Blocked —" in dash_c.detail
    assert _branches(install) == before_install
    assert "escaped" not in _branches(project)

    absolute = execute_bm_cli(
        agent,
        state,
        f"git -C {install} checkout -b poc-branch",
    )
    assert absolute.ok is False
    assert "application install" in absolute.detail
    assert _branches(install) == before_install

    other_repo = execute_bm_cli(
        agent,
        state,
        f"git --git-dir {other / '.git'} --work-tree {other} checkout -b side",
    )
    assert other_repo.ok is False
    assert other_repo.kind == "project_git_fence"
    assert "Blocked —" in other_repo.detail
    assert _branches(other) == before_other
    assert _branches(install) == before_install


def test_git_commit_inside_the_project_does_not_touch_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    before = _branches(install)
    head = _git(install, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)

    agent, state = _agent_and_state()
    written = execute_bm_cli(
        agent,
        state,
        "write /projects/bound-poc/readme.txt",
        content="project notes\n",
    )
    assert written.ok is True, written.detail
    entered = execute_bm_cli(agent, state, "cd /projects/bound-poc")
    assert entered.ok is True, entered.detail

    added = execute_bm_cli(agent, state, "git add readme.txt")
    assert added.ok is True, added.detail
    committed = execute_bm_cli(agent, state, 'git commit -m "project note"')
    assert committed.ok is True, committed.detail

    project = _lobby_projects() / "bound-poc"
    toplevel = _git(project, "rev-parse", "--show-toplevel")
    assert Path(toplevel.stdout.strip()).resolve() == project.resolve()
    assert _git(project, "log", "-1", "--pretty=%s").stdout.strip() == "project note"
    assert _branches(install) == before
    assert _git(install, "rev-parse", "HEAD").stdout.strip() == head


def test_git_dash_c_projects_path_is_that_project_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git -C /projects/<slug>`` rewrites onto that project's real root.

    The command is issued from ``/me``. Status, commit, and checkout stay
    inside the project. The projects mount, the application install, and a
    real absolute path outside that project stay blocked.
    """
    _enable_shell()
    install = tmp_path / "app-checkout"
    _init_app(install)
    other = tmp_path / "other-repo"
    _init_app(other)
    before_install = _branches(install)
    head = _git(install, "rev-parse", "HEAD").stdout.strip()
    before_other = _branches(other)
    monkeypatch.setattr(install_layout, "app_install_root", lambda: install)

    agent, state = _agent_and_state()
    written = execute_bm_cli(
        agent,
        state,
        "write /projects/diablo-poc/readme.txt",
        content="project notes\n",
    )
    assert written.ok is True, written.detail
    project = _lobby_projects() / "diablo-poc"
    assert get_cli_cwd(agent.id) == "/me"

    status = execute_bm_cli(agent, state, "git -C /projects/diablo-poc status")
    assert status.ok is True, status.detail
    assert status.kind == "shell"
    assert "readme.txt" in (status.prompt_content or "")
    assert project_git_fence_reason(
        agent,
        parse_cli_command("git -C /projects/diablo-poc status"),
        "/me",
    ) is None

    absolute = execute_bm_cli(agent, state, f"git -C {project} status --short")
    assert absolute.ok is True, absolute.detail
    assert "readme.txt" in (absolute.prompt_content or "")

    added = execute_bm_cli(agent, state, "git -C /projects/diablo-poc add readme.txt")
    assert added.ok is True, added.detail
    committed = execute_bm_cli(
        agent,
        state,
        'git -C /projects/diablo-poc commit -m "project note"',
    )
    assert committed.ok is True, committed.detail
    assert _git(project, "log", "-1", "--pretty=%s").stdout.strip() == "project note"
    assert "readme.txt" in _git(project, "ls-files").stdout

    checkout = execute_bm_cli(
        agent,
        state,
        "git -C /projects/diablo-poc checkout -b poc-branch",
    )
    assert checkout.ok is False
    assert checkout.kind != "project_git_fence"
    assert checkout.approval_required is True
    assert "Switch branches" in (checkout.detail or "")
    assert "bound project" not in (checkout.detail or "")
    assert project_git_fence_reason(
        agent,
        parse_cli_command("git -C /projects/diablo-poc checkout -b poc-branch"),
        "/me",
    ) is None
    approved = execute_approved_command(
        agent,
        state,
        "git -C /projects/diablo-poc checkout -b poc-branch",
        approval_request_id=checkout.approval_request_id or "",
    )
    assert approved.ok is True, approved.detail
    assert "poc-branch" in _branches(project)
    assert _branches(install) == before_install
    assert _git(install, "rev-parse", "HEAD").stdout.strip() == head

    parent = execute_bm_cli(agent, state, "git -C /projects/diablo-poc/.. status")
    assert parent.ok is False
    assert parent.kind == "project_git_fence"
    assert "Blocked —" in parent.detail
    assert "bound project" in parent.detail
    assert "application install" not in parent.detail

    app = execute_bm_cli(agent, state, f"git -C {install} status")
    assert app.ok is False
    assert app.kind == "project_git_fence"
    assert "application install" in app.detail

    other_repo = execute_bm_cli(agent, state, f"git -C {other} status")
    assert other_repo.ok is False
    assert other_repo.kind == "project_git_fence"
    assert "Blocked —" in other_repo.detail
    assert "bound project" in other_repo.detail
    assert _branches(other) == before_other
    assert _branches(install) == before_install


def test_operator_deny_picks_stay_untouched() -> None:
    checkout = next(
        rule
        for rule in db.list_cli_policy_rules()
        if rule.pattern == "git checkout" and rule.agent_id is None
    )
    denied_checkout = db.update_cli_policy_rule(checkout.id, tier="never_allowed")
    assert denied_checkout is not None
    custom = db.create_cli_policy_rule(
        tier="never_allowed",
        pattern="curl http://deny-pick.example",
        match_mode="prefix",
        description="Operator deny pick",
    )
    policy_engine.reload()

    agent, state = _agent_and_state()
    _enable_shell()
    created = execute_bm_cli(agent, state, "mkdir /projects/deny-poc")
    assert created.ok is True, created.detail
    entered = execute_bm_cli(agent, state, "cd /projects/deny-poc")
    assert entered.ok is True
    blocked = execute_bm_cli(agent, state, "git checkout -b feature")
    assert blocked.ok is False
    assert blocked.kind != "project_git_fence"
    assert "application install" not in (blocked.detail or "")

    dash_c_denied = execute_bm_cli(
        agent,
        state,
        "git -C /projects/deny-poc checkout -b feature",
    )
    assert dash_c_denied.ok is False
    assert dash_c_denied.kind != "project_git_fence"
    assert "application install" not in (dash_c_denied.detail or "")
    assert "bound project" not in (dash_c_denied.detail or "")

    reconcile_hardened_seed_rules()
    policy_engine.reload()
    checkout_after = db.get_cli_policy_rule(checkout.id)
    custom_after = db.get_cli_policy_rule(custom.id)
    assert checkout_after is not None
    assert checkout_after.tier == "never_allowed"
    assert checkout_after.pattern == "git checkout"
    assert custom_after is not None
    assert custom_after.tier == "never_allowed"
    assert custom_after.pattern == "curl http://deny-pick.example"
