"""Process-tree clean-exit regressions.

The desktop SIGTERMs the backend so FastAPI lifespan can run
``runtime_services.stop()`` (``shutdown_runtime``) before leftovers are
killed. These tests exercise that backend/worker contract without Tauri.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core.runtime.services import RuntimeServices

ROOT = Path(__file__).resolve().parent.parent
LOST_PARENT = "Runtime worker lost its parent process"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return Path(f"/proc/{pid}").exists()


def _child_pids(pid: int) -> list[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    if not path.exists():
        return []
    return [int(part) for part in path.read_text().split() if part]


def _descendants(pid: int) -> list[int]:
    found: list[int] = []
    stack = [pid]
    seen = {pid}
    while stack:
        current = stack.pop()
        for child in _child_pids(current):
            if child not in seen:
                seen.add(child)
                found.append(child)
                stack.append(child)
    return found


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _drain(stream, chunks: list[str]) -> None:
    if stream is None:
        return
    for line in iter(stream.readline, ""):
        chunks.append(line)
    stream.close()


def _wait_health(port: int, timeout: float = 15.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"backend did not become healthy on port {port}: {last_error}")


def test_run_sh_supervises_desktop_without_exec() -> None:
    text = (ROOT / "run.sh").read_text(encoding="utf-8")
    assert "exec ./" not in text
    assert "os.setpgid(0, 0)" in text
    assert "kill -TERM" in text
    assert "trap" in text
    assert "sweep_recorded_backend" in text
    assert "pkill" not in text


def test_desktop_ordered_shutdown_source_contract() -> None:
    rust = (ROOT / "desktop" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "fn stop_backend_tree(" in rust
    assert "fn take_and_stop_backend(" in rust
    assert "libc::SIGTERM" in rust
    assert "process_group(0)" in rust
    assert "shutdown_runtime" in rust
    assert "handle_quit_signal" in rust
    assert 'Command::new("pkill")' not in rust


@pytest.mark.asyncio
async def test_runtime_services_stop_joins_worker() -> None:
    services = RuntimeServices()
    await services.start()
    process = services._process
    assert process is not None
    worker_pid = process.pid
    assert _process_alive(worker_pid)
    await services.stop()
    deadline = time.time() + 3.0
    while time.time() < deadline and _process_alive(worker_pid):
        time.sleep(0.05)
    assert not _process_alive(worker_pid)


def test_worker_sigterm_exits_without_lost_parent(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["BOSSMOD_DB_PATH"] = str(tmp_path / "worker.sqlite3")
    env["BOSSMOD_APP_PID"] = str(os.getpid())
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "core.runtime.worker"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True).start()
    try:
        deadline = time.time() + 10.0
        while time.time() < deadline and "ready" not in "".join(stdout_chunks):
            time.sleep(0.05)
        assert "ready" in "".join(stdout_chunks), "".join(stderr_chunks)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        assert proc.returncode == 0
        combined = "".join(stdout_chunks) + "".join(stderr_chunks)
        assert LOST_PARENT not in combined
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_backend_sigterm_stops_worker_without_orphan(tmp_path: Path) -> None:
    port = _free_port()
    env = os.environ.copy()
    env["BOSSMOD_DB_PATH"] = str(tmp_path / "backend.sqlite3")
    env["BOSSMOD_HOST"] = "127.0.0.1"
    env["BOSSMOD_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py")],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True).start()
    worker_pids: list[int] = []
    try:
        _wait_health(port)
        worker_pids = [
            child
            for child in _descendants(proc.pid)
            if "core.runtime.worker" in Path(f"/proc/{child}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        ]
        assert worker_pids, "runtime worker was not spawned"

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=8)
        combined = "".join(stdout_chunks) + "".join(stderr_chunks)
        assert LOST_PARENT not in combined, combined

        deadline = time.time() + 2.0
        while time.time() < deadline and any(_process_alive(pid) for pid in worker_pids):
            time.sleep(0.05)
        live = [pid for pid in worker_pids if _process_alive(pid)]
        assert live == []
        assert not _process_alive(proc.pid)
    finally:
        leftovers = [proc.pid, *worker_pids, *_descendants(proc.pid)]
        for pid in leftovers:
            if _process_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
