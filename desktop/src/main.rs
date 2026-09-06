// BossMod AI — Tauri desktop shell.
//
// Spawns the FastAPI backend as a child process, waits for it
// to become ready, then opens the native app window pointing
// at the configured local backend port.
//
// Quit (window close or SIGINT/SIGTERM) uses ordered teardown:
// SIGTERM the backend so FastAPI lifespan can run runtime_services.stop()
// (`shutdown_runtime`), wait briefly, then kill only leftover processes
// in that backend tree. The backend is spawned in its own process group
// so a terminal interrupt is not delivered to the worker as a surprise.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::Manager;

static QUIT_REQUESTED: AtomicBool = AtomicBool::new(false);

extern "C" fn handle_quit_signal(_: libc::c_int) {
    QUIT_REQUESTED.store(true, Ordering::SeqCst);
}

fn install_quit_signals() {
    unsafe {
        libc::signal(libc::SIGINT, handle_quit_signal as libc::sighandler_t);
        libc::signal(libc::SIGTERM, handle_quit_signal as libc::sighandler_t);
    }
}

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "38471";
const BACKEND_STOP_GRACE: Duration = Duration::from_millis(3500);
const LEFTOVER_TERM_GRACE: Duration = Duration::from_millis(400);

struct BackendProcess(Mutex<Option<Child>>);

fn backend_pid_path(project_root: &Path) -> PathBuf {
    project_root.join(".bossmod-backend.pid")
}

fn is_our_backend_cmdline(cmdline: &str, project_root: &Path) -> bool {
    let main_py = project_root.join("main.py");
    cmdline.contains(main_py.to_string_lossy().as_ref())
}

fn is_our_worker_cmdline(cmdline: &str) -> bool {
    cmdline.contains("core.runtime.worker")
}

fn read_cmdline(pid: u32) -> Option<String> {
    let bytes = std::fs::read(format!("/proc/{}/cmdline", pid)).ok()?;
    Some(String::from_utf8_lossy(&bytes).into_owned())
}

fn is_recorded_backend(pid: u32, project_root: &Path) -> bool {
    read_cmdline(pid).is_some_and(|cmd| is_our_backend_cmdline(&cmd, project_root))
}

fn is_our_tree_process(pid: u32, project_root: &Path) -> bool {
    match read_cmdline(pid) {
        Some(cmd) => is_our_backend_cmdline(&cmd, project_root) || is_our_worker_cmdline(&cmd),
        None => false,
    }
}

fn process_alive(pid: u32) -> bool {
    Path::new(&format!("/proc/{}", pid)).exists()
}

fn collect_descendants(pid: u32) -> Vec<u32> {
    let mut out = Vec::new();
    let mut stack = vec![pid];
    let mut seen = HashSet::new();
    while let Some(current) = stack.pop() {
        if !seen.insert(current) {
            continue;
        }
        let path = format!("/proc/{}/task/{}/children", current, current);
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        for part in text.split_whitespace() {
            if let Ok(child) = part.parse::<u32>() {
                out.push(child);
                stack.push(child);
            }
        }
    }
    out
}

fn pids_in_process_group(pgid: u32) -> Vec<u32> {
    let mut pids = Vec::new();
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return pids;
    };
    for entry in entries.flatten() {
        let Ok(pid) = entry.file_name().to_string_lossy().parse::<u32>() else {
            continue;
        };
        let pg = unsafe { libc::getpgid(pid as i32) };
        if pg == pgid as i32 {
            pids.push(pid);
        }
    }
    pids
}

fn send_signal(pid: i32, sig: i32) {
    unsafe {
        libc::kill(pid, sig);
    }
}

fn wait_until(timeout: Duration, mut done: impl FnMut() -> bool) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if done() {
            return true;
        }
        thread::sleep(Duration::from_millis(50));
    }
    done()
}

fn wait_for_child(child: &mut Child, timeout: Duration) -> bool {
    wait_until(timeout, || matches!(child.try_wait(), Ok(Some(_))))
}

fn wait_for_pid_exit(pid: u32, timeout: Duration) -> bool {
    wait_until(timeout, || !process_alive(pid))
}

fn kill_leftovers(pids: &[u32], project_root: &Path) {
    let live: Vec<u32> = pids
        .iter()
        .copied()
        .filter(|pid| process_alive(*pid) && is_our_tree_process(*pid, project_root))
        .collect();
    for pid in &live {
        send_signal(*pid as i32, libc::SIGTERM);
    }
    wait_until(LEFTOVER_TERM_GRACE, || {
        live.iter().all(|pid| !process_alive(*pid))
    });
    for pid in &live {
        if process_alive(*pid) && is_our_tree_process(*pid, project_root) {
            send_signal(*pid as i32, libc::SIGKILL);
        }
    }
}

fn collect_tree_pids(root_pid: u32, project_root: &Path) -> Vec<u32> {
    let mut leftovers = collect_descendants(root_pid);
    leftovers.push(root_pid);
    for member in pids_in_process_group(root_pid) {
        if is_our_tree_process(member, project_root) {
            leftovers.push(member);
        }
    }
    leftovers.sort_unstable();
    leftovers.dedup();
    leftovers
}

fn stop_backend_tree(child: &mut Child, project_root: &Path) {
    let pid = child.id();
    let leftovers = collect_tree_pids(pid, project_root);
    println!("[BossMod] Shutting down backend");
    // SIGTERM the backend only so uvicorn runs lifespan → shutdown_runtime.
    send_signal(pid as i32, libc::SIGTERM);
    if !wait_for_child(child, BACKEND_STOP_GRACE) {
        send_signal(pid as i32, libc::SIGKILL);
        let _ = child.wait();
    }
    kill_leftovers(&leftovers, project_root);
}

fn stop_pid_tree(pid: u32, project_root: &Path) {
    if !is_our_tree_process(pid, project_root) && !is_recorded_backend(pid, project_root) {
        return;
    }
    let leftovers = collect_tree_pids(pid, project_root);
    send_signal(pid as i32, libc::SIGTERM);
    wait_for_pid_exit(pid, BACKEND_STOP_GRACE);
    if process_alive(pid) && is_our_tree_process(pid, project_root) {
        send_signal(pid as i32, libc::SIGKILL);
        wait_for_pid_exit(pid, LEFTOVER_TERM_GRACE);
    }
    kill_leftovers(&leftovers, project_root);
}

fn stop_recorded_backend(project_root: &Path) {
    // HA-OPS-P2-02: signal only the PID we recorded last launch (and its
    // known tree). Do not pkill -f — that can match unrelated python …/main.py.
    let pid_path = backend_pid_path(project_root);
    let Ok(contents) = std::fs::read_to_string(&pid_path) else {
        return;
    };
    let Ok(pid) = contents.trim().parse::<u32>() else {
        let _ = std::fs::remove_file(&pid_path);
        return;
    };
    if is_recorded_backend(pid, project_root) || is_our_tree_process(pid, project_root) {
        stop_pid_tree(pid, project_root);
    } else {
        // Backend PID is gone; reap anyone still in the process group we created
        // (process_group(0) → pgid == backend pid) if they still look like ours.
        let leftovers: Vec<u32> = pids_in_process_group(pid)
            .into_iter()
            .filter(|member| is_our_tree_process(*member, project_root))
            .collect();
        kill_leftovers(&leftovers, project_root);
    }
    let _ = std::fs::remove_file(&pid_path);
}

fn take_and_stop_backend(state: &BackendProcess, project_root: &Path) {
    let mut guard = state.0.lock().unwrap();
    if let Some(mut child) = guard.take() {
        stop_backend_tree(&mut child, project_root);
    }
    drop(guard);
    let _ = std::fs::remove_file(backend_pid_path(project_root));
}

fn find_project_root() -> PathBuf {
    // Project root is one level up from the desktop/ directory
    std::env::current_exe()
        .ok()
        .and_then(|exe| {
            exe.ancestors()
                .find(|p| p.join("main.py").exists())
                .map(|p| p.to_path_buf())
        })
        .unwrap_or_else(|| {
            // Fallback: try cwd parent (when run via `cargo run` from desktop/)
            std::env::current_dir()
                .expect("Cannot determine current directory")
                .parent()
                .expect("Cannot determine project root")
                .to_path_buf()
        })
}

fn start_backend() -> Child {
    let project_root = find_project_root();

    let venv_python = project_root.join(".venv/bin/python");
    let python = if venv_python.exists() {
        venv_python.to_string_lossy().to_string()
    } else {
        "python3".to_string()
    };

    let main_py = project_root.join("main.py");

    stop_recorded_backend(&project_root);

    let mut cmd = Command::new(&python);
    cmd.arg(main_py.to_string_lossy().as_ref())
        .env("BOSSMOD_HOST", BACKEND_HOST)
        .env("BOSSMOD_PORT", BACKEND_PORT)
        .current_dir(&project_root);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }

    let child = cmd
        .spawn()
        .unwrap_or_else(|e| panic!("Failed to start backend with {}: {}", python, e));

    let _ = std::fs::write(backend_pid_path(&project_root), child.id().to_string());
    child
}

fn wait_for_backend(url: &str, timeout_secs: u64) -> bool {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(timeout_secs) {
        if let Ok(resp) = ureq::get(&format!("{}/health", url)).call() {
            if resp.status() == 200 {
                return true;
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

fn main() {
    // Start the FastAPI backend
    let backend = start_backend();
    println!("[BossMod] Backend started (PID: {})", backend.id());

    let backend_state = BackendProcess(Mutex::new(Some(backend)));

    // Wait for backend to be ready
    let url = &format!("http://{}:{}", BACKEND_HOST, BACKEND_PORT);
    if !wait_for_backend(url, 15) {
        eprintln!("[BossMod] Backend failed to start within 15 seconds");
        take_and_stop_backend(&backend_state, &find_project_root());
        std::process::exit(1);
    }
    println!("[BossMod] Backend ready");

    tauri::Builder::default()
        .manage(backend_state)
        .setup(|app| {
            install_quit_signals();
            let handle = app.handle().clone();
            thread::spawn(move || {
                while !QUIT_REQUESTED.load(Ordering::SeqCst) {
                    thread::sleep(Duration::from_millis(50));
                }
                handle.exit(0);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state: tauri::State<BackendProcess> = window.state();
                take_and_stop_backend(&state, &find_project_root());
            }
        })
        .build(tauri::generate_context!())
        .expect("Error running BossMod")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<BackendProcess>() {
                    take_and_stop_backend(&state, &find_project_root());
                }
            }
        });
}
