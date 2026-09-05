// BossMod AI — Tauri desktop shell.
//
// Spawns the FastAPI backend as a child process, waits for it
// to become ready, then opens the native app window pointing
// at the configured local backend port. Cleans up the backend on exit.

use std::process::{Child, Command};
use std::path::Path;
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::Manager;

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "38471";

struct BackendProcess(Mutex<Option<Child>>);

fn backend_pid_path(project_root: &Path) -> std::path::PathBuf {
    project_root.join(".bossmod-backend.pid")
}

fn is_recorded_backend(pid: u32, project_root: &Path) -> bool {
    let cmdline_path = format!("/proc/{}/cmdline", pid);
    let Ok(bytes) = std::fs::read(&cmdline_path) else {
        return false;
    };
    let cmdline = String::from_utf8_lossy(&bytes);
    let main_py = project_root.join("main.py");
    cmdline.contains(main_py.to_string_lossy().as_ref())
}

fn stop_recorded_backend(project_root: &Path) {
    // HA-OPS-P2-02: signal only the PID we recorded last launch.
    // Do not pkill -f — that can match unrelated python …/main.py processes.
    let pid_path = backend_pid_path(project_root);
    let Ok(contents) = std::fs::read_to_string(&pid_path) else {
        return;
    };
    let Ok(pid) = contents.trim().parse::<u32>() else {
        let _ = std::fs::remove_file(&pid_path);
        return;
    };
    if is_recorded_backend(pid, project_root) {
        let _ = Command::new("kill")
            .arg(pid.to_string())
            .status();
        thread::sleep(Duration::from_millis(300));
    }
    let _ = std::fs::remove_file(&pid_path);
}

fn find_project_root() -> std::path::PathBuf {
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

    let child = Command::new(&python)
        .arg(main_py.to_string_lossy().as_ref())
        .env("BOSSMOD_HOST", BACKEND_HOST)
        .env("BOSSMOD_PORT", BACKEND_PORT)
        .current_dir(&project_root)
        .spawn()
        .unwrap_or_else(|e| panic!("Failed to start backend with {}: {}", python, e));

    let _ = std::fs::write(backend_pid_path(&project_root), child.id().to_string());
    child
}

fn wait_for_backend(url: &str, timeout_secs: u64) -> bool {
    let start = std::time::Instant::now();
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
        if let Some(mut child) = backend_state.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        std::process::exit(1);
    }
    println!("[BossMod] Backend ready");

    // Run Tauri app
    tauri::Builder::default()
        .manage(backend_state)
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Kill backend when window closes
                let state: tauri::State<BackendProcess> = window.state();
                let mut guard = state.0.lock().unwrap();
                if let Some(mut child) = guard.take() {
                    println!("[BossMod] Shutting down backend");
                    let _ = child.kill();
                    let _ = child.wait();
                }
                let _ = std::fs::remove_file(backend_pid_path(&find_project_root()));
                drop(guard);
            }
        })
        .run(tauri::generate_context!())
        .expect("Error running BossMod");
}
