//! Stenny's app: the menu bar (menubar.rs) and its windows (the library, "Record a talk"). The windows'
//! interface is the web code in desktop/src; everything about lectures comes from the engine
//! (`stenny app-server`, see engine.rs).
//!
//! `--library` runs the library window alone, without the menu bar icon: for trying a build next to the
//! installed Stenny, which keeps its own icon (one Stenny in the menu bar, never two).

mod engine;
#[cfg(target_os = "macos")]
mod macos;
mod menubar;

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
async fn lecturer_agreed(app: AppHandle, use_: String, course: String, event: bool) -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(move || menubar::lecturer_agreed(&app, &use_, &course, event))
        .await
        .map_err(|e| e.to_string())
}

/// The library window's footer: Send Feedback… and Check for Updates…, as in the menu bar.
#[tauri::command]
fn show_feedback(app: AppHandle) {
    show_window(&app, "feedback");
}

#[tauri::command]
async fn check_updates(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || menubar::check_updates(&app)).await.map_err(|e| e.to_string())
}

/// From the "Record a talk" window: start recording a talk with this title.
#[tauri::command]
async fn start_talk(app: AppHandle, title: String) -> Result<(), String> {
    let title = title.trim().to_string();
    if title.is_empty() {
        return Err("Give the talk a title.".into());
    }
    tauri::async_runtime::spawn_blocking(move || menubar::start(&app, &title, true))
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
                .inner_size(440.0, 262.0)
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
        .invoke_handler(tauri::generate_handler![engine, open_transcript, reveal, make_pdf, lecturer_agreed, start_talk, show_feedback, check_updates])
        .setup(move |app| {
            if !library_only {
                // a menu bar app: no Dock icon; its windows still come up when opened from the menu
                #[cfg(target_os = "macos")]
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
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
                let app = app.clone();
                macos::handle_quit_event(move || quit_as_asked(&app));
            }
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
