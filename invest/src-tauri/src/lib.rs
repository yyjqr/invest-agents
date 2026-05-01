use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent,
};

/// Shared handle to the Python server child process.
type PythonProcess = Arc<Mutex<Option<Child>>>;

// ---------------------------------------------------------------------------
// Alpha Vantage quote command (runs natively in Rust, no Python dependency)
// ---------------------------------------------------------------------------

/// Convert a CN A-share code to Alpha Vantage symbol.
/// Shanghai (prefix sh): CODE.SHG   Shenzhen (prefix sz): CODE.SHZ
/// Non-CN: returned as-is.
fn cn_ticker_to_av_symbol(ticker: &str) -> String {
    let code = ticker.split('.').next().unwrap_or(ticker).to_uppercase();
    if code.len() == 6 && code.chars().all(|c| c.is_ascii_digit()) {
        if code.starts_with('6') || code.starts_with('5') || code.starts_with('9') {
            return format!("{}.SHG", code);
        }
        return format!("{}.SHZ", code);
    }
    ticker.to_uppercase()
}

/// Fetch a real-time quote from Alpha Vantage GLOBAL_QUOTE endpoint.
///
/// Called directly from the frontend (before Python is fully started) or as a
/// fallback when the Python backend is temporarily unavailable.
///
/// Returns the raw `Global Quote` JSON object so the frontend / Python can
/// map fields using the same logic (`05. price`, `09. change`, etc.).
#[tauri::command]
async fn fetch_av_quote(symbol: String, api_key: String) -> Result<serde_json::Value, String> {
    if api_key.trim().is_empty() {
        return Err("Alpha Vantage API key not configured".to_string());
    }
    let av_symbol = cn_ticker_to_av_symbol(&symbol);
    let url = format!(
        "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={}&apikey={}",
        av_symbol, api_key
    );

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .get(&url)
        .header("User-Agent", "invest-app/1.0")
        .send()
        .await
        .map_err(|e| format!("AlphaVantage request failed: {e}"))?;

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("AlphaVantage JSON parse failed: {e}"))?;

    // Surface quota-exceeded / information-notice as an error
    if let Some(note) = json.get("Note").and_then(|v| v.as_str()) {
        return Err(format!("AlphaVantage quota: {note}"));
    }
    if let Some(info) = json.get("Information").and_then(|v| v.as_str()) {
        return Err(format!("AlphaVantage info: {info}"));
    }

    Ok(json.get("Global Quote").cloned().unwrap_or(json))
}
/// Locate the Python executable bundled alongside the app, or fall back to
/// system `python` / `python3`.  Always returns an absolute path when possible.
fn find_python() -> String {
    // Release bundle (NSIS/Windows): Tauri places bundled resources alongside the
    // exe.  First priority: self-contained embeddable Python at invest-app/python/.
    // This is the only portable approach — .venv relies on the host having Python
    // installed (for python3X.dll) and will not work on clean machines.
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|pp| pp.to_path_buf()))
        .unwrap_or_default();

    // Priority 1: Embeddable Python (produced by build-prod.ps1)
    //   <install_dir>\invest-app\python\python.exe
    let embed_python = exe_dir.join(r"invest-app\python\python.exe");
    if embed_python.exists() {
        if let Ok(abs) = std::fs::canonicalize(&embed_python) {
            log::info!("Using bundled embeddable Python: {}", abs.display());
            return abs.to_string_lossy().into_owned();
        }
    }

    // Priority 2: Legacy bundled venv (kept for backward compatibility only).
    //   ⚠ This will NOT work on machines that lack the matching Python installation.
    let bundled_venv = exe_dir.join(r"invest-app\.venv\Scripts\python.exe");
    if bundled_venv.exists() {
        if let Ok(abs) = std::fs::canonicalize(&bundled_venv) {
            log::info!("Using bundled venv Python (legacy): {}", abs.display());
            return abs.to_string_lossy().into_owned();
        }
    }

    // Dev venv paths — cwd is src-tauri/ when running `cargo tauri dev`
    let candidates = [
        r"..\invest-app\.venv\Scripts\python.exe",
        r"invest-app\.venv\Scripts\python.exe",
        // System fallback
        "python",
        "python3",
    ];

    for c in &candidates {
        let p = std::path::Path::new(c);
        if p.exists() {
            if let Ok(abs) = std::fs::canonicalize(p) {
                return abs.to_string_lossy().into_owned();
            }
            return c.to_string();
        }
    }

    // Last resort: just try "python" and hope it's on PATH
    "python".to_string()
}

/// Check if the server on port 8765 is healthy by hitting /health.
/// Uses a short blocking HTTP call (std only, no tokio needed here).
/// Returns true ONLY IF the server responds 200 AND the response body
/// contains the expected build marker ("build":1).  This prevents
/// reusing a stale orphan process (from a previous install) that happens
/// to have a /health endpoint but is running old code without the marker.
fn server_is_healthy() -> bool {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    // Must match the "build" value in server.py /health handler.
    // Bump both numbers together whenever you want old orphans to be evicted.
    const BUILD_MARKER: &[u8] = b"\"build\":1";

    let Ok(mut stream) = TcpStream::connect_timeout(
        &"127.0.0.1:8765".parse().unwrap(),
        Duration::from_millis(1500),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let _ = stream.write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n");
    // Read enough bytes to see both the status line and the JSON body
    let mut buf = [0u8; 256];
    if let Ok(n) = stream.read(&mut buf) {
        let data = &buf[..n];
        // Must be HTTP 2xx AND carry the current build marker
        return data.starts_with(b"HTTP/1.")
            && data.contains(&b'2')
            && data.windows(BUILD_MARKER.len()).any(|w| w == BUILD_MARKER);
    }
    false
}

/// Kill any process listening on 127.0.0.1:8765.
/// Called to evict a stale/orphaned Python process left behind when a previous
/// Tauri instance was forcefully terminated (e.g., by the NSIS installer's
/// taskkill), preventing RunEvent::Exit from firing and leaving old code running.
fn kill_server_on_8765() {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;

        // Parse `netstat -ano` to find PIDs listening on port 8765, then kill
        // them with `taskkill`.  This is more reliable than the PowerShell
        // `Get-NetTCPConnection` pipeline which silently fails on some Windows
        // configurations (restricted execution policy, non-admin user, PowerShell
        // module not loaded, etc.).  `netstat` + `taskkill` are always available
        // on Windows 7+ and do not need elevation for same-user processes.
        if let Ok(out) = std::process::Command::new("netstat")
            .args(["-ano"])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .output()
        {
            let text = String::from_utf8_lossy(&out.stdout);
            for line in text.lines() {
                // Typical line: "  TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING    12345"
                // Split into fields to avoid substring false-positives
                // (e.g. ":18765" matching ":8765").
                let fields: Vec<&str> = line.split_whitespace().collect();
                if fields.len() >= 5
                    && fields[1].ends_with(":8765")
                    && fields[3].eq_ignore_ascii_case("LISTENING")
                {
                    let pid = fields[4];
                    if pid.chars().all(|c| c.is_ascii_digit()) && pid != "0" {
                        log::info!("Killing stale server PID {pid} on :8765");
                        let _ = std::process::Command::new("taskkill")
                            .args(["/F", "/PID", pid])
                            .creation_flags(CREATE_NO_WINDOW)
                            .output();
                    }
                }
            }
        }
        // Give the OS a moment to release the port
        std::thread::sleep(std::time::Duration::from_millis(1000));
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = std::process::Command::new("fuser")
            .args(["-k", "8765/tcp"])
            .output();
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

/// Spawn the FastAPI backend.  Returns the child process handle.
fn start_python_server(python: &str) -> std::io::Result<Child> {
    // ⚠ IMPORTANT: Do NOT set current_dir to invest-app/.
    // uvicorn CLI defaults --app-dir to "" which inserts cwd at sys.path[0].
    // If cwd contains a stale invest_app/ subdirectory (left from a previous
    // NSIS install), it shadows the correct code in site-packages and the
    // server runs old code (missing build marker, missing routes → 404).
    // Using the exe directory (which never contains invest_app/) avoids this.
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|pp| pp.to_path_buf()))
        .unwrap_or_default();
    log::info!("Python server working dir: {}", exe_dir.display());

    // Write Python stderr to a log file alongside the exe so that startup
    // crashes (which would otherwise silently close the console window) can
    // be diagnosed by the user or support team.
    let log_path = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("python-server.log")))
        .unwrap_or_else(|| "python-server.log".into());

    let stderr_sink = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path);

    let mut cmd = Command::new(python);
    cmd.args(["-m", "uvicorn", "invest_app.server:app",
               "--host", "127.0.0.1",
               "--port", "8765",
               "--log-level", "warning",
               // 强制使用 h11 纯 Python HTTP 解析器：
               // httptools 的原生 .pyd 在某些安装或 AV 扫描后可能缺失，
               // 导致 ProactorEventLoop._start_serving 每次连接都抛
               // AttributeError 并在 5 秒内拒绝所有请求。
               // h11 无原生依赖，在嵌入式 Python 下 100% 可用。
               "--http", "h11"])
        .current_dir(&exe_dir)
        // Suppress uvicorn's normal access-log output (only errors matter)
        .stdout(std::process::Stdio::null());

    // Redirect stderr → log file; fall back to null if the file can't be created
    match stderr_sink {
        Ok(f) => { cmd.stderr(f); }
        Err(_) => { cmd.stderr(std::process::Stdio::null()); }
    }

    // On Windows: hide the Python console window that would otherwise flash
    // up as a black command-prompt box on every app launch.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = cmd.spawn()?;
    log::info!("Python server started (pid {}). Log: {}", child.id(), log_path.display());
    Ok(child)
}

#[tauri::command]
fn get_server_url() -> String {
    "http://127.0.0.1:8765".to_string()
}

/// Send a native OS desktop notification for a price alert.
/// Called from the frontend when a watched stock's price crosses a threshold.
#[tauri::command]
async fn trigger_price_alert(
    app: tauri::AppHandle,
    title: String,
    body: String,
) -> Result<(), String> {
    use tauri_plugin_notification::NotificationExt;
    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let python_proc: PythonProcess = Arc::new(Mutex::new(None));
    let python_proc_clone = python_proc.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .invoke_handler(tauri::generate_handler![get_server_url, fetch_av_quote, trigger_price_alert])
        .setup(move |app| {
            // ── Start Python FastAPI server ──────────────────────────────
            // Strategy: probe /health first.
            //   • Responds 200  →  server already healthy; reuse it, skip spawn.
            //     This covers the normal “app restarted quickly” case and avoids
            //     unnecessary cold-start delay.
            //   • No response or error  →  kill whatever is on port 8765
            //     (could be an orphan from a forced upgrade install) then spawn
            //     fresh.  This ensures the current bundled code is always used.
            if server_is_healthy() {
                log::info!("Server already healthy on :8765 — reusing existing process");
            } else {
                // Evict any stale/orphan process that may be holding the port
                if std::net::TcpStream::connect("127.0.0.1:8765").is_ok() {
                    log::info!("Port 8765 occupied but not healthy — evicting stale server");
                    kill_server_on_8765();

                    // Verify kill succeeded; retry if port is still occupied
                    if std::net::TcpStream::connect("127.0.0.1:8765").is_ok() {
                        log::warn!("Port 8765 still occupied after first kill — retrying");
                        kill_server_on_8765();
                        if std::net::TcpStream::connect("127.0.0.1:8765").is_ok() {
                            log::error!("Port 8765 STILL occupied after two kill attempts");
                        }
                    }
                }
                let python = find_python();
                log::info!("Python executable: {python}");
                match start_python_server(&python) {
                    Ok(mut child) => {
                        log::info!("Python server spawned (pid {})", child.id());

                        // Brief pause to catch immediate-exit failures such as
                        // "port already in use" or import errors.  2 s is
                        // enough for these — a successful uvicorn stays alive.
                        std::thread::sleep(std::time::Duration::from_millis(2000));

                        match child.try_wait() {
                            Ok(Some(status)) => {
                                log::error!(
                                    "Python server exited immediately (status: {status}). \
                                     Killing port 8765 and retrying..."
                                );
                                kill_server_on_8765();
                                match start_python_server(&python) {
                                    Ok(child2) => {
                                        log::info!(
                                            "Retry: Python server started (pid {})",
                                            child2.id()
                                        );
                                        *python_proc_clone.lock().unwrap() = Some(child2);
                                    }
                                    Err(e2) => {
                                        log::error!("Retry: failed to start Python server: {e2}");
                                    }
                                }
                            }
                            Ok(None) => {
                                // Still running — all good
                                log::info!("Python server alive after 2 s – startup OK");
                                *python_proc_clone.lock().unwrap() = Some(child);
                            }
                            Err(e) => {
                                log::warn!("Could not check Python child status: {e}");
                                *python_proc_clone.lock().unwrap() = Some(child);
                            }
                        }
                    }
                    Err(e) => {
                        log::error!("Failed to start Python server: {e}");
                    }
                }
            }

            // ── System tray ─────────────────────────────────────────────
            let open_i  = MenuItem::with_id(app, "open",  "打开主窗口", true, None::<&str>)?;
            let quit_i  = MenuItem::with_id(app, "quit",  "退出",       true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&open_i, &quit_i])?;

            TrayIconBuilder::new()
                .menu(&tray_menu)
                .tooltip("投资大师")
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            if cfg!(debug_assertions) {
                log::info!("Dev mode: React DevServer at http://127.0.0.1:5173");
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |_app, event| {
            if let RunEvent::Exit = event {
                // Kill Python server on app exit
                if let Ok(mut guard) = python_proc.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                        log::info!("Python server stopped");
                    }
                }
            }
        });
}
