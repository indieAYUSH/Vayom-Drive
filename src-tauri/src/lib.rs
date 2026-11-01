use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{Manager, RunEvent};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

// CREATE_NO_WINDOW: backend spawns must not flash a console window over the
// desktop app UI on Windows.
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct BackendProcess(Arc<Mutex<Option<Child>>>);

const HEALTH_URL: &str = "http://127.0.0.1:8000/api/v1/health";

/// Probes the local backend health endpoint.
///
/// C-3: the response body is checked for the "Vayom Drive" application marker so
/// that an unrelated local service listening on port 8000 is never adopted as
/// the backend (the frontend hard-codes 127.0.0.1:8000, so adopting a foreign
/// server would show garbage data in the vault).
fn backend_health_ok(timeout: Duration) -> bool {
    match ureq::get(HEALTH_URL).timeout(timeout).call() {
        Ok(resp) if resp.status() == 200 => resp
            .into_string()
            .map(|body| body.contains("Vayom Drive"))
            .unwrap_or(false),
        _ => false,
    }
}

fn spawn_backend() -> Option<Child> {
    // Check if backend is already running on port 8000. A backend from a prior
    // app run may still be shutting down via its parent watchdog, so re-check
    // before adopting it. Otherwise a quick app restart could bind the UI to a
    // server that exits a second later.
    if backend_health_ok(Duration::from_millis(1500)) {
        println!("Backend already running; checking whether it is stale...");
        thread::sleep(Duration::from_millis(2200));
        if backend_health_ok(Duration::from_millis(800)) {
            println!("Vayom Drive backend already running on port 8000.");
            return None;
        }
        println!("Stale backend exited; starting a fresh one.");
    }

    println!("Starting Vayom Drive backend...");

    // 1. Try bundled / compiled standalone binary (PyInstaller vayom-backend)
    let mut standalone_candidates = Vec::new();

    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(exe_dir) = current_exe.parent() {
            #[cfg(target_os = "windows")]
            {
                standalone_candidates.push(exe_dir.join("vayom-backend.exe"));
                standalone_candidates.push(exe_dir.join("binaries").join("vayom-backend.exe"));
                standalone_candidates.push(exe_dir.join("binaries").join("vayom-backend-x86_64-pc-windows-msvc.exe"));
            }
            #[cfg(not(target_os = "windows"))]
            {
                standalone_candidates.push(exe_dir.join("vayom-backend"));
                standalone_candidates.push(exe_dir.join("binaries").join("vayom-backend"));
                standalone_candidates.push(exe_dir.join("binaries").join("vayom-backend-x86_64-unknown-linux-gnu"));
            }
        }
    }

    #[cfg(target_os = "windows")]
    {
        standalone_candidates.push(std::path::PathBuf::from("src-tauri/binaries/vayom-backend-x86_64-pc-windows-msvc.exe"));
        standalone_candidates.push(std::path::PathBuf::from("binaries/vayom-backend.exe"));
        standalone_candidates.push(std::path::PathBuf::from("vayom-backend.exe"));
    }
    #[cfg(not(target_os = "windows"))]
    {
        standalone_candidates.push(std::path::PathBuf::from("src-tauri/binaries/vayom-backend-x86_64-unknown-linux-gnu"));
        standalone_candidates.push(std::path::PathBuf::from("binaries/vayom-backend"));
        standalone_candidates.push(std::path::PathBuf::from("vayom-backend"));
    }

    for bin_path in &standalone_candidates {
        if bin_path.exists() {
            let mut cmd = Command::new(bin_path);
            // The PyInstaller one-file launcher has a child that owns the
            // HTTP server. Pass the app PID so the child can shut itself
            // down even if the launcher is killed or the app crashes.
            cmd.env("VAYOM_PARENT_PID", std::process::id().to_string());
            #[cfg(target_os = "windows")]
            cmd.creation_flags(CREATE_NO_WINDOW);
            let child = cmd.spawn();
            if let Ok(c) = child {
                println!("Successfully spawned standalone backend sidecar: {:?}", bin_path);
                return Some(c);
            }
        }
    }

    // 2. Fallback to developer python environment
    #[cfg(target_os = "windows")]
    let python_candidates = [
        ".venv\\Scripts\\python.exe",
        "python.exe",
        "py.exe"
    ];

    #[cfg(not(target_os = "windows"))]
    let python_candidates = [
        ".venv/bin/python",
        "/usr/bin/python3",
        "python3",
        "python"
    ];

    for python_bin in python_candidates {
        let mut cmd = Command::new(python_bin);
        cmd.env("VAYOM_PARENT_PID", std::process::id().to_string())
            .args(["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"]);
        #[cfg(target_os = "windows")]
        cmd.creation_flags(CREATE_NO_WINDOW);
        let child = cmd.spawn();

        if let Ok(c) = child {
            println!("Successfully spawned backend with {}", python_bin);
            return Some(c);
        }
    }

    eprintln!("Warning: Could not automatically spawn backend sidecar or python backend. Ensure backend is running on port 8000.");
    None
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend_child = Arc::new(Mutex::new(None));
    let backend_holder = BackendProcess(backend_child.clone());

    let app = tauri::Builder::default()
        .manage(backend_holder)
        .setup(move |_app| {
            let child = spawn_backend();
            if let Ok(mut lock) = backend_child.lock() {
                *lock = child;
            }

            // Wait briefly for backend health. The timeout on the probe matters:
            // without it a service that accepts the TCP connection but never
            // answers would block this thread forever.
            thread::spawn(|| {
                for _ in 0..30 {
                    thread::sleep(Duration::from_millis(300));
                    if backend_health_ok(Duration::from_millis(500)) {
                        println!("Backend health verified.");
                        break;
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(move |_app_handle, event| {
        if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
            // Cleanly kill spawned backend process on desktop exit
            if let Some(state) = _app_handle.try_state::<BackendProcess>() {
                if let Ok(mut lock) = state.0.lock() {
                    if let Some(mut child) = lock.take() {
                        println!("Shutting down Vayom Drive backend process...");
                        let _ = child.kill();
                    }
                }
            }
        }
    });
}
