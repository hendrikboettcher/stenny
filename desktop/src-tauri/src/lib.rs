//! Stenny's app: a menu bar icon and the library window. The window's interface is the web code in desktop/src;
//! everything about lectures comes from the engine (`stenny app-server`, see engine.rs).
//!
//! `--library` runs the window alone, without the menu bar icon: for trying a build next to the installed
//! Stenny, which keeps its own icon (one Stenny in the menu bar, never two).

mod engine;

use engine::Engine;
use serde_json::Value;
use std::sync::Arc;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WindowEvent};

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

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

fn show_library(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("library") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn add_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Stenny…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Stenny", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &PredefinedMenuItem::separator(app)?, &quit])?;
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    TrayIconBuilder::with_id("stenny")
        .icon(icon)
        .icon_as_template(true)
        .tooltip("Stenny")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_library(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Right, button_state: MouseButtonState::Up, .. } = event {
                show_library(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let library_only = std::env::args().any(|a| a == "--library");
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::new(Engine::default()))
        .invoke_handler(tauri::generate_handler![engine, open_transcript, reveal])
        .setup(move |app| {
            if !library_only {
                add_tray(app.handle())?;
            }
            Ok(())
        })
        .on_window_event(move |window, event| {
            // With the menu bar icon, closing the window only hides it; Stenny keeps running for the next lecture.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if !library_only {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Stenny");
}
