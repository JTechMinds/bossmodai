"""Policy sees through trampolines: wrappers, ``find`` actions, env injection.

A wrapper (``env``, ``nohup``, ``timeout``, …) is replaced by the command
it runs, ``find -exec/-delete/-fprint*`` add the commands they run, and the
strictest decision wins. Assigning a program-selecting variable is never
allowed. The read-only diagnostic seeds are inserted only when missing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.effective_commands import (
    PROGRAM_SELECTING_ENV,
    effective_commands,
    unwrap_command,
)
from core.bm_cli.find_actions import has_test_predicates
from core.bm_cli.policy_engine import CommandPolicyDecision, policy_engine, strictest


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _tier(command: str) -> str:
    return policy_engine.evaluate(command, frozenset()).tier


# ---------------------------------------------------------------------------
# Unwrapping (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("env python3 -c x", ("python3 -c x",)),
        ("env -i -u HOME FOO=1 ls -la", ("ls -la",)),
        ("/usr/bin/env -- ls", ("ls",)),
        ("env -S 'bash -c id'", ("bash -c id",)),
        ("FOO=1 BAR=2 ls", ("ls",)),
        ("nohup git status", ("git status",)),
        ("timeout -k 3 --signal=TERM 5s pytest -q", ("pytest -q",)),
        ("nice -n 5 make", ("make",)),
        ("nice -10 make", ("make",)),
        ("ionice -c3 make", ("make",)),
        ("stdbuf -oL make", ("make",)),
        ("time -p make", ("make",)),
        ("nohup timeout 5 env A=1 nice ls", ("ls",)),
        ("time -o out.txt ls", ("ls", "tee out.txt")),
        ("env", ("env",)),
        ("env -i", ("env -i",)),
        ("env FOO=1", ("env FOO=1",)),
        ("timeout 5", ("timeout 5",)),
        ("ionice -p 123", ("ionice -p 123",)),
        ("ls -la", ("ls -la",)),
    ],
)
def test_wrappers_are_replaced_by_the_command_they_run(command: str, expected: tuple[str, ...]) -> None:
    assert effective_commands(command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("find . -exec rm {} \\;", ("find . -exec rm {} \\;", "rm '{}'")),
        ("find a b -execdir env bash {} +", ("find a b -execdir env bash {} +", "bash '{}'")),
        ("find . -ok mv {} x \\; -okdir ls \\;", ("find . -ok mv {} x \\; -okdir ls \\;", "mv '{}' x", "ls")),
        ("find a b -name '*.pyc' -delete", ("find a b -name '*.pyc' -delete", "rm a b")),
        ("find -delete", ("find -delete", "rm .")),
        ("find . -fprint out.txt", ("find . -fprint out.txt", "tee out.txt")),
        ("find . -fprintf out.txt %p", ("find . -fprintf out.txt %p", "tee out.txt")),
        ("find . -fls out.txt", ("find . -fls out.txt", "tee out.txt")),
        ("find -L . -name x", ("find -L . -name x",)),
    ],
)
def test_find_actions_add_the_commands_they_run(command: str, expected: tuple[str, ...]) -> None:
    assert effective_commands(command) == expected


@pytest.mark.parametrize(
    ("expression", "has_tests"),
    [
        ((), False),
        (("-delete",), False),
        (("-depth", "-maxdepth", "2", "-mindepth", "1", "-xdev", "-print0", "-delete"), False),
        (("(", "-print", "-o", "-ls", ")", "-not", "-fprintf", "f", "%p", "-delete"), False),
        (("-exec", "rm", "-name", "{}", ";"), False),
        (("-name", "*.pyc", "-delete"), True),
        (("-type", "f", "-exec", "rm", "{}", "+"), True),
        (("!", "-path", "./keep/*", "-delete"), True),
        (("-maxdepth", "1", "-newer", "ref", "-delete"), True),
    ],
)
def test_find_test_predicates(expression: tuple[str, ...], has_tests: bool) -> None:
    assert has_test_predicates(expression) is has_tests


def test_program_selecting_assignments_are_reported() -> None:
    assert PROGRAM_SELECTING_ENV >= {"PATH", "LD_PRELOAD", "GIT_SSH_COMMAND", "NODE_OPTIONS"}
    assert unwrap_command("PATH=. ls").program_env == "PATH"
    assert unwrap_command("env LD_PRELOAD=/x.so ls").program_env == "LD_PRELOAD"
    assert unwrap_command("timeout 5 env GIT_SSH_COMMAND=x git fetch").program_env == "GIT_SSH_COMMAND"
    assert unwrap_command("env -u PATH ls").program_env is None
    assert unwrap_command("env FOO=1 ls").program_env is None


def test_unknown_wrapper_options_need_approval() -> None:
    unknown = unwrap_command("timeout --frobnicate 5 ls")
    assert unknown.commands == ("timeout --frobnicate 5 ls",)
    assert "--frobnicate" in (unknown.approval_reason or "")
    chdir = unwrap_command("env -C /tmp ls")
    assert chdir.commands == ("ls",)
    assert "env -C" in (chdir.approval_reason or "")
    assert unwrap_command("echo 'unterminated").approval_reason is not None


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------


def test_env_python_is_never_allowed() -> None:
    assert _tier("env python3 -c x") == "never_allowed"
    assert _tier("env -S 'bash -c id'") == "never_allowed"
    assert _tier("nohup bash -c id") == "never_allowed"


def test_env_assignment_then_an_allowed_command_is_allowed() -> None:
    decision = policy_engine.evaluate("env FOO=1 ls", frozenset())
    assert decision.tier == "always_allowed"
    assert decision.allowed is True


def test_program_selecting_assignment_is_never_allowed() -> None:
    for command in ("PATH=. ls", "env LD_PRELOAD=/tmp/x.so ls", "nohup env NODE_OPTIONS=x ls"):
        decision = policy_engine.evaluate(command, frozenset())
        assert decision.tier == "never_allowed", command
        assert decision.allowed is False
        assert decision.approval_required is False
    message = policy_engine.evaluate("PATH=. ls", frozenset()).message or ""
    assert "setting PATH changes which program runs; run the program directly" in message


def test_find_trampolines_need_approval() -> None:
    for command in ("find . -exec rm {} \\;", "find . -delete", "find . -execdir mv {} /tmp \\;"):
        decision = policy_engine.evaluate(command, frozenset())
        assert decision.tier == "approval_required", command
        assert decision.approval_required is True
    assert _tier("find . -exec bash -c id \\;") == "never_allowed"
    assert _tier("find . -name '*.md' -exec ls {} \\;") == "always_allowed"
    assert _tier("find . -name '*.md'") == "always_allowed"


def test_timeout_wrapped_test_runner_is_allowed() -> None:
    assert _tier("timeout 5 pytest") == "always_allowed"
    assert _tier("timeout 5 rm -rf build") == "approval_required"
    assert _tier("nohup kill 1") == "approval_required"


def test_bare_env_and_token_dumps_keep_their_decisions() -> None:
    assert _tier("env") == "always_allowed"
    for command in ("env GH_TOKEN=x true", "env GH_TOKEN", "timeout 5 printenv", "nohup gh auth token"):
        assert _tier(command) == "never_allowed", command


def test_unknown_wrapper_option_needs_approval_even_for_allowed_commands() -> None:
    decision = policy_engine.evaluate("timeout --frobnicate 5 ls", frozenset())
    assert decision.tier == "approval_required"
    assert "--frobnicate" in (decision.message or "")
    assert _tier("env -C /tmp ls") == "approval_required"


def test_operator_rules_on_a_wrapper_still_apply() -> None:
    db.create_cli_policy_rule(tier="never_allowed", pattern="nohup", match_mode="prefix")
    db.create_cli_policy_rule(tier="always_allowed", pattern="timeout", match_mode="prefix")
    policy_engine.reload()

    assert _tier("nohup ls") == "never_allowed"
    # An Always on the wrapper does not decide for what it wraps.
    assert _tier("timeout 5 rm -rf build") == "approval_required"


def test_default_deny_is_stricter_than_approval() -> None:
    db.set_setting("cli_default_policy", "deny", "cli_policy")
    config.reload()
    decision = policy_engine.evaluate("find . -exec rm {} \\; -exec zz-unmatched {} \\;", frozenset())
    assert decision.allowed is False
    assert decision.approval_required is False
    assert decision.tier == "default"


def test_strictest_orders_tiers_and_prefers_a_scoped_always() -> None:
    always = CommandPolicyDecision(allowed=True, tier="always_allowed", executor="shell", matched_rule_id="g")
    scoped = CommandPolicyDecision(
        allowed=True, tier="always_allowed", executor="shell",
        matched_rule_id="s", matched_cwd_prefix="/projects/demo",
    )
    nest = CommandPolicyDecision(
        allowed=True, tier="always_allowed", executor="shell",
        matched_rule_id="n", matched_cwd_prefix="/me/host-work",
    )
    default_ask = CommandPolicyDecision(
        allowed=False, tier="default", executor="shell", approval_required=True,
    )
    rule_ask = CommandPolicyDecision(
        allowed=False, tier="approval_required", executor="shell", approval_required=True,
    )
    deny = CommandPolicyDecision(allowed=False, tier="default", executor="shell")
    never = CommandPolicyDecision(allowed=False, tier="never_allowed", executor="shell")

    assert strictest([always, scoped, nest]) is scoped
    assert strictest([always, nest]) is nest
    assert strictest([always, default_ask]) is default_ask
    assert strictest([default_ask, rule_ask]) is rule_ask
    assert strictest([rule_ask, deny]) is deny
    assert strictest([deny, never, always]) is never
    with pytest.raises(ValueError):
        strictest([])


def test_scoped_always_in_a_find_exec_is_still_contained() -> None:
    """A scoped rule matched by the exec'd command, not ``find``, is returned."""
    agent = db.create_agent("Find Clerk", role="Eng")
    rule = db.create_cli_policy_rule(
        tier="always_allowed", pattern="cp", match_mode="prefix",
        cwd_prefix="/me", agent_id=agent.id,
    )
    policy_engine.reload()

    decision = policy_engine.evaluate(
        "find /projects -exec cp {} /me \\;", frozenset(), agent.id, cwd="/me",
    )
    assert decision.tier == "always_allowed"
    assert decision.matched_rule_id == rule.id


# ---------------------------------------------------------------------------
# Read-only seeds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git ls-files src",
        "git ls-tree -r HEAD",
        "git rev-parse --show-toplevel",
        "git rev-list --count HEAD",
        "git remote -v",
        "git remote get-url origin",
        "git branch --show-current",
        "git branch --list",
        "git blame app.py",
        "git describe --tags",
        "git shortlog -sn",
        "git cat-file -p HEAD",
        "git config --get user.email",
        "git stash list",
        "git tag --list",
        "readlink -f x",
        "realpath x",
        "file x.bin",
        "stat x",
        "du -sh .",
        "df -h",
        "ps aux",
        "pgrep -fl node",
        "sleep 1",
        "printf '%s' x",
        "tree -L 2",
        "cut -d, -f1 a.csv",
        "nl a.py",
        "md5sum a",
        "sha1sum a",
        "sha256sum a",
        "jq . a.json",
        "ffprobe clip.mp4",
        "unzip -l a.zip",
        "id",
        "hostname",
        "nproc",
        "uptime",
    ],
)
def test_read_only_seeds_match(command: str) -> None:
    decision = policy_engine.evaluate(command, frozenset())
    assert decision.tier == "always_allowed", command
    assert decision.matched_rule_id is not None


@pytest.mark.parametrize(
    "command",
    [
        "awk '{print $1}' a",
        "sed -n 1p a",
        "git remote add origin x",
        "git branch -D x",
        "tar -xf a.tar",
        "tar -tf a.tar",
        "tar -tzf a.tar.gz",
        "tar -tf a.tar -I 'python3 -c x'",
    ],
)
def test_executing_or_writing_tools_are_not_seeded(command: str) -> None:
    assert _tier(command) != "always_allowed", command


def test_tar_listing_is_not_seeded() -> None:
    patterns = {rule.pattern for rule in db.list_cli_policy_rules()}
    assert "tar -tf" not in patterns
    assert "tar -tzf" not in patterns


def _global_rules(pattern: str):
    return [rule for rule in db.list_cli_policy_rules() if rule.pattern == pattern and rule.agent_id is None]


def test_reconcile_keeps_an_operator_edited_seed_and_restores_a_missing_one() -> None:
    (jq,) = _global_rules("jq")
    db.update_cli_policy_rule(jq.id, tier="approval_required", description="Operator: ask for jq")
    (readlink,) = _global_rules("readlink")
    assert db.delete_cli_policy_rule(readlink.id) is True

    inserted = db.reconcile_hardened_cli_policy_rules()
    policy_engine.reload()

    assert inserted == 1
    assert [(rule.tier, rule.description) for rule in _global_rules("jq")] == [
        ("approval_required", "Operator: ask for jq"),
    ]
    assert _tier("jq . a.json") == "approval_required"
    assert _tier("readlink -f x") == "always_allowed"
    assert db.reconcile_hardened_cli_policy_rules() == 0
