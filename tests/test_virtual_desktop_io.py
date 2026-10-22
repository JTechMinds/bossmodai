"""Virtual Desktop file copies and shell commands: vm run, launch, upload, download (no Docker)."""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.host_roots import PathOutsideRootsError
from core.bm_cli.virtual_fs import resolve_cli_path
from core.extensions.contract import ExtensionContext
from tests._virtual_desktop_fakes import ENTRY, PACKAGE, FakeHost, cli_runner, module, types

commands_io = module("commands_io")
archive = module("archive")


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


@pytest.fixture()
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ready.json").write_text(json.dumps({"image_version": "1"}), encoding="utf-8")
    host = FakeHost()
    agent = db.create_agent("Filer", role="Tester")
    configs = {agent.id: {}}
    extension = PACKAGE.VirtualDesktopExtension(
        ExtensionContext(manifest=ENTRY.manifest, data_dir=data_dir, read_agent_config=configs.get),
        host=host,
        vision_model=lambda agent: ("vision-model", True),
        sleep=lambda seconds: None,
    )
    me = resolve_cli_path(agent.storage_key, "/me", "/me").real_path
    me.mkdir(parents=True, exist_ok=True)
    return {"run": cli_runner(extension, agent), "host": host, "me": me}


def _error(result) -> str:
    assert not result.ok, result.prompt_content
    return result.data["error"]


def _tar(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name), data


def _entry(name: str, kind: bytes) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
        info.linkname = "/etc/passwd"
    return info, None


# ── vm run ───────────────────────────────────────────────────────────────


def test_run_returns_the_exit_code_and_both_streams(env) -> None:
    env["host"].run_outcome = types.RunOutcome(exit_code=0, stdout=b"Python 3.13.5\n", stderr=b"a warning\n", stdout_size=14, stderr_size=10)
    result = env["run"]('vm run "python --version && node --version"')
    assert result.ok and result.exit_code == 0
    assert ("run", "python --version && node --version", 120) in env["host"].calls
    text = result.prompt_content
    assert "exit code: 0" in text and "STDOUT:\nPython 3.13.5" in text and "STDERR:\na warning" in text
    assert not result.image_paths


def test_run_with_a_failing_exit_is_not_ok(env) -> None:
    env["host"].run_outcome = types.RunOutcome(exit_code=100, stdout=b"", stderr=b"E: Unable to locate package cowsay\n", stdout_size=0, stderr_size=35)
    result = env["run"]('vm run "sudo apt-get install -y cowsay"')
    assert not result.ok and result.exit_code == 100
    assert "exit code: 100" in result.prompt_content


def test_run_says_what_to_do_when_its_time_limit_stops_the_command(env) -> None:
    env["host"].run_outcome = types.RunOutcome(exit_code=124, stdout=b"", stderr=b"", stdout_size=0, stderr_size=0)
    result = env["run"]('vm run "sleep 999"')
    assert "timed out after 120s; start programs that keep running with vm launch" in result.prompt_content


def test_run_marks_output_longer_than_it_returned_with_cores_marker(env) -> None:
    env["host"].run_outcome = types.RunOutcome(exit_code=0, stdout=b"x" * 20_000, stderr=b"e", stdout_size=20_050, stderr_size=1)
    text = env["run"]('vm run "yes x"').prompt_content
    assert "x" * 20_000 + "\n[truncated — 20050 bytes total]" in text
    assert "STDERR:\ne" in text and "1 bytes total" not in text


def test_run_with_a_trailing_ampersand_is_passed_on_whole(env) -> None:
    result = env["run"]('vm run "python -m http.server 8000 > ~/srv.log 2>&1 &"')
    assert result.ok
    assert ("run", "python -m http.server 8000 > ~/srv.log 2>&1 &", 120) in env["host"].calls


def test_the_prompt_and_help_say_how_to_leave_something_running() -> None:
    advice = 'vm run "npm run dev > ~/dev.log 2>&1 &"'
    assert advice in (Path(PACKAGE.__file__).with_name("prompt.md")).read_text(encoding="utf-8")
    assert advice in ENTRY.manifest.command.help


def test_run_takes_one_quoted_command_several_words_or_the_body(env) -> None:
    env["run"]('vm run "ls -la && pwd"')
    env["run"]("vm run python -c 'print(1)'")
    env["run"]("vm run", body="make test\n")
    commands = [call[1] for call in env["host"].calls if call[0] == "run"]
    assert commands == ["ls -la && pwd", "python -c 'print(1)'", "make test"]
    assert _error(env["run"]("vm run ls", body="pwd")).startswith("USAGE: vm run: give the command either")
    assert _error(env["run"]("vm run")).startswith("USAGE: vm run <command>")


# ── vm launch ────────────────────────────────────────────────────────────


def test_launch_needs_a_command(env) -> None:
    assert _error(env["run"]("vm launch")).startswith("USAGE: vm launch <command>")
    assert "launch" not in env["host"].names()


# ── vm upload ────────────────────────────────────────────────────────────


def test_upload_copies_a_file_to_the_desktop_owned_by_agent(env) -> None:
    (env["me"] / "report.md").write_text("# Report\n", encoding="utf-8")
    result = env["run"]("vm upload /me/report.md")
    assert result.ok, result.prompt_content
    assert "uploaded: /me/report.md → /home/agent/Desktop/report.md (9 bytes)" in result.prompt_content
    dest, data = env["host"].uploads[0]
    assert dest == "/home/agent/Desktop"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = tar.getmember("report.md")
        assert (member.uid, member.gid, member.uname, member.gname) == (1000, 1000, "agent", "agent")
        assert tar.extractfile(member).read() == b"# Report\n"


def test_upload_copies_a_folder_into_a_named_desktop_folder(env) -> None:
    folder = env["me"] / "project"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (folder / "b.txt").write_text("b\n", encoding="utf-8")
    result = env["run"]("vm upload /me/project ~/work")
    assert "uploaded: /me/project → /home/agent/work/project (2 files, 11 bytes)" in result.prompt_content
    dest, data = env["host"].uploads[0]
    assert dest == "/home/agent/work"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        assert sorted(tar.getnames()) == ["project", "project/b.txt", "project/src", "project/src/a.py"]
        assert {(m.uid, m.gid) for m in tar.getmembers()} == {(1000, 1000)}


def test_upload_refusals(env) -> None:
    assert _error(env["run"]("vm upload /me/nothing.txt")) == "NOT_FOUND: /me/nothing.txt does not exist"
    assert _error(env["run"]("vm upload /")).startswith("INVALID_ARGUMENT: / is not a file or folder")
    (env["me"] / "x.txt").write_text("x", encoding="utf-8")
    env["host"].missing.add("/home/agent/Nope")
    assert _error(env["run"]("vm upload /me/x.txt Nope")) == "NOT_FOUND: /home/agent/Nope does not exist on the desktop"
    assert _error(env["run"]("vm upload")).startswith("USAGE: vm upload")


# ── vm download ──────────────────────────────────────────────────────────


def test_download_writes_into_me_downloads_under_a_unique_name(env) -> None:
    env["host"].archive = _tar([_file("data.csv", b"a,b\n1,2\n")])
    first = env["run"]("vm download ~/out/data.csv")
    second = env["run"]("vm download ~/out/data.csv")
    assert ("get_archive", "/home/agent/out/data.csv") in env["host"].calls
    assert "downloaded: /me/downloads/data.csv (8 bytes)" in first.prompt_content
    assert "downloaded: /me/downloads/data (1).csv (8 bytes)" in second.prompt_content
    assert first.data["status_lines"] == ["Copied data.csv to /me/downloads"]
    assert (env["me"] / "downloads" / "data (1).csv").read_bytes() == b"a,b\n1,2\n"


def test_download_of_a_folder_into_a_chosen_folder(env) -> None:
    folder = tarfile.TarInfo("out")
    folder.type = tarfile.DIRTYPE
    env["host"].archive = _tar([(folder, None), _file("out/a.txt", b"alpha"), _file("out/sub/b.txt", b"beta")])
    result = env["run"]("vm download out /me/results")
    assert "downloaded: /me/results/out (9 bytes)" in result.prompt_content
    assert (env["me"] / "results" / "out" / "sub" / "b.txt").read_bytes() == b"beta"


@pytest.mark.parametrize(("members", "reason"), [
    ([_entry("out/link", tarfile.SYMTYPE)], "out/link is a symbolic link"),
    ([_entry("out/hard", tarfile.LNKTYPE)], "out/hard is a hard link"),
    ([_entry("out/dev", tarfile.CHRTYPE)], "out/dev is a device or special file"),
    ([_entry("out/fifo", tarfile.FIFOTYPE)], "out/fifo is a device or special file"),
    ([_file("/etc/cron.d/x", b"x")], "is not a relative name"),
    ([_file("out/../../escape.txt", b"x")], "leaves its folder"),
    ([_file("out/a.txt", b"a"), _file("other/b.txt", b"b")], "other/b.txt is outside out"),
    ([_file("out/a.txt", b"a"), _file("out/a.txt", b"b")], "out/a.txt appears twice"),
    ([], "the copy from the desktop was empty"),
])
def test_download_refuses_anything_but_regular_files_and_folders(env, members, reason: str) -> None:
    env["host"].archive = _tar(members)
    message = _error(env["run"]("vm download ~/out"))
    assert message.startswith("UNSAFE_ARCHIVE:") and reason in message
    assert not (env["me"] / "downloads").exists() or not any((env["me"] / "downloads").iterdir())


def test_download_refuses_an_unreadable_archive(env) -> None:
    env["host"].archive = b"this is not a tar archive" * 40
    assert _error(env["run"]("vm download ~/out")).startswith("UNSAFE_ARCHIVE: the copy from the desktop is not a readable archive")


def test_download_refusals(env) -> None:
    env["host"].missing.add("/home/agent/nope.txt")
    assert _error(env["run"]("vm download nope.txt")) == "NOT_FOUND: /home/agent/nope.txt does not exist on the desktop"
    assert _error(env["run"]("vm download ~/x /")).startswith("INVALID_ARGUMENT: / is not a file or folder")
    (env["me"] / "afile").write_text("x", encoding="utf-8")
    assert _error(env["run"]("vm download ~/x /me/afile")) == "INVALID_ARGUMENT: /me/afile is a file, not a folder"


@pytest.mark.parametrize(("raw", "outside"), [
    ("vm upload /etc/passwd", "/etc/passwd"),
    ("vm download ~/out /etc", "/etc"),
    ("vm download ~/out /me/../../etc", "/etc"),
])
def test_a_path_outside_the_agents_roots_goes_to_cores_consent_flow_before_anything_happens(env, raw: str, outside: str) -> None:
    with pytest.raises(PathOutsideRootsError) as raised:
        env["run"](raw)
    assert raised.value.raw_path == outside
    assert env["host"].calls == []


def test_a_host_folder_the_boss_allowed_is_used_like_me(env, tmp_path: Path) -> None:
    allowed = tmp_path / "host-folder"
    allowed.mkdir()
    (allowed / "notes.txt").write_text("from the host\n", encoding="utf-8")
    db.set_setting("workspace_host_roots", str(allowed.resolve()), "cli_policy")
    upload = env["run"](f"vm upload {allowed / 'notes.txt'}")
    assert upload.ok, upload.prompt_content
    assert env["host"].uploads[0][0] == "/home/agent/Desktop"
    env["host"].archive = _tar([_file("result.csv", b"1,2\n")])
    download = env["run"](f"vm download ~/result.csv {allowed}")
    assert download.ok, download.prompt_content
    assert (allowed / "result.csv").read_bytes() == b"1,2\n"


def test_a_failed_write_removes_what_this_copy_wrote(tmp_path: Path) -> None:
    data = _tar([_file("out/a.txt", b"alpha"), _file("out/b.txt", b"beta")])
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = archive.checked_members(tar.getmembers())
        target = tmp_path / "out"
        (target / "b.txt").mkdir(parents=True)  # makes the second write fail
        with pytest.raises(OSError):
            archive.extract(tar, members, target)
    assert not target.exists()


def test_unique_names_and_desktop_paths() -> None:
    assert commands_io.desktop_path("~") == "/home/agent"
    assert commands_io.desktop_path("~/Downloads/x.zip") == "/home/agent/Downloads/x.zip"
    assert commands_io.desktop_path("notes/a.md") == "/home/agent/notes/a.md"
    assert commands_io.desktop_path("/tmp/../etc/hosts") == "/etc/hosts"
    with pytest.raises(ValueError):
        commands_io.desktop_path("  ")
