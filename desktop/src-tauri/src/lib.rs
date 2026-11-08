//! Stenny's app: a Dock app with a menu bar icon (menubar.rs) and its windows (the library, "Record a talk"). The windows'
//! interface is the web code in desktop/src; everything about lectures comes from the engine
//! (`stenny app-server`, see engine.rs).
//!
//! `--library` runs the library window alone, without the menu bar icon: for trying a build next to the
//! installed Stenny, which keeps its own icon (one Stenny in the menu bar, never two).

mod engine;
#[cfg(target_os = "macos")]
mod macos;
mod menubar;
#[cfg(windows)]
mod windows_share;

use engine::Engine;
use menubar::MenuBar;
use serde_json::Value;
use std::sync::Arc;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

/// One request to the engine, e.g. `engine("library")`. Runs off the main thread: a request can take a moment.
#[tauri::command]
async fn engine(state: tauri::State<'_, Arc<Engine>>, method: String, params: Option<Value>) -> Result<Value, String> {
    let engine = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || engine.call(&method, params.unwrap_or(Value::Null)))
        .await
        .map_err(|e| e.to_string())?
}

/// Opens a transcript in Obsidian when it's installed (the vault is an Obsidian vault), else in the default app.
#[tauri::command]
fn open_transcript(path: String) -> Result<(), String> {
    if std::path::Path::new("/Applications/Obsidian.app").exists() {
        let url = format!("obsidian://open?path={}", urlencode(&path));
        if tauri_plugin_opener::open_url(url, None::<&str>).is_ok() {
            return Ok(());
        }
    }
    tauri_plugin_opener::open_path(path, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn reveal(path: String) -> Result<(), String> {
    tauri_plugin_opener::reveal_item_in_dir(path).map_err(|e| e.to_string())
}

/// Share…: the system's share menu for the copy just made: on macOS below the Share button (its rectangle in the
/// page), on Windows the Share panel. Elsewhere: the copy shown in its folder.
#[tauri::command]
async fn share_files(window: tauri::WebviewWindow, paths: Vec<String>, x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let view = window.ns_view().map_err(|e| e.to_string())? as usize;
        let (tx, rx) = std::sync::mpsc::channel();
        window
            .run_on_main_thread(move || {
                let _ = tx.send(crate::macos::share_files(view as *mut std::ffi::c_void, &paths, x, y, width, height));
            })
            .map_err(|e| e.to_string())?;
        rx.recv().map_err(|e| e.to_string())?
    }
    #[cfg(windows)]
    {
        let _ = (x, y, width, height);
        let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as isize;
        let (tx, rx) = std::sync::mpsc::channel();
        window
            .run_on_main_thread(move || {
                let _ = tx.send(windows_share::share(hwnd, paths));
            })
            .map_err(|e| e.to_string())?;
        rx.recv().map_err(|e| e.to_string())?
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = (window, x, y, width, height);
        paths.first().map_or(Ok(()), |p| reveal(p.clone()))
    }
}

/// Share… as PDF: the engine's HTML copy (`make_copy` with format "html") turned into a PDF next to it by
/// stenny-agent (`--pdf`, the macOS text system paginates it); the HTML is removed. Returns the PDF's path.
#[tauri::command]
async fn make_pdf(html: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let agent = menubar::agent_path().ok_or("The PDF couldn't be made: stenny-agent is missing.")?;
        let out = std::process::Command::new(agent).args(["--pdf", &html]).output().map_err(|e| e.to_string())?;
        let text = String::from_utf8_lossy(&out.stdout);
        let line = text.lines().last().unwrap_or("");
        match line.strip_prefix("pdf ") {
            Some(path) => Ok(path.to_string()),
            None => Err(line.strip_prefix("error ").unwrap_or("The PDF couldn't be made.").to_string()),
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// "Did the lecturer agree?" (asked once per course and use); the window asks it before sharing.
#[tauri::command]
async fn lecturer_agreed(app: AppHandle, purpose: String, course: String, event: bool) -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(move || menubar::lecturer_agreed(&app, &purpose, &course, event))
        .await
        .map_err(|e| e.to_string())
}

/// The library window's footer: Send Feedback… and Check for Updates…, as in the menu bar.
#[tauri::command]
fn show_feedback(app: AppHandle) {
    show_window(&app, "feedback");
}

/// Share Stenny in the library window's footer: text to the clipboard (pbcopy; the web view's own clipboard
/// isn't reliable in a WKWebView).
#[tauri::command]
fn copy_text(text: String) -> Result<(), String> {
    use std::io::Write;
    let mut child = std::process::Command::new("/usr/bin/pbcopy")
        .stdin(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    child.stdin.take().ok_or("no stdin")?.write_all(text.as_bytes()).map_err(|e| e.to_string())?;
    match child.wait() {
        Ok(s) if s.success() => Ok(()),
        Ok(s) => Err(format!("pbcopy: {s}")),
        Err(e) => Err(e.to_string()),
    }
}

/// Settings' microphone: the one the next recording uses (null: the system's input device).
#[tauri::command]
fn microphone_pref(app: AppHandle) -> Option<String> {
    menubar::microphone_pref(&app)
}

#[tauri::command]
async fn set_microphone_pref(app: AppHandle, device: Option<String>) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || menubar::set_microphone_pref(&app, device)).await.map_err(|e| e.to_string())
}

/// Opening at login: `on` true/false changes it, null only asks. "on", "off", "approval" or null (can't tell).
#[tauri::command]
async fn login_item(app: AppHandle, on: Option<bool>) -> Result<Option<String>, String> {
    let command = match on {
        Some(true) => "login",
        Some(false) => "login off",
        None => "login check",
    };
    tauri::async_runtime::spawn_blocking(move || menubar::login_item(&app, command)).await.map_err(|e| e.to_string())
}

/// After the pause shortcut changed in the config: registers the new one. Its problem, if it couldn't.
#[tauri::command]
async fn apply_shortcut(app: AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || menubar::restart_agent(&app)).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn check_updates(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || menubar::check_updates(&app)).await.map_err(|e| e.to_string())
}

/// From the "Record a talk" window: start recording a talk with this title, in this language (None: mixed).
#[tauri::command]
async fn start_talk(app: AppHandle, title: String, language: Option<String>) -> Result<(), String> {
    let title = title.trim().to_string();
    if title.is_empty() {
        return Err("Give the talk a title.".into());
    }
    tauri::async_runtime::spawn_blocking(move || menubar::start_with(&app, &title, true, language.as_deref()))
        .await
        .map_err(|e| e.to_string())?
}

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// Shows a window, creating it the first time: "library" or "talk".
pub fn show_window(app: &AppHandle, label: &str) {
    let app2 = app.clone();
    let label = label.to_string();
    let _ = app.run_on_main_thread(move || {
        if let Some(window) = app2.get_webview_window(&label) {
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
            return;
        }
        let built = match label.as_str() {
            "feedback" => WebviewWindowBuilder::new(&app2, "feedback", WebviewUrl::App("feedback".into()))
                .title("Send Feedback")
                .inner_size(460.0, 430.0)
                .resizable(false)
                .minimizable(false)
                .center()
                .build(),
            "talk" => WebviewWindowBuilder::new(&app2, "talk", WebviewUrl::App("talk".into()))
                .title("Record a Talk or Event")
                .inner_size(460.0, 330.0)
                .resizable(false)
                .minimizable(false)
                .center()
                .build(),
            _ => return,
        };
        if let Ok(window) = built {
            let _ = window.set_focus();
        }
    });
}

/// Quits as the menu's Quit does, for a quit asked for from outside (macOS, through stenny-agent too):
/// right away, or after asking while a lecture records.
pub fn quit_as_asked(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        if menubar::confirm_quit(&app) {
            app.exit(0);
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let library_only = std::env::args().any(|a| a == "--library");
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(Arc::new(Engine::default()))
        .manage(MenuBar::default())
        .invoke_handler(tauri::generate_handler![engine, open_transcript, reveal, make_pdf, lecturer_agreed, start_talk, show_feedback, share_files, copy_text, check_updates, microphone_pref, set_microphone_pref, login_item, apply_shortcut])
        .setup(move |app| {
            if !library_only {
                // A Dock app with the menu bar icon next to it. Info.plist keeps LSUIElement for the helpers in the
                // bundle (stenny-agent reads the same Info.plist), so the app itself asks for its Dock icon here.
                #[cfg(target_os = "macos")]
                app.set_activation_policy(tauri::ActivationPolicy::Regular);
                menubar::install(app.handle())?;
            } else if let Some(window) = app.get_webview_window("library") {
                let _ = window.show();
            }
            Ok(())
        })
        .on_window_event(move |window, event| {
            // With the menu bar icon, closing the library only hides it; Stenny keeps running for the next lecture.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if !library_only && window.label() == "library" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Stenny");
    app.run(move |app, event| {
        // "Quit Stenny" from macOS (an installer, logging out) works like the menu's Quit. Set up once launched:
        // AppKit puts its own handler in place while launching, over one set up earlier (in `setup`).
        #[cfg(target_os = "macos")]
        if let RunEvent::Ready = event {
            if !library_only {
                // opened by the user: the library comes up; opened at login: Stenny waits in the Dock and menu bar
                if !macos::launched_at_login() {
                    show_window(app, "library");
                }
                let app = app.clone();
                macos::handle_quit_event(move || quit_as_asked(&app));
            }
        }
        // Opened again while running (Dock icon, Spotlight, Finder) with no window showing: the library comes back
        #[cfg(target_os = "macos")]
        if let RunEvent::Reopen { has_visible_windows: false, .. } = event {
            show_window(app, "library");
        }
        // ⌘Q while a lecture records asks first (the menu's Quit does the same)
        if let RunEvent::ExitRequested { api, code: None, .. } = event {
            if !library_only {
                api.prevent_exit();
                let app = app.clone();
                std::thread::spawn(move || {
                    if menubar::confirm_quit(&app) {
                        app.exit(0);
                    }
                });
            }
        }
    });
}
