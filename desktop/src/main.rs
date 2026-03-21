// BossMod AI — Tauri desktop shell.
//
// Spawns the FastAPI backend as a child process, waits for it
// to become ready, then opens the native app window pointing
// at localhost:8000. Cleans up the backend on exit.

use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::Manager;

struct BackendProcess(Mutex<Option<Child>>);

fn start_backend() -> Child {
    // Project root is one level up from the desktop/ directory
    let project_root = std::env::current_exe()
        .expect("Cannot determine executable path")
        .ancestors()  // walk up from target/release/bossmod-desktop
        .find(|p| p.join("main.py").exists())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| {
            // Fallback: try cwd parent (when run via `cargo run` from desktop/)
            std::env::current_dir()
                .expect("Cannot determine current directory")
                .parent()
                .expect("Cannot determine project root")
                .to_path_buf()
        });

    let venv_python = project_root.join(".venv/bin/python");
    let python = if venv_python.exists() {
        venv_python.to_string_lossy().to_string()
    } else {
        "python3".to_string()
    };

    let main_py = project_root.join("main.py");

    Command::new(&python)
        .arg(main_py.to_string_lossy().as_ref())
        .current_dir(&project_root)
        .spawn()
        .unwrap_or_else(|e| panic!("Failed to start backend with {}: {}", python, e))
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
    let url = "http://127.0.0.1:8000";
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
                drop(guard);
            }
        })
        .run(tauri::generate_context!())
        .expect("Error running BossMod");
}
