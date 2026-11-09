//! Stenny's app: a Dock app with a menu bar icon (menubar.rs) and its windows (the library, "Record a talk"). The windows'
//! interface is the web code in desktop/src; everything about lectures comes from the engine
//! (`stenny app-server`, see engine.rs).
//!
//! `--library` runs the library window alone, without the menu bar icon: for trying a build next to the
//! installed Stenny, which keeps its own icon (one Stenny in the menu bar, never two).

mod engine;
mod i18n;
#[cfg(target_os = "macos")]
mod macos;
mod menubar;
#[cfg(windows)]
mod windows_share;

use engine::Engine;
use i18n::tr;
use menubar::MenuBar;
use serde_json::Value;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};

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
    // a folder Stenny hasn't made yet (Talks before the first talk): the nearest folder above it that's there
    let mut shown = std::path::PathBuf::from(&path);
    while !shown.exists() && shown.pop() {}
    tauri_plugin_opener::reveal_item_in_dir(if shown.as_os_str().is_empty() { path.into() } else { shown })
        .map_err(|e| e.to_string())
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
        let agent = menubar::agent_path().ok_or_else(|| tr!("error.noAgentPdf"))?;
        // stenny-agent's own messages in Stenny's language
        let lang = i18n::language();
        let out = std::process::Command::new(agent).args(["--pdf", &html, "--lang", lang]).output().map_err(|e| e.to_string())?;
        let text = String::from_utf8_lossy(&out.stdout);
        let line = text.lines().last().unwrap_or("");
        match line.strip_prefix("pdf ") {
            Some(path) => Ok(path.to_string()),
            None => Err(line.strip_prefix("error ").map(String::from).unwrap_or_else(|| tr!("error.pdf"))),
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
        return Err(tr!("talk.noTitle"));
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

/// The windows' language ("en" or "de"), asked by each window as it opens (routes/+layout.svelte). From the engine,
/// so it's always what the config says now; the menu bar follows if it was behind.
#[tauri::command]
async fn ui_language(app: AppHandle) -> String {
    let lang = tauri::async_runtime::spawn_blocking(move || {
        let engine = app.state::<Arc<Engine>>().inner().clone();
        let settings = engine.call("settings", serde_json::json!({})).unwrap_or(Value::Null);
        if let Some(lang) = settings.get("language").and_then(Value::as_str) {
            if i18n::set_language(lang) {
                language_changed(&app);
            }
        }
        i18n::language()
    })
    .await;
    lang.unwrap_or("en").to_string()
}

/// Settings ▸ General ▸ Language changed (and is saved in the config): every window, the menus and the agent follow.
/// A window changed the course list (lib/engine.ts, coursesChanged): the menu bar reads it again.
#[tauri::command]
fn courses_changed(app: AppHandle) {
    menubar::lists_changed(&app);
}

#[tauri::command]
async fn set_ui_language(app: AppHandle, language: String) {
    if i18n::set_language(&language) {
        let _ = tauri::async_runtime::spawn_blocking(move || language_changed(&app)).await;
    }
}

/// After the language changed: the windows (the "language" event, routes/+layout.svelte), their titles, the app
/// menu, the menu bar's menu, and stenny-agent (its notifications), which is started again with the new language.
pub fn language_changed(app: &AppHandle) {
    let _ = app.emit("language", i18n::language());
    let app2 = app.clone();
    let _ = app.run_on_main_thread(move || {
        for (label, key) in [("feedback", "feedback.window"), ("talk", "talk.window")] {
            if let Some(window) = app2.get_webview_window(label) {
                let _ = window.set_title(&i18n::text(key, &[]));
            }
        }
        #[cfg(target_os = "macos")]
        if let Ok(menu) = app_menu(&app2) {
            let _ = app2.set_menu(menu);
        }
    });
    menubar::redraw(app, true);
    menubar::restart_agent(app);
}

/// The app menu while a window of Stenny's is in front (macOS): Tauri's own default, in Stenny's language.
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let about = tauri::menu::AboutMetadata {
        name: Some("Stenny".into()),
        version: Some(app.package_info().version.to_string()),
        ..Default::default()
    };
    let t = |key: &str| i18n::text(key, &[]);
    Menu::with_items(app, &[
        #[cfg(target_os = "macos")]
        &Submenu::with_items(app, "Stenny", true, &[
            &PredefinedMenuItem::about(app, Some(&t("app.about")), Some(about))?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "settings", t("lib.settings"), true, Some("CmdOrCtrl+,"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::services(app, Some(&t("app.services")))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, Some(&t("app.hide")))?,
            &PredefinedMenuItem::hide_others(app, Some(&t("app.hideOthers")))?,
            &PredefinedMenuItem::show_all(app, Some(&t("app.showAll")))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, Some(&t("app.quit")))?,
        ])?,
        &Submenu::with_items(app, t("app.file"), true, &[&PredefinedMenuItem::close_window(app, Some(&t("app.closeWindow")))?])?,
        &Submenu::with_items(app, t("app.edit"), true, &[
            &PredefinedMenuItem::undo(app, Some(&t("app.undo")))?,
            &PredefinedMenuItem::redo(app, Some(&t("app.redo")))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, Some(&t("app.cut")))?,
            &PredefinedMenuItem::copy(app, Some(&t("app.copy")))?,
            &PredefinedMenuItem::paste(app, Some(&t("app.paste")))?,
            &PredefinedMenuItem::select_all(app, Some(&t("app.selectAll")))?,
        ])?,
        &Submenu::with_items(app, t("app.view"), true, &[
            &MenuItem::with_id(app, "reload", t("app.reload"), true, Some("CmdOrCtrl+R"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, Some(&t("app.fullscreen")))?,
        ])?,
        &Submenu::with_id_and_items(app, WINDOW_SUBMENU_ID, t("app.window"), true, &[
            &PredefinedMenuItem::minimize(app, Some(&t("app.minimize")))?,
            &PredefinedMenuItem::maximize(app, Some(&t("app.zoom")))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, Some(&t("app.closeWindow")))?,
        ])?,
        &Submenu::with_id_and_items(app, HELP_SUBMENU_ID, t("app.help"), true, &[])?,
    ])
}

/// ⌘, or Stenny ▸ Settings…: the library window comes up showing Settings (routes/+page.svelte listens).
fn open_settings(app: &AppHandle) {
    show_window(app, "library");
    let _ = app.emit_to("library", "open-settings", ());
}

/// ⌘R / Ctrl+R or View ▸ Reload: the window's page loads again, like a browser's. Only the web view: a recording,
/// its live draft and a final pass run in their own processes and the menu bar icon is Rust's, so they all go on;
/// the page picks them up again as it loads (routes/+page.svelte). The page catches the key itself
/// (routes/+layout.svelte) and the menu has it too; where both get one press (Windows, maybe), it reloads once.
fn reload(window: &tauri::WebviewWindow) {
    static LAST: Mutex<Option<(String, Instant)>> = Mutex::new(None);
    {
        let mut last = LAST.lock().unwrap();
        if matches!(&*last, Some((label, at)) if label == window.label() && at.elapsed() < Duration::from_millis(800)) {
            return;
        }
        *last = Some((window.label().to_string(), Instant::now()));
    }
    let _ = window.reload();
}

#[tauri::command]
fn reload_window(window: tauri::WebviewWindow) {
    reload(&window);
}

/// Shows a window, creating it the first time: "library" or "talk".
pub fn show_window(app: &AppHandle, label: &str) {
    present(app, label, false);
}

/// The menu bar's "Text so far": the library comes up on the running recording, even if it was open already.
pub fn show_recording(app: &AppHandle) {
    present(app, "library", true);
}

/// The library coming back (it was hidden or in the Dock, or `to_recording`) is told so ("library-shown"): it then
/// selects a running recording (routes/+page.svelte). Showing it for the first time needs no word: the page loads.
fn present(app: &AppHandle, label: &str, to_recording: bool) {
    let app2 = app.clone();
    let label = label.to_string();
    let _ = app.run_on_main_thread(move || {
        if let Some(window) = app2.get_webview_window(&label) {
            let hidden = !window.is_visible().unwrap_or(true) || window.is_minimized().unwrap_or(false);
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
            if label == "library" && (hidden || to_recording) {
                let _ = window.emit("library-shown", ());
            }
            return;
        }
        let built = match label.as_str() {
            "feedback" => WebviewWindowBuilder::new(&app2, "feedback", WebviewUrl::App("feedback".into()))
                .title(tr!("feedback.window"))
                .inner_size(460.0, 430.0)
                .resizable(false)
                .minimizable(false)
                .center()
                .build(),
            "talk" => WebviewWindowBuilder::new(&app2, "talk", WebviewUrl::App("talk".into()))
                .title(tr!("talk.window"))
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
    // until the engine says which (menubar.rs, load_lists): the system's
    i18n::set_language(i18n::system_language());
    let app = tauri::Builder::default()
        .menu(app_menu)
        .on_menu_event(|app, event| {
            match event.id().as_ref() {
                "settings" => open_settings(app),
                // the window in front (the menu is the app's, not a window's)
                "reload" => {
                    if let Some(window) = app.webview_windows().into_values().find(|w| w.is_focused().unwrap_or(false)) {
                        reload(&window);
                    }
                }
                _ => {}
            }
        })
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(Arc::new(Engine::default()))
        .manage(MenuBar::default())
        .invoke_handler(tauri::generate_handler![engine, open_transcript, reveal, make_pdf, lecturer_agreed, start_talk, show_feedback, share_files, copy_text, check_updates, reload_window, microphone_pref, set_microphone_pref, login_item, apply_shortcut, ui_language, set_ui_language, courses_changed])
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
