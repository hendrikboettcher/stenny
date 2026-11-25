//! The app menu at the top of the screen while a window of Stenny's is in front (macOS; Windows has no such menu, and a
//! menu bar inside each window would only repeat the window's own buttons).
//!
//! Besides the standard items it has Stenny's own: File (add a recording or transcript, open, show and share the
//! lecture shown), Edit ▸ Rename, a Recording menu (record the course shown or a talk, pause, stop, the text so far,
//! cancel), Window ▸ Stenny ⌘0 and Help (the docs on stenny.eu, Send Feedback…).
//!
//! What the window shows decides which items work: the library page tells (`menu_state`, routes/+page.svelte) and
//! the menu bar tells the recording's state (`recording_changed`, from menubar.rs's redraw). The items that act on
//! the lecture shown go to the library page ("menu" event); the recording's items have the menu bar menu's ids, whose
//! handler (menubar.rs, `on_menu`) hears every menu's clicks, so they do exactly what the menu bar's items do.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use crate::i18n;
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID};
use tauri::{AppHandle, Emitter, Wry};

/// What the library page shows, as far as the menu cares.
#[derive(Deserialize, Default, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PageMenu {
    /// the next lecture's number of the course shown, if a course is shown (not talks) and it may record now
    record: Option<u32>,
    /// a course or the talks are shown: a file can be added to them
    add_file: bool,
    /// the lecture shown has its final transcript: open, show in Finder, share
    transcript: bool,
    /// the lecture shown can be renamed
    rename: bool,
}

#[derive(Default)]
struct State {
    items: HashMap<&'static str, MenuItem<Wry>>,
    page: PageMenu,
    recording: String, // the recording's state as menubar.rs has it: "", "idle", "starting", "recording", "paused" …
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

/// "&" is the mnemonic marker in menus, "&&" shows one (Stop & Make Final Transcript).
fn label(text: String) -> String {
    text.replace('&', "&&")
}

/// The menu, in Stenny's language. Built again when the language changes (lib.rs, `language_changed`).
pub fn build(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let t = |key: &str| label(i18n::text(key, &[]));
    let about = tauri::menu::AboutMetadata {
        name: Some("Stenny".into()),
        version: Some(app.package_info().version.to_string()),
        ..Default::default()
    };
    let item = |id: &str, text: String, accel: Option<&str>| MenuItem::with_id(app, id, text, false, accel);
    // the items whose text or state follows the window and the recording; their text is set in `apply`
    let mut items: HashMap<&'static str, MenuItem<Wry>> = HashMap::new();
    for (id, accel) in [
        ("add-file", Some("CmdOrCtrl+O")),
        ("open-transcript", Some("CmdOrCtrl+Down")),
        ("reveal", Some("Alt+CmdOrCtrl+R")),
        ("share", None),
        ("rename", None),
        ("record", Some("CmdOrCtrl+N")),
        ("talk", Some("Shift+CmdOrCtrl+N")),
        // the pause shortcut is put on Pause/Resume when the menu opens (macos.rs, `watch_menus`): it's global
        ("toggle", None),
        ("stop", None),
        ("open-text", None),
        ("cancel", None),
    ] {
        items.insert(id, item(id, String::new(), accel)?);
    }
    let i = |id: &str| &items[id];
    let menu = Menu::with_items(app, &[
        &Submenu::with_items(app, "Stenny", true, &[
            &PredefinedMenuItem::about(app, Some(&t("app.about")), Some(about))?,
            &MenuItem::with_id(app, "updates", t("lib.updates"), true, None::<&str>)?,
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
        &Submenu::with_items(app, t("app.file"), true, &[
            i("add-file"),
            &PredefinedMenuItem::separator(app)?,
            i("open-transcript"),
            i("reveal"),
            i("share"),
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, Some(&t("app.closeWindow")))?,
        ])?,
        &Submenu::with_items(app, t("app.edit"), true, &[
            &PredefinedMenuItem::undo(app, Some(&t("app.undo")))?,
            &PredefinedMenuItem::redo(app, Some(&t("app.redo")))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, Some(&t("app.cut")))?,
            &PredefinedMenuItem::copy(app, Some(&t("app.copy")))?,
            &PredefinedMenuItem::paste(app, Some(&t("app.paste")))?,
            &PredefinedMenuItem::select_all(app, Some(&t("app.selectAll")))?,
            &PredefinedMenuItem::separator(app)?,
            i("rename"),
        ])?,
        &Submenu::with_items(app, t("app.view"), true, &[
            &MenuItem::with_id(app, "reload", t("app.reload"), true, Some("CmdOrCtrl+R"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, Some(&t("app.fullscreen")))?,
        ])?,
        &Submenu::with_items(app, t("app.recording"), true, &[
            i("record"),
            i("talk"),
            &PredefinedMenuItem::separator(app)?,
            i("toggle"),
            i("stop"),
            i("open-text"),
            &PredefinedMenuItem::separator(app)?,
            i("cancel"),
        ])?,
        &Submenu::with_id_and_items(app, WINDOW_SUBMENU_ID, t("app.window"), true, &[
            &PredefinedMenuItem::minimize(app, Some(&t("app.minimize")))?,
            &PredefinedMenuItem::maximize(app, Some(&t("app.zoom")))?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "library", "Stenny", true, Some("CmdOrCtrl+0"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::bring_all_to_front(app, Some(&t("app.bringAllToFront")))?,
        ])?,
        &Submenu::with_id_and_items(app, HELP_SUBMENU_ID, t("app.help"), true, &[
            &MenuItem::with_id(app, "help", t("app.helpDocs"), true, None::<&str>)?,
            &MenuItem::with_id(app, "help-problems", t("app.helpProblems"), true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "feedback", t("lib.feedback"), true, None::<&str>)?,
        ])?,
    ])?;
    let changes = {
        let mut state = STATE.lock().unwrap();
        let state = state.get_or_insert_with(State::default);
        state.items = items;
        plan(state)
    };
    apply(changes);
    Ok(menu)
}

/// Each item's text and whether it works now, from what the page shows and the recording.
fn plan(state: &State) -> Vec<(MenuItem<Wry>, String, bool)> {
    let page = &state.page;
    let rec = state.recording.as_str();
    let active = matches!(rec, "recording" | "paused");
    let idle = matches!(rec, "idle" | ""); // as the menu bar menu offers its Start items
    let t = |key: &str| label(i18n::text(key, &[]));
    let record = match page.record {
        Some(n) => label(i18n::text("lib.recordLecture", &[("n", n.to_string())])),
        None => t("app.recordLecture"),
    };
    [
        ("add-file", t("app.addFile"), page.add_file),
        ("open-transcript", t("menu.openTranscript"), page.transcript),
        ("reveal", t("common.revealIn"), page.transcript),
        ("share", t("lib.share"), page.transcript),
        ("rename", t("app.rename"), page.rename),
        ("record", record, idle && page.record.is_some()),
        ("talk", t("menu.talk"), idle),
        ("toggle", t(if rec == "paused" { "menu.resume" } else { "menu.pause" }), active),
        ("stop", t("menu.stop"), active),
        ("open-text", t("menu.textSoFar"), active),
        ("cancel", t("menu.cancel"), active),
    ]
    .into_iter()
    .filter_map(|(id, text, enabled)| state.items.get(id).map(|item| (item.clone(), text, enabled)))
    .collect()
}

/// The changes, outside the lock: a menu item changes on the main thread, and from another thread it waits for it.
fn apply(changes: Vec<(MenuItem<Wry>, String, bool)>) {
    for (item, text, enabled) in changes {
        if item.text().ok().as_deref() != Some(text.as_str()) {
            let _ = item.set_text(text);
        }
        if item.is_enabled().ok() != Some(enabled) {
            let _ = item.set_enabled(enabled);
        }
    }
}

/// The library page's part (`menu_state`): what it shows changed.
pub fn page_changed(page: PageMenu) {
    let changes = {
        let mut state = STATE.lock().unwrap();
        let state = state.get_or_insert_with(State::default);
        if state.page == page {
            return;
        }
        state.page = page;
        plan(state)
    };
    apply(changes);
}

/// The menu bar's part: the recording's state changed (or not: it asks on every redraw, so this is cheap then).
pub fn recording_changed(recording: &str) {
    let changes = {
        let mut state = STATE.lock().unwrap();
        let state = state.get_or_insert_with(State::default);
        if state.recording == recording {
            return;
        }
        state.recording = recording.to_string();
        plan(state)
    };
    apply(changes);
}

/// A click on one of the menu's own items (the recording's are menubar.rs's). Some go to the library page, which
/// comes up first: they act on what it shows.
pub fn on_event(app: &AppHandle, id: &str) {
    match id {
        "settings" => crate::open_settings(app),
        // the window in front (the menu is the app's, not a window's)
        "reload" => {
            if let Some(window) = crate::focused_window(app) {
                crate::reload(&window);
            }
        }
        "updates" => {
            let app = app.clone();
            std::thread::spawn(move || crate::updater::check_updates(&app));
        }
        "library" => crate::show_window(app, "library"),
        "feedback" => crate::show_window(app, "feedback"),
        "help" | "help-problems" => {
            let docs = if i18n::language() == "de" { "https://stenny.eu/de/docs/" } else { "https://stenny.eu/docs/" };
            let page = if id == "help-problems" { "troubleshooting/" } else { "" };
            let _ = tauri_plugin_opener::open_url(format!("{docs}{page}"), None::<&str>);
        }
        "add-file" | "open-transcript" | "reveal" | "share" | "rename" | "record" => {
            crate::show_window(app, "library");
            let _ = app.emit_to("library", "menu", id);
        }
        _ => {}
    }
}
