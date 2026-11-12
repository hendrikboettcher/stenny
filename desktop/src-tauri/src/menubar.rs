//! The menu bar: Stenny's icon with the recording's clock next to it, and a classic macOS menu to start, pause,
//! resume and stop recordings, pick the microphone and open the library. Everything goes through the engine
//! (`stenny app-server`): `status` every second while recording, every 200 ms while one starts (and right after
//! Start), every three seconds otherwise. A change of state is sent to the windows as the "recording" event.
//!
//! Like the Swift app it replaces (app/Sources/Stenny/LectureController.swift): it asks once per course whether
//! the lecturer agreed to being recorded; a recording that vanishes without stopping (killed, crashed) is
//! recovered (`recover`) and started again without the live draft; quitting during a recording asks first.

use crate::engine::Engine;
use crate::i18n::{self, tr};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
#[cfg(not(windows))]
use std::io::Write;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, Wry};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind, MessageDialogResult};
#[cfg(not(target_os = "macos"))]
use tauri_plugin_notification::NotificationExt;

const TRAY: &str = "stenny";

#[derive(Serialize, Deserialize, Default, Clone)]
struct Prefs {
    microphone: Option<String>, // None: the system's input device
    #[serde(default)]
    login_item: bool,           // added itself to the login items once; after that it's the user's choice
}

#[derive(Default)]
struct State {
    status: Value,
    answered: Option<Instant>, // when an action (start, pause, stop …) last brought a status: older polls don't count
    shape: String, // what the menu shows, so it's only rebuilt when that changes
    courses: Vec<String>,
    devices: Vec<String>,
    default_device: Option<String>,
    prefs: Prefs,
    running: Option<(String, bool, bool)>, // course, is a talk, was recording (not paused)
    missed: u32,
    restarts: Vec<Instant>,
    last_warning: String,
    last_note: String,
    idle_message: String,
    lists_loaded: Option<Instant>,
    suggestion: Option<Suggestion>,          // the calendar's lecture on now (or within 10 minutes)
    course_for_title: HashMap<String, Option<String>>,
    notified: HashSet<String>,               // calendar events already announced (title and start)
    #[cfg_attr(windows, allow(dead_code))]
    agent_events: Vec<Value>,                // macOS: the Mac's own calendars' events on now (stenny-agent)
    link_events: Vec<Value>,                 // the linked calendars' events on now (the engine, calendars.py)
    no_reminders: bool,                      // "Record this lecture?" switched off in Settings ▸ Calendar
    #[cfg_attr(windows, allow(dead_code))]
    calendar_answers: u32,                   // macOS: how many "calendar-state" lines the agent has sent
    #[cfg_attr(windows, allow(dead_code))]
    calendar_state: String,                  // macOS: the agent's last "calendar-state" ("allowed ask")
    #[cfg_attr(windows, allow(dead_code))]
    agent_in: Option<std::process::ChildStdin>,
    status_item: Option<MenuItem<Wry>>,      // the menu's first line, updated in place
    icon: String,                            // the icon shown, so it is only set when it changes (no flicker)
    pause_accel: Option<String>,             // the pause shortcut as a physical accelerator ("Cmd+F1"), if not a character
    #[cfg_attr(windows, allow(dead_code))]
    login: Option<String>,                   // the agent's last "login on|off|approval" (Settings asks for it)
    shortcut_problem: Option<String>,        // the agent couldn't register the pause shortcut: why
    #[cfg_attr(windows, allow(dead_code))]
    pause_shortcut: String,                  // macOS: the pause shortcut the agent was told ("cmd+shift+ü")
    #[cfg_attr(windows, allow(dead_code))]
    shortcut_answers: u32,                   // macOS: how many "ready"/"error" lines the agent has sent
    pause_label: Option<String>,             // Windows: the pause shortcut as registered ("Strg+Umschalt+Ü")
}

/// The pause shortcut for the menu by physical key (the agent tells which: "ready 122 ⌘F1"), for keys that aren't a
/// single character; those go through macos.rs. Carbon key codes → the W3C key names muda uses.
#[cfg_attr(windows, allow(dead_code))]
fn accelerator(shortcut: &str, key_code: u32) -> Option<String> {
    let code = match key_code {
        0 => "KeyA", 11 => "KeyB", 8 => "KeyC", 2 => "KeyD", 14 => "KeyE", 3 => "KeyF", 5 => "KeyG", 4 => "KeyH",
        34 => "KeyI", 38 => "KeyJ", 40 => "KeyK", 37 => "KeyL", 46 => "KeyM", 45 => "KeyN", 31 => "KeyO", 35 => "KeyP",
        12 => "KeyQ", 15 => "KeyR", 1 => "KeyS", 17 => "KeyT", 32 => "KeyU", 9 => "KeyV", 13 => "KeyW", 7 => "KeyX",
        16 => "KeyY", 6 => "KeyZ", 29 => "Digit0", 18 => "Digit1", 19 => "Digit2", 20 => "Digit3", 21 => "Digit4",
        23 => "Digit5", 22 => "Digit6", 26 => "Digit7", 28 => "Digit8", 25 => "Digit9", 33 => "BracketLeft",
        30 => "BracketRight", 41 => "Semicolon", 39 => "Quote", 43 => "Comma", 47 => "Period", 44 => "Slash",
        42 => "Backslash", 50 => "Backquote", 27 => "Minus", 24 => "Equal", 49 => "Space",
        122 => "F1", 120 => "F2", 99 => "F3", 118 => "F4", 96 => "F5", 97 => "F6", 98 => "F7", 100 => "F8",
        101 => "F9", 109 => "F10", 103 => "F11", 111 => "F12",
        _ => return None,
    };
    let mods: Vec<&str> = shortcut.to_lowercase().split('+').filter_map(|m| match m {
        "cmd" => Some("Cmd"), "shift" => Some("Shift"), "ctrl" => Some("Ctrl"), "opt" => Some("Alt"), _ => None,
    }).collect();
    Some(format!("{}+{code}", mods.join("+")))
}

/// The pause shortcut from config.toml ("cmd+shift+ü") as Windows has it: ⌘ is Ctrl there, ⌥ is Alt. The key is
/// as printed on the keyboard ("ü", "space", "f5"). None if it isn't one (config.py's parse_shortcut is the rule).
#[cfg_attr(not(windows), allow(dead_code))]
#[derive(Debug, PartialEq)]
pub(crate) struct ShortcutParts {
    pub ctrl: bool,
    pub alt: bool,
    pub shift: bool,
    pub key: String,
}

#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn shortcut_parts(shortcut: &str) -> Option<ShortcutParts> {
    let lower = shortcut.trim().to_lowercase();
    let (mods, key) = match lower.strip_suffix("++") {
        Some(mods) => (mods, "+"), // "cmd+shift++": the key is "+" itself
        None => lower.rsplit_once('+')?,
    };
    let mut parts = ShortcutParts { ctrl: false, alt: false, shift: false, key: key.to_string() };
    for m in mods.split('+') {
        match m {
            "cmd" | "command" | "ctrl" | "control" => parts.ctrl = true,
            "opt" | "option" | "alt" => parts.alt = true,
            "shift" => parts.shift = true,
            _ => return None,
        }
    }
    (parts.ctrl || parts.alt).then_some(parts).filter(|p| !p.key.is_empty())
}

#[cfg_attr(not(windows), allow(dead_code))]
impl ShortcutParts {
    /// As Windows writes it, in Stenny's language: "Ctrl+Shift+Ü", "Strg+Umschalt+Ü".
    pub fn label(&self, key: &str) -> String {
        let mut names = Vec::new();
        if self.ctrl { names.push(tr!("key.ctrl")); }
        if self.alt { names.push(tr!("key.alt")); }
        if self.shift { names.push(tr!("key.shift")); }
        names.push(key.to_string());
        names.join("+")
    }
}

#[derive(Clone, Debug, PartialEq)]
struct Suggestion {
    course: String,
    range: String, // "09:40–11:10"
}

#[derive(Default)]
pub struct MenuBar(Mutex<State>, Wake, Wake); // the state; what wakes the status poll; what wakes the calendar links' poll

/// Wakes the polling thread before its interval is up: after Start, so "recording" shows as soon as it is true.
#[derive(Default)]
struct Wake {
    asked: Mutex<bool>,
    signal: Condvar,
}

fn engine(app: &AppHandle) -> Arc<Engine> {
    app.state::<Arc<Engine>>().inner().clone()
}

fn prefs_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("prefs.json"))
}

fn load_prefs(app: &AppHandle) -> Prefs {
    prefs_path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_prefs(app: &AppHandle, prefs: &Prefs) {
    if let Some(path) = prefs_path(app) {
        let _ = std::fs::create_dir_all(path.parent().unwrap());
        let _ = std::fs::write(path, serde_json::to_string_pretty(prefs).unwrap_or_default());
    }
}

fn str_of<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or("")
}

fn num(v: &Value, key: &str) -> f64 {
    v.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

fn clock(seconds: f64) -> String {
    let s = seconds.max(0.0) as u64;
    format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
}

fn is_active(status: &Value) -> bool {
    matches!(str_of(status, "state"), "recording" | "paused")
}

fn icon(name: &str) -> Image<'static> {
    let bytes: &'static [u8] = match name {
        "recording" => include_bytes!("../icons/tray-recording.png"),
        "paused" => include_bytes!("../icons/tray-paused.png"),
        "warning" => include_bytes!("../icons/tray-warning.png"),
        "finishing" => include_bytes!("../icons/tray-finishing.png"),
        "starting" => include_bytes!("../icons/tray-starting.png"),
        _ => include_bytes!("../icons/tray.png"),
    };
    let image = Image::from_bytes(bytes).expect("tray icon");
    #[cfg(windows)]
    let image = notification_area_icon(&image, crate::windows_agent::dark_taskbar());
    image
}

/// Windows: the menu bar's icon (black, macOS draws it as a template) for the notification area, which wants it
/// square and has no templates: centred on a square, and white when the taskbar is dark.
#[cfg_attr(not(windows), allow(dead_code))]
fn notification_area_icon(image: &Image<'_>, white: bool) -> Image<'static> {
    let (width, height) = (image.width() as usize, image.height() as usize);
    let side = width.max(height);
    let (dx, dy) = ((side - width) / 2, (side - height) / 2);
    let mut rgba = vec![0u8; side * side * 4];
    for y in 0..height {
        for x in 0..width {
            let from = &image.rgba()[(y * width + x) * 4..][..4];
            let to = &mut rgba[((y + dy) * side + x + dx) * 4..][..4];
            to.copy_from_slice(from);
            if white {
                for c in &mut to[..3] {
                    *c = 255 - *c;
                }
            }
        }
    }
    Image::new_owned(rgba, side as u32, side as u32)
}

/// One of Stenny's messages as a notification. On a Mac from Stenny's own process (macos_notifications.rs).
fn notify(app: &AppHandle, message: &str) {
    #[cfg(target_os = "macos")]
    {
        let _ = app;
        crate::macos_notifications::post(message);
    }
    #[cfg(not(target_os = "macos"))]
    let _ = app.notification().builder().title("Stenny").body(message).show();
}

/// A menu item's text: "&" is the mnemonic marker in menus, "&&" shows one.
fn label(text: String) -> String {
    text.replace('&', "&&")
}

/// Where deleted things go, as the window calls it (Trash, Recycle Bin, Papierkorb).
fn trash_name() -> String {
    tr!("common.trash")
}

fn alert(app: &AppHandle, title: &str, message: &str) {
    app.dialog().message(message).title(title).kind(MessageDialogKind::Info).blocking_show();
}

// ---------------------------------------------------------------- the menu

/// The menu's first line; it changes every second while paused, so it is updated in place, not rebuilt.
fn status_line(st: &State) -> String {
    let s = &st.status;
    let course = str_of(s, "course");
    match str_of(s, "state") {
        "recording" if s.get("input_lost").and_then(Value::as_bool) == Some(true) => tr!("menu.status.noAudio"),
        "recording" if !str_of(s, "warning").is_empty() => str_of(s, "warning").to_string(),
        // the words transcribed so far, while it transcribes (plugged in or on battery; a low battery or low
        // memory pauses it, and then the warning above shows instead)
        "recording" if s.get("transcribing").and_then(Value::as_bool) == Some(true) => {
            tr!("menu.status.recordingWords", course = course, words = s.get("words").and_then(Value::as_u64).unwrap_or(0))
        }
        "recording" => tr!("menu.status.recording", course = course),
        "paused" => tr!("menu.status.paused", time = clock(num(s, "current_pause_s")), course = course),
        "stopping" => tr!("menu.status.stopping"),
        "cancelling" => tr!("menu.status.cancelling"),
        "finalizing" | "done" => tr!("menu.status.finalizing"),
        "starting" => tr!("menu.status.starting"),
        _ if st.idle_message.is_empty() => tr!("menu.status.ready"),
        _ => tr!("menu.status.readyNote", note = st.idle_message),
    }
}

fn build_menu(app: &AppHandle, st: &State) -> tauri::Result<(Menu<Wry>, MenuItem<Wry>)> {
    let s = &st.status;
    let state = str_of(s, "state");
    let active = is_active(s);
    let status_item = MenuItem::with_id(app, "status", status_line(st), false, None::<&str>)?;
    let mut items: Vec<Box<dyn IsMenuItem<Wry>>> = vec![Box::new(status_item.clone()), Box::new(PredefinedMenuItem::separator(app)?)];

    if active {
        let pause = if state == "paused" { tr!("menu.resume") } else { tr!("menu.pause") };
        // Windows: the shortcut after a tab, where menus show one (it's global, not the menu's own accelerator)
        let pause = match &st.pause_label {
            Some(shortcut) if cfg!(windows) => format!("{}\t{shortcut}", label(pause)),
            _ => label(pause),
        };
        items.push(Box::new(MenuItem::with_id(app, "toggle", pause, true, st.pause_accel.as_deref())?));
        items.push(Box::new(MenuItem::with_id(app, "stop", label(tr!("menu.stop")), true, None::<&str>)?));
        items.push(Box::new(PredefinedMenuItem::separator(app)?));
    } else if state == "idle" || state.is_empty() {
        if let Some(sg) = &st.suggestion {
            items.push(Box::new(MenuItem::with_id(app, format!("start:{}", sg.course),
                tr!("menu.startSuggested", course = sg.course, range = sg.range), true, None::<&str>)?));
        }
        let starts: Vec<MenuItem<Wry>> = st
            .courses
            .iter()
            .map(|c| MenuItem::with_id(app, format!("start:{c}"), c, true, None::<&str>))
            .collect::<tauri::Result<_>>()?;
        let refs: Vec<&dyn IsMenuItem<Wry>> = starts.iter().map(|m| m as &dyn IsMenuItem<Wry>).collect();
        let empty = MenuItem::with_id(app, "nocourse", label(tr!("menu.noCourses")), false, None::<&str>)?;
        let title = label(tr!("menu.startLecture"));
        let start = if refs.is_empty() {
            Submenu::with_id_and_items(app, "start", &title, true, &[&empty])?
        } else {
            Submenu::with_id_and_items(app, "start", &title, true, &refs)?
        };
        items.push(Box::new(start));
        items.push(Box::new(MenuItem::with_id(app, "talk", label(tr!("menu.talk")), true, None::<&str>)?));
        items.push(Box::new(PredefinedMenuItem::separator(app)?));
    }

    // the microphone: while recording the one in use (switching it switches the recording), else the next one's
    let in_use = if active { s.get("microphone").and_then(Value::as_str).map(String::from) } else { None };
    let chosen = in_use.clone().or_else(|| st.prefs.microphone.clone());
    let device = chosen.clone().unwrap_or_else(|| tr!("menu.systemDefault"));
    let mut mics: Vec<CheckMenuItem<Wry>> = Vec::new();
    if !active {
        let default = label(st.default_device.as_deref().map(|d| tr!("menu.systemDefaultNamed", device = d))
            .unwrap_or_else(|| tr!("menu.systemDefault")));
        mics.push(CheckMenuItem::with_id(app, "mic:", default, true, chosen.is_none(), None::<&str>)?);
    }
    for d in &st.devices {
        mics.push(CheckMenuItem::with_id(app, format!("mic:{d}"), d, true, chosen.as_deref() == Some(d), None::<&str>)?);
    }
    let mic_refs: Vec<&dyn IsMenuItem<Wry>> = mics.iter().map(|m| m as &dyn IsMenuItem<Wry>).collect();
    let mic_enabled = state != "starting" && (!active || in_use.is_some());
    items.push(Box::new(Submenu::with_id_and_items(app, "mics", label(tr!("menu.microphone", device = device)), mic_enabled, &mic_refs)?));
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    if active {  // the transcript isn't there yet: the window shows the text so far
        items.push(Box::new(MenuItem::with_id(app, "open-text", label(tr!("menu.textSoFar")), true, None::<&str>)?));
    } else if !str_of(s, "transcript").is_empty() {
        items.push(Box::new(MenuItem::with_id(app, "transcript", label(tr!("menu.openTranscript")), true, None::<&str>)?));
    }
    items.push(Box::new(MenuItem::with_id(app, "open", label(tr!("menu.open")), true, None::<&str>)?));
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    // far from Stop, and it asks first: it throws the recording away
    if active {
        items.push(Box::new(MenuItem::with_id(app, "cancel", label(tr!("menu.cancel")), true, None::<&str>)?));
        items.push(Box::new(PredefinedMenuItem::separator(app)?));
    }
    items.push(Box::new(MenuItem::with_id(app, "quit", label(tr!("menu.quit")), true, None::<&str>)?));
    let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
    Ok((Menu::with_items(app, &refs)?, status_item))
}

fn shape(st: &State) -> String {
    let s = &st.status;
    // what the menu's items depend on; the clock goes to the title and the status line is updated in place,
    // because rebuilding the menu closes it while it is open
    format!(
        "{}|{}|{}|{}|{}|{:?}|{:?}|{:?}|{:?}",
        i18n::language(),
        str_of(s, "state"),
        str_of(s, "course"),
        str_of(s, "microphone"),
        str_of(s, "transcript").is_empty(),
        st.courses,
        st.devices,
        st.prefs.microphone,
        st.suggestion,
    )
}

pub(crate) fn redraw(app: &AppHandle, force: bool) {
    let Some(tray) = app.tray_by_id(TRAY) else { return };
    let bar = app.state::<MenuBar>();
    let mut st = bar.0.lock().unwrap();
    let s = st.status.clone();
    let state = str_of(&s, "state");
    let lost = s.get("input_lost").and_then(Value::as_bool) == Some(true);
    let (name, title) = match state {
        "recording" if lost => ("warning", Some(clock(num(&s, "elapsed_s") - num(&s, "paused_s")))),
        "recording" => ("recording", Some(clock(num(&s, "elapsed_s") - num(&s, "paused_s")))),
        "paused" => ("paused", Some(clock(num(&s, "elapsed_s") - num(&s, "paused_s")))),
        "stopping" | "cancelling" | "finalizing" | "done" => ("finishing", None),
        // the clock is there the moment Start is clicked, while the recorder comes up
        "starting" => ("starting", Some(clock(0.0))),
        _ => ("idle", None),
    };
    if st.icon != name {
        st.icon = name.to_string();
        let _ = tray.set_icon(Some(icon(name)));
        let _ = tray.set_icon_as_template(true);
    }
    let _ = tray.set_title(title.as_deref());
    // Windows shows no text next to the icon: the clock and the status line are its tooltip
    #[cfg(windows)]
    {
        let tip = match &title {
            Some(clock) => format!("Stenny · {clock} · {}", status_line(&st)),
            None => format!("Stenny · {}", status_line(&st)),
        };
        let _ = tray.set_tooltip(Some(tip.chars().take(127).collect::<String>()));
    }
    let new_shape = shape(&st);
    if force || new_shape != st.shape || st.status_item.is_none() {
        st.shape = new_shape;
        if let Ok((menu, item)) = build_menu(app, &st) {
            let _ = tray.set_menu(Some(menu));
            st.status_item = Some(item);
        }
    } else if let Some(item) = &st.status_item {
        let line = status_line(&st);
        if item.text().ok().as_deref() != Some(line.as_str()) {
            let _ = item.set_text(line);
        }
    }
}

// ---------------------------------------------------------------- polling

fn load_lists(app: &AppHandle) {
    let e = engine(app);
    let settings = e.call("settings", json!({})).unwrap_or(Value::Null);
    // the language, in case the config changed it (Settings tells the app itself, see lib.rs: set_ui_language)
    if let Some(lang) = settings.get("language").and_then(Value::as_str) {
        if i18n::set_language(lang) {
            crate::language_changed(app);
        }
    }
    let devices = e.call("devices", json!({})).unwrap_or(Value::Null);
    let bar = app.state::<MenuBar>();
    let mut st = bar.0.lock().unwrap();
    if let Some(on) = settings.get("reminders").and_then(Value::as_bool) {
        st.no_reminders = !on;
    }
    if let Some(list) = settings.get("courses").and_then(Value::as_array) {
        let courses: Vec<String> = list.iter().filter_map(|c| c.as_str().map(String::from)).collect();
        if courses != st.courses {
            st.course_for_title.clear(); // a course was added or removed: match the calendar again
        }
        st.courses = courses;
    }
    if let Some(list) = devices.get("devices").and_then(Value::as_array) {
        st.devices = list.iter().filter_map(|d| d.as_str().map(String::from)).collect();
        st.default_device = devices.get("default").and_then(Value::as_str).map(String::from);
    }
    st.lists_loaded = Some(Instant::now());
}

/// One status answer: notes and warnings, the "vanished recording" check, then the menu bar and the windows.
/// `asked`: when the polling thread asked for it; None for the answer to an action (start, pause, stop …). A poll
/// asked before the last action's answer came is older than what the menu shows: it would show "idle" again for a
/// moment right after Start.
fn update(app: &AppHandle, status: Value, asked: Option<Instant>) {
    let bar = app.state::<MenuBar>();
    let mut vanished = None;
    let mut messages = Vec::new();
    let changed;
    let plugged_in;
    {
        let mut st = bar.0.lock().unwrap();
        match asked {
            Some(t) if st.answered.is_some_and(|a| a > t) => return,
            Some(_) => {}
            None => st.answered = Some(Instant::now()),
        }
        let state = str_of(&status, "state").to_string();
        let before = str_of(&st.status, "state").to_string();
        // the library window (routes/+page.svelte) reads the library again: a new recording shows at once
        changed = (state != before).then(|| json!({
            "state": state,
            "course": str_of(&status, "course"),
            "kind": str_of(&status, "kind"),
            "transcript": str_of(&status, "transcript"),
            "started": state == "recording" && matches!(before.as_str(), "idle" | "" | "starting"),
        }));
        if let Some(error) = status.get("error").and_then(Value::as_str) {
            messages.push((true, tr!("note.didntStart", error = error))); // a dialog, not a notification
        }
        let note = str_of(&status, "note");
        if !note.is_empty() {
            st.last_note = note.to_string();
        }
        let warning = str_of(&status, "warning").to_string();
        if !warning.is_empty() && warning != st.last_warning {
            messages.push((false, warning.clone()));
        }
        st.last_warning = warning;
        let was_session = !matches!(str_of(&st.status, "state"), "idle" | "" | "starting");
        // after a lecture the menu just says "Ready" (where its final transcript is shows in the window);
        // only a lecture that ended for a reason, like a full disk, says so here ("Ready · …", see status_line)
        if was_session && state == "idle" {
            st.idle_message = st.last_note.clone();
            st.last_note.clear();
        }
        // a session that ends properly goes through "stopping"; one that goes from recording to gone was killed
        if is_active(&status) {
            st.missed = 0;
            let event = str_of(&status, "kind") == "event";
            st.running = Some((str_of(&status, "course").to_string(), event, state == "recording"));
        } else if state == "idle" {
            if st.running.is_some() {
                st.missed += 1;
                if st.missed >= 3 {
                    vanished = st.running.take();
                }
            }
        } else {
            st.running = None;
        }
        // plugged in just now with final passes waiting: the queue's run starts now, not when the background job
        // next fires (every 10 minutes). Only a change seen here counts: with the app just opened, that job does it.
        let ready = |s: &Value| s.get("queued").and_then(Value::as_u64).unwrap_or(0) > 0
            && s.get("on_power").and_then(Value::as_bool) == Some(true);
        let on_battery = st.status.get("on_power").and_then(Value::as_bool) == Some(false);
        plugged_in = on_battery && ready(&status);
        st.status = status;
    }
    if plugged_in {
        let app = app.clone();
        std::thread::spawn(move || {
            let _ = engine(&app).call("plugged_in", json!({}));
        });
    }
    for (as_dialog, m) in messages {
        if as_dialog {
            let app = app.clone();
            std::thread::spawn(move || alert(&app, "Stenny", &m));
        } else {
            notify(app, &m);
        }
    }
    if let Some((course, event, was_recording)) = vanished {
        let app = app.clone();
        std::thread::spawn(move || handle_vanished(&app, course, event, was_recording));
    }
    redraw(app, false);
    if let Some(payload) = changed {
        let _ = app.emit("recording", payload);
    }
}

/// The polling thread asks again now instead of when its interval is up.
fn wake(app: &AppHandle) {
    let bar = app.state::<MenuBar>();
    *bar.1.asked.lock().unwrap() = true;
    bar.1.signal.notify_one();
}

/// Recording again without the live draft needs far less memory; `recover` decides whether it really was a crash.
fn handle_vanished(app: &AppHandle, course: String, event: bool, was_recording: bool) {
    let e = engine(app);
    let recovered = e.call("recover", json!({})).ok();
    let any = recovered.as_ref().and_then(|r| r.get("recovered")).and_then(Value::as_array).is_some_and(|a| !a.is_empty());
    if !any {
        return;
    }
    let bar = app.state::<MenuBar>();
    let (restart, mic) = {
        let mut st = bar.0.lock().unwrap();
        st.restarts.retain(|t| t.elapsed() < Duration::from_secs(600));
        let ok = was_recording && st.restarts.len() < 3 && str_of(&st.status, "state") == "idle";
        if ok {
            st.restarts.push(Instant::now());
        }
        (ok, st.prefs.microphone.clone())
    };
    let target = if event { json!({"event": course}) } else { json!({"course": course}) };
    let mut params = target;
    params["draft"] = json!(false);
    if let Some(m) = mic {
        params["device"] = json!(m);
    }
    if restart && e.call("start", params).is_ok() {
        notify(app, &tr!("note.restarted", course = course));
    } else {
        notify(app, &tr!("note.stopped", course = course));
    }
}

/// The course list changed in a window (added, removed, deleted, renamed, brought back): the next poll, within
/// 3 s, reads it again and rebuilds Start Lecture, instead of waiting for the minute.
pub fn lists_changed(app: &AppHandle) {
    app.state::<MenuBar>().0.lock().unwrap().lists_loaded = None;
}

pub fn start_polling(app: AppHandle) {
    std::thread::spawn(move || loop {
        let stale = {
            let bar = app.state::<MenuBar>();
            let st = bar.0.lock().unwrap();
            !is_active(&st.status) && st.lists_loaded.is_none_or(|t| t.elapsed() > Duration::from_secs(60))
        };
        if stale {
            load_lists(&app);
        }
        let asked = Instant::now();
        let status = engine(&app).call("status", json!({})).unwrap_or_else(|_| json!({"state": "idle"}));
        update(&app, status, Some(asked));
        let bar = app.state::<MenuBar>();
        // while starting, every 200 ms: the clock starts as soon as the recording does
        let interval = match str_of(&bar.0.lock().unwrap().status, "state") {
            "starting" => Duration::from_millis(200),
            "recording" | "paused" | "stopping" | "cancelling" => Duration::from_secs(1),
            _ => Duration::from_secs(3),
        };
        let mut woken = bar.1.asked.lock().unwrap();
        if !*woken {
            woken = bar.1.signal.wait_timeout(woken, interval).unwrap().0;
        }
        *woken = false;
    });
}

// ---------------------------------------------------------------- actions

/// "Did the lecturer agree?" once per course and use (record or share), as in the Swift app.
pub fn lecturer_agreed(app: &AppHandle, use_: &str, course: &str, event: bool) -> bool {
    let e = engine(app);
    let params = json!({"use": use_, "course": course, "event": event});
    if e.call("consent", params.clone()).ok().and_then(|r| r.get("given").and_then(Value::as_bool)) == Some(true) {
        return true;
    }
    let use_ = if use_ == "record" { "record" } else { "share" };
    let who = if event { "event" } else { "course" };
    let question = i18n::text(&format!("consent.{use_}.{who}.question"), &[]);
    let why = i18n::text(&format!("consent.{use_}.{who}.why"), &[]);
    let yes = if use_ == "record" { tr!("consent.yesRecord") } else { tr!("consent.yesShare") };
    let answer = app
        .dialog()
        .message(tr!("consent.message", why = why, course = course))
        .title(question)
        .buttons(MessageDialogButtons::OkCancelCustom(yes.clone(), tr!("consent.notYet")))
        .blocking_show_with_result();
    let agreed = matches!(&answer, MessageDialogResult::Ok) || matches!(&answer, MessageDialogResult::Custom(t) if *t == yes);
    if agreed {
        let _ = e.call("give_consent", params);
    }
    agreed
}

/// Starts a recording of a course, or of a talk (`event`: its title). Runs off the main thread (dialogs).
pub fn start(app: &AppHandle, course: &str, event: bool) -> Result<(), String> {
    start_with(app, course, event, None).map(|_| ())
}

/// "Start Recording" in the "Record this lecture?" notification: as from the menu, on a thread of its own (it may
/// ask whether the lecturer agreed).
pub(crate) fn start_from_notification(app: &AppHandle, course: &str) {
    let (app, course) = (app.clone(), course.to_string());
    std::thread::spawn(move || {
        if let Err(e) = start(&app, &course, false) {
            alert(&app, "Stenny", &e)
        }
    });
}

/// `lang`: the language for this recording ("en", "de", "fr" or "mixed"), instead of the course's or talks' setting.
/// The menu, the calendar's notification, the talk window and the library window's Record buttons all start here, so
/// they never behave differently. Returns the engine's status ("starting"), or None if the lecturer hasn't agreed.
pub fn start_with(app: &AppHandle, course: &str, event: bool, lang: Option<&str>) -> Result<Option<Value>, String> {
    if !lecturer_agreed(app, "record", course, event) {
        return Ok(None);
    }
    let prefs = app.state::<MenuBar>().0.lock().unwrap().prefs.clone();
    let mut params = if event { json!({"event": course}) } else { json!({"course": course}) };
    if let Some(l) = lang {
        params["lang"] = json!(l);
    }
    if let Some(m) = prefs.microphone {
        params["device"] = json!(m);
    }
    // development: record an audio file instead of the microphone (with STENNY_HOME pointing at a test copy)
    if let Ok(file) = std::env::var("STENNY_TEST_FILE") {
        params["file"] = json!(file);
        params["llm"] = json!(false);
    }
    let status = engine(app).call("start", params)?;
    update(app, status.clone(), None);
    wake(app);
    Ok(Some(status))
}

/// "Cancel Recording…": the recording ends without a transcript and its files go to the Trash (Recycle Bin). Asked first, with
/// Keep Recording as the default button, so Return or a hasty click keeps it.
fn cancel_recording(app: &AppHandle) {
    let course = {
        let st = app.state::<MenuBar>();
        let st = st.0.lock().unwrap();
        if !is_active(&st.status) {
            return;
        }
        str_of(&st.status, "course").to_string()
    };
    let delete = tr!("cancelRec.delete");
    let answer = app
        .dialog()
        .message(tr!("cancelRec.message", course = course, trash = trash_name()))
        .title(tr!("cancelRec.title"))
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(tr!("cancelRec.keep"), delete.clone()))
        .blocking_show_with_result();
    if matches!(&answer, MessageDialogResult::Custom(t) if *t == delete) {
        send(app, "cancel");
    }
}

fn send(app: &AppHandle, cmd: &str) {
    if let Err(e) = command(app, cmd) {
        notify(app, &e);
    }
}

/// Pause/Resume ("toggle"), Stop, Cancel: the menu's items and the library window's activity bar. The new status.
pub fn command(app: &AppHandle, cmd: &str) -> Result<Value, String> {
    let status = engine(app).call("command", json!({"cmd": cmd}))?;
    update(app, status.clone(), None);
    wake(app);
    Ok(status)
}

/// What the library window's activity bar shows: the recording's status and the calendar's lecture on now
/// (`suggestion`, or null). With the menu bar, its last poll (one a second while recording), so the window adds no
/// work; in a `--library` window, which has no menu bar, the engine is asked.
pub fn window_status(app: &AppHandle) -> Value {
    if app.tray_by_id(TRAY).is_some() {
        let bar = app.state::<MenuBar>();
        let st = bar.0.lock().unwrap();
        let suggestion = st.suggestion.as_ref().map(|s| json!({"course": s.course, "range": s.range}));
        let status = if st.status.is_null() { json!({"state": "idle"}) } else { st.status.clone() };
        return json!({"status": status, "suggestion": suggestion});
    }
    let status = engine(app).call("status", json!({})).unwrap_or_else(|_| json!({"state": "idle"}));
    json!({"status": status, "suggestion": null})
}

fn choose_microphone(app: &AppHandle, device: Option<String>) {
    let active = is_active(&app.state::<MenuBar>().0.lock().unwrap().status);
    if active {
        if let Some(d) = device {
            if let Err(e) = engine(app).call("microphone", json!({"device": d})) {
                alert(app, "Stenny", &e);
            }
        }
    } else {
        let prefs = {
            let bar = app.state::<MenuBar>();
            let mut st = bar.0.lock().unwrap();
            st.prefs.microphone = device;
            st.prefs.clone()
        };
        save_prefs(app, &prefs);
    }
    redraw(app, true);
}

fn open_transcript(app: &AppHandle) {
    let path = str_of(&app.state::<MenuBar>().0.lock().unwrap().status, "transcript").to_string();
    if !path.is_empty() {
        let _ = crate::open_transcript(path);
    }
}

/// Quitting during a recording: stop it, keep it recording in the background, or cancel. True: quit.
pub fn confirm_quit(app: &AppHandle) -> bool {
    let status = engine(app).call("status", json!({})).unwrap_or(Value::Null);
    if !is_active(&status) {
        return true;
    }
    let (stop, keep) = (tr!("quit.stop"), tr!("quit.keep"));
    let answer = app
        .dialog()
        .message(tr!("quit.message"))
        .title(tr!("quit.title"))
        .buttons(MessageDialogButtons::YesNoCancelCustom(stop.clone(), keep.clone(), tr!("common.cancel")))
        .blocking_show_with_result();
    match answer {
        MessageDialogResult::Yes => { let _ = engine(app).call("command", json!({"cmd": "stop"})); true }
        MessageDialogResult::Custom(t) if t == stop => { let _ = engine(app).call("command", json!({"cmd": "stop"})); true }
        MessageDialogResult::No => true,
        MessageDialogResult::Custom(t) if t == keep => true,
        _ => false,
    }
}

fn on_menu(app: &AppHandle, id: &str) {
    let app = app.clone();
    let id = id.to_string();
    // off the main thread: actions may show dialogs or wait for the engine
    std::thread::spawn(move || match id.as_str() {
        "toggle" => send(&app, "toggle"),
        "stop" => send(&app, "stop"),
        "cancel" => cancel_recording(&app),
        "talk" => crate::show_window(&app, "talk"),
        "open" => crate::show_window(&app, "library"),
        "open-text" => crate::show_recording(&app),
        "transcript" => open_transcript(&app),
        "quit" => {
            if confirm_quit(&app) {
                app.exit(0);
            }
        }
        id if id.starts_with("start:") => {
            if let Err(e) = start(&app, &id["start:".len()..], false) {
                alert(&app, "Stenny", &e);
            }
        }
        id if id.starts_with("mic:") => {
            let name = &id["mic:".len()..];
            choose_microphone(&app, (!name.is_empty()).then(|| name.to_string()));
        }
        _ => {}
    });
}

/// "Check for Updates…" in the library window's footer: whether a newer Stenny is out. Installing it comes with the app's own updater; until
/// then "Download" opens the release page.
pub fn check_updates(app: &AppHandle) {
    let result = engine(app).call("check_update", json!({}));
    let dialog = app.dialog();
    match result {
        Err(e) => {
            dialog.message(e).title(tr!("updates.failed")).kind(MessageDialogKind::Warning).blocking_show();
        }
        Ok(r) if r.get("update_available").and_then(Value::as_bool) != Some(true) => {
            let current = str_of(&r, "current").to_string();
            dialog.message(tr!("updates.upToDate", version = current)).title(tr!("updates.upToDate.title")).blocking_show();
        }
        Ok(r) => {
            let (current, latest) = (str_of(&r, "current").to_string(), str_of(&r, "latest").to_string());
            let download = dialog
                .message(tr!("updates.available", version = current))
                .title(tr!("updates.available.title", version = latest))
                .buttons(MessageDialogButtons::OkCancelCustom(tr!("updates.download"), tr!("updates.notNow")))
                .blocking_show();
            if download {
                let _ = tauri_plugin_opener::open_url("https://github.com/hendrikboettcher/stenny/releases/latest", None::<&str>);
            }
        }
    }
}

/// The pause shortcut and other outside callers: pause or resume the running recording, or beep.
pub fn toggle_pause(app: &AppHandle) {
    let active = is_active(&app.state::<MenuBar>().0.lock().unwrap().status);
    if active {
        let app = app.clone();
        std::thread::spawn(move || send(&app, "toggle"));
    } else {
        // nothing to pause
        #[cfg(target_os = "macos")]
        let _ = std::process::Command::new("osascript").args(["-e", "beep"]).spawn();
        #[cfg(windows)]
        crate::windows_agent::beep();
    }
}

/// stenny-agent (app/Sources/stenny-agent): the global pause shortcut. Next to Stenny's own program in the app
/// bundle; $STENNY_AGENT or the Swift build folder during development.
#[cfg(not(windows))]
pub(crate) fn agent_path() -> Option<std::path::PathBuf> {
    let candidates = [
        std::env::var("STENNY_AGENT").ok().map(std::path::PathBuf::from),
        std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join("stenny-agent"))),
        Some(std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../app/.build/release/stenny-agent"))),
    ];
    candidates.into_iter().flatten().find(|p| p.exists())
}

#[cfg(not(windows))]
fn start_agent(app: &AppHandle) {
    let Some(exe) = agent_path() else {
        eprintln!("stenny-agent not found: no pause shortcut");
        return;
    };
    let app = app.clone();
    std::thread::spawn(move || {
        use std::io::BufRead;
        let settings = engine(&app).call("settings", json!({})).unwrap_or(Value::Null);
        let shortcut = settings.get("pause_shortcut").and_then(Value::as_str).unwrap_or("cmd+shift+ü").to_string();
        let data_dir = settings.get("data_dir").and_then(Value::as_str).unwrap_or_default().to_string();
        // its messages in Stenny's language ("lang …" when that changes, set_agent_language)
        let lang = i18n::language();
        let child = std::process::Command::new(exe)
            .args(["--shortcut", &shortcut, "--calendar", "--data-dir", &data_dir, "--lang", lang])
            .stdin(std::process::Stdio::piped()) // closes when Stenny quits, and the agent with it
            .stdout(std::process::Stdio::piped())
            .spawn();
        let Ok(mut child) = child else { return };
        {
            let bar = app.state::<MenuBar>();
            let mut st = bar.0.lock().unwrap();
            st.agent_in = child.stdin.take(); // open while Stenny runs
            st.pause_shortcut = shortcut.clone();
            if i18n::language() != lang {
                // changed while it started
                if let Some(stdin) = st.agent_in.as_mut() {
                    let _ = writeln!(stdin, "lang {}", i18n::language());
                }
            }
            // open at login by default, so the lecture notification works without thinking of it: once, and only
            // for a copy in an Applications folder (never a build copy)
            let installed = std::env::current_exe().is_ok_and(|e| {
                let home = std::env::var("HOME").unwrap_or_default();
                e.starts_with("/Applications") || e.starts_with(format!("{home}/Applications"))
            });
            if installed && !st.prefs.login_item {
                if let Some(stdin) = st.agent_in.as_mut() {
                    let _ = writeln!(stdin, "login");
                }
            }
        }
        let Some(out) = child.stdout.take() else { return };
        for line in std::io::BufReader::new(out).lines().map_while(Result::ok) {
            if let Some(rest) = line.strip_prefix("ready ") {
                let shortcut = {
                    let bar = app.state::<MenuBar>();
                    let mut st = bar.0.lock().unwrap();
                    st.shortcut_problem = None;
                    st.shortcut_answers += 1;
                    st.pause_shortcut.clone()
                };
                let code = rest.split(' ').next().and_then(|c| c.parse::<u32>().ok());
                // a character key (Ü) is put on the menu item natively (macos.rs): Tauri's physical accelerator
                // gets relabelled by macOS (⇧⌘Ö on a German keyboard)
                #[cfg(target_os = "macos")]
                let by_char = crate::macos::set_pause_shortcut(&shortcut);
                #[cfg(not(target_os = "macos"))]
                let by_char = false;
                let accel = if by_char { None } else { code.and_then(|c| accelerator(&shortcut, c)) };
                app.state::<MenuBar>().0.lock().unwrap().pause_accel = accel;
                redraw(&app, true);
            } else if line == "hotkey" {
                toggle_pause(&app);
            } else if line == "quit" {
                crate::quit_as_asked(&app); // macOS asked "Stenny" to quit and reached the helper
            } else if let Some(events) = line.strip_prefix("events ") {
                if let Ok(Value::Array(events)) = serde_json::from_str::<Value>(events) {
                    app.state::<MenuBar>().0.lock().unwrap().agent_events = events;
                    calendar(&app);
                }
            } else if line == "calendar-asked" {
                // the first time, macOS asked for the calendar; now Stenny asks for notifications, from its own
                // process (macOS turns the agent down, #60). Nothing shows once that has been answered
                #[cfg(target_os = "macos")]
                if crate::macos_notifications::state() == Some("ask") {
                    crate::macos_notifications::request(&app);
                }
            } else if let Some(state) = line.strip_prefix("calendar-state ") {
                // may Stenny read the calendar: the agent wrote it down for the engine
                // (calendar-mac.json). Whoever waits for this look (calendar_check) goes on; Settings looks again
                // only when something changed (its own look is answered with this line too)
                let changed = {
                    let bar = app.state::<MenuBar>();
                    let mut st = bar.0.lock().unwrap();
                    st.calendar_answers += 1;
                    let changed = st.calendar_state != state;
                    st.calendar_state = state.to_string();
                    changed
                };
                if changed {
                    let _ = app.emit("calendar", ());
                    // calendar-titles.json follows what the Mac sees now (the engine puts it together)
                    let bar = app.state::<MenuBar>();
                    *bar.2.asked.lock().unwrap() = true;
                    bar.2.signal.notify_one();
                }
            } else if line == "login ok" {
                let prefs = {
                    let bar = app.state::<MenuBar>();
                    let mut st = bar.0.lock().unwrap();
                    st.prefs.login_item = true;
                    st.prefs.clone()
                };
                save_prefs(&app, &prefs);
            } else if let Some(state) = line.strip_prefix("login ").filter(|s| ["on", "off", "approval"].contains(s)) {
                app.state::<MenuBar>().0.lock().unwrap().login = Some(state.to_string());
            } else if let Some(problem) = line.strip_prefix("error ") {
                {
                    let bar = app.state::<MenuBar>();
                    let mut st = bar.0.lock().unwrap();
                    st.shortcut_problem = Some(problem.to_string());
                    st.shortcut_answers += 1;
                }
                // nothing registered now: no shortcut on the menu item either
                #[cfg(target_os = "macos")]
                crate::macos::set_pause_shortcut("");
                app.state::<MenuBar>().0.lock().unwrap().pause_accel = None;
                redraw(&app, true);
                notify(&app, &tr!("note.shortcutBroken", problem = problem));
            }
        }
    });
}

/// Windows: what stenny-agent does on a Mac happens in the app (windows_agent.rs): the pause shortcut, and opening
/// at login by default, once, for an installed copy. The calendar is the linked ones only (poll_calendar_links).
#[cfg(windows)]
fn start_agent(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        register_shortcut(&app);
        let first = !app.state::<MenuBar>().0.lock().unwrap().prefs.login_item;
        if first && crate::windows_agent::installed() && crate::windows_agent::login_item("login").is_ok() {
            remember_login_item(&app);
        }
    });
}

/// Windows: registers the pause shortcut from the config (again); its problem, if it couldn't.
#[cfg(windows)]
fn register_shortcut(app: &AppHandle) -> Option<String> {
    let settings = engine(app).call("settings", json!({})).unwrap_or(Value::Null);
    let shortcut = settings.get("pause_shortcut").and_then(Value::as_str).unwrap_or("cmd+shift+ü").to_string();
    let registered = crate::windows_agent::register_shortcut(app, &shortcut);
    let problem = registered.as_ref().err().cloned();
    {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        st.pause_label = registered.ok();
        st.shortcut_problem = problem.clone();
    }
    if let Some(p) = &problem {
        notify(app, &tr!("note.shortcutBroken", problem = p));
    }
    redraw(app, true);
    problem
}

/// Opened at login once (by itself or by the user): after that it's the user's choice.
#[cfg_attr(not(windows), allow(dead_code))]
fn remember_login_item(app: &AppHandle) {
    let prefs = {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        st.prefs.login_item = true;
        st.prefs.clone()
    };
    save_prefs(app, &prefs);
}

/// The calendar's events on now (or within 10 minutes), from the Mac's own calendars (the agent) and from the linked
/// ones (the engine): the first that belongs to a course becomes the menu's suggestion, announced once by a
/// notification with "Start Recording" unless a recording runs or the reminders are switched off. A lecture that is
/// in both (the Mac's Google account and its link) counts once.
fn calendar(app: &AppHandle) {
    let bar = app.state::<MenuBar>();
    let (events, unknown) = {
        let st = bar.0.lock().unwrap();
        let mut seen = HashSet::new();
        let mut events: Vec<Value> =
            st.agent_events.iter().chain(st.link_events.iter()).filter(|e| seen.insert(event_key(e))).cloned().collect();
        events.sort_by(|a, b| start_of(a).total_cmp(&start_of(b)));
        let unknown: Vec<String> =
            events.iter().map(|e| str_of(e, "title").to_string()).filter(|t| !st.course_for_title.contains_key(t)).collect();
        (events, unknown)
    };
    if !unknown.is_empty() {
        if let Ok(Value::Object(matches)) = engine(app).call("match_courses", json!({"titles": unknown})) {
            let mut st = bar.0.lock().unwrap();
            for t in unknown {
                let course = matches.get(&t).and_then(Value::as_str).map(String::from);
                st.course_for_title.insert(t, course);
            }
        }
    }
    let mut announce = None;
    {
        let mut st = bar.0.lock().unwrap();
        let first = events.iter().find_map(|e| {
            let course = st.course_for_title.get(str_of(e, "title")).cloned().flatten()?;
            Some((Suggestion { course, range: str_of(e, "range").to_string() }, event_key(e)))
        });
        st.suggestion = first.as_ref().map(|(s, _)| s.clone());
        let busy = !matches!(str_of(&st.status, "state"), "idle" | "");
        if let Some((sg, key)) = first {
            if !busy && !st.no_reminders && st.notified.insert(key.clone()) {
                announce = Some((key, sg));
            }
        }
    }
    // a notification of the app's own with Start Recording: macos_notifications.rs, windows_agent.rs
    #[cfg(target_os = "macos")]
    if let Some((key, sg)) = announce.take() {
        crate::macos_notifications::announce_lecture(&key, &sg.course, &sg.range);
    }
    #[cfg(windows)]
    if let Some((_, sg)) = announce.take() {
        crate::windows_agent::announce_lecture(app, &sg.course, &sg.range);
    }
    let _ = announce;
    redraw(app, false);
}

fn start_of(event: &Value) -> f64 {
    event.get("start").and_then(Value::as_f64).unwrap_or(0.0)
}

/// What makes two calendar events the same lecture, whichever calendar they come from: the title and the minute it
/// starts. Also what an announced lecture is remembered by (an event that repeats weekly has one id for all weeks).
fn event_key(event: &Value) -> String {
    format!("{}|{}", str_of(event, "title"), (start_of(event) / 60.0).round() as i64)
}

/// The linked calendars (Settings ▸ Calendar ▸ Add a calendar link…), on a Mac and on Windows: every 30 seconds the
/// engine says which of their events are on now or start within 10 minutes; it fetches a link again when its copy
/// is a few minutes old. Woken early when a link was added or removed.
fn poll_calendar_links(app: AppHandle) {
    std::thread::spawn(move || loop {
        if let Ok(reply) = engine(&app).call("calendar_events", json!({})) {
            if let Some(events) = reply.get("events").and_then(Value::as_array) {
                app.state::<MenuBar>().0.lock().unwrap().link_events = events.clone();
                calendar(&app);
            }
        }
        let bar = app.state::<MenuBar>();
        let mut woken = bar.2.asked.lock().unwrap();
        if !*woken {
            woken = bar.2.signal.wait_timeout(woken, Duration::from_secs(30)).unwrap().0;
        }
        *woken = false;
    });
}

/// A calendar link was added or removed, or the reminders switched: the menu follows now, not at its next look.
pub fn calendar_changed(app: &AppHandle) {
    let bar = app.state::<MenuBar>();
    bar.0.lock().unwrap().lists_loaded = None; // the reminders switch is one of the settings
    *bar.2.asked.lock().unwrap() = true;
    bar.2.signal.notify_one();
    wake(app);
}

/// Settings ▸ Calendar and the setup card look at the calendar: on a Mac the agent checks the permissions and the
/// Mac's accounts again and writes them down for the engine (calendar-mac.json); this waits a moment for that.
/// Nothing to wait for without an agent (a --library window, Windows).
#[cfg(not(windows))]
pub fn calendar_check(app: &AppHandle) {
    let before = {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        let Some(stdin) = st.agent_in.as_mut() else { return };
        if writeln!(stdin, "calendar check").is_err() {
            return;
        }
        st.calendar_answers
    };
    for _ in 0..40 {
        std::thread::sleep(Duration::from_millis(50));
        if app.state::<MenuBar>().0.lock().unwrap().calendar_answers != before {
            return;
        }
    }
}

#[cfg(windows)]
pub fn calendar_check(_app: &AppHandle) {}

/// "Allow…" in Settings ▸ Calendar: macOS asks for the calendar ("calendar") or for notifications ("notifications")
/// in its own dialog, and the windows look again once it has been answered. The calendar is the agent's (it reports
/// "calendar-state …"); notifications are asked for by Stenny itself (macos_notifications.rs): macOS turns the agent
/// down (#60).
pub fn calendar_allow(app: &AppHandle, what: &str) {
    #[cfg(target_os = "macos")]
    if what == "notifications" {
        crate::macos_notifications::request(app);
        return;
    }
    #[cfg(not(windows))]
    tell_agent(app, "calendar allow");
    #[cfg(windows)]
    let _ = (app, what);
}

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    {
        let bar = app.state::<MenuBar>();
        bar.0.lock().unwrap().prefs = load_prefs(app);
    }
    let menu = Menu::with_items(app, &[&MenuItem::with_id(app, "status", tr!("menu.status.starting"), false, None::<&str>)?])?;
    TrayIconBuilder::with_id(TRAY)
        .icon(icon("idle"))
        .icon_as_template(true)
        .tooltip("Stenny")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| on_menu(app, event.id.as_ref()))
        .build(app)?;
    #[cfg(target_os = "macos")]
    {
        crate::macos::watch_menus();
        if let Some(tray) = app.tray_by_id(TRAY) {
            let _ = tray.with_inner_tray_icon(|t| {
                if let Some(item) = t.ns_status_item() {
                    crate::macos::monospaced_clock(&item);
                }
            });
        }
    }
    start_polling(app.clone());
    start_agent(app);
    poll_calendar_links(app.clone());
    Ok(())
}

// Settings (desktop/src/lib/Settings.svelte, inside the library window): the microphone for the next recording (the same choice as the menu's),
// opening at login and a new pause shortcut, which the agent registers when it starts.

/// The microphone the next recording uses (None: the system's input device).
pub fn microphone_pref(app: &AppHandle) -> Option<String> {
    load_prefs(app).microphone
}

/// Also switches a running recording to it, like the menu.
pub fn set_microphone_pref(app: &AppHandle, device: Option<String>) {
    let prefs = {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        st.prefs = Prefs { microphone: device.clone(), ..load_prefs(app) };
        st.prefs.clone()
    };
    save_prefs(app, &prefs);
    choose_microphone(app, device);
}

/// Sends the agent a line and waits a moment for its answer about opening at login: "on", "off" or "approval"
/// (the user still has to allow it in System Settings). None: no agent (a --library window) or no answer.
#[cfg(not(windows))]
pub fn login_item(app: &AppHandle, command: &str) -> Option<String> {
    {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        st.login = None;
        let stdin = st.agent_in.as_mut()?;
        writeln!(stdin, "{command}").ok()?;
        writeln!(stdin, "login status").ok()?;
    }
    for _ in 0..40 {
        std::thread::sleep(Duration::from_millis(50));
        if let Some(state) = app.state::<MenuBar>().0.lock().unwrap().login.clone() {
            return Some(state);
        }
    }
    None
}

/// Windows: the Run value in the registry (windows_agent.rs), "on" or "off". None in a --library window: opening at
/// login is the installed Stenny's business.
#[cfg(windows)]
pub fn login_item(app: &AppHandle, command: &str) -> Option<String> {
    app.tray_by_id(TRAY)?;
    match crate::windows_agent::login_item(command) {
        Ok(state) => {
            if command == "login" {
                remember_login_item(app);
            }
            Some(state)
        }
        Err(e) => {
            alert(app, "Stenny", &e);
            crate::windows_agent::login_item("login status").ok()
        }
    }
}

/// The pause shortcut as registered, for Settings: on Windows "Strg+Umschalt+Ü" (or the key right of P on a keyboard
/// without Ü); None on a Mac, where Settings writes it from the config itself.
pub fn pause_label(app: &AppHandle) -> Option<String> {
    app.state::<MenuBar>().0.lock().unwrap().pause_label.clone()
}

/// Windows: the pause shortcut registered again from the config (windows_agent.rs). Its problem, if it couldn't be
/// (another app has it). Nothing to do in a --library window: the shortcut is the installed Stenny's.
#[cfg(windows)]
pub fn apply_shortcut(app: &AppHandle) -> Option<String> {
    app.tray_by_id(TRAY)?;
    register_shortcut(app)
}

/// Windows: after the language changed, the pause shortcut's label in it ("Strg+Umschalt+Ü" / "Ctrl+Shift+Ü"):
/// registered again in the app's own thread (windows_agent.rs), no new process, window or notification-area icon.
#[cfg(windows)]
pub fn set_agent_language(app: &AppHandle) {
    let _ = apply_shortcut(app);
}

/// A line to the running agent. False if there's none (a --library window) or it's gone.
#[cfg(not(windows))]
fn tell_agent(app: &AppHandle, line: &str) -> bool {
    let bar = app.state::<MenuBar>();
    let mut st = bar.0.lock().unwrap();
    st.agent_in.as_mut().is_some_and(|stdin| writeln!(stdin, "{line}").is_ok())
}

/// After the language changed: the agent's messages follow ("lang de"). Never by starting it
/// again: a second process with Stenny's bundle id shows up in the Dock as another Stenny (#46).
#[cfg(not(windows))]
pub fn set_agent_language(app: &AppHandle) {
    tell_agent(app, &format!("lang {}", i18n::language()));
}

/// After the pause shortcut changed in the config: the running agent registers it in place of the old one (not a
/// new agent, see set_agent_language). Returns its problem, if the new shortcut couldn't be registered (another app
/// has it). Nothing to do without an agent (a --library window).
#[cfg(not(windows))]
pub fn apply_shortcut(app: &AppHandle) -> Option<String> {
    let settings = engine(app).call("settings", json!({})).unwrap_or(Value::Null);
    let shortcut = settings.get("pause_shortcut").and_then(Value::as_str).unwrap_or("cmd+shift+ü").to_string();
    let before = {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        st.agent_in.as_ref()?;
        st.pause_shortcut = shortcut.clone();
        st.shortcut_answers
    };
    if !tell_agent(app, &format!("shortcut {shortcut}")) {
        return restart_agent(app); // the agent died: a new one, as at start
    }
    for _ in 0..40 {
        std::thread::sleep(Duration::from_millis(50));
        let bar = app.state::<MenuBar>();
        let st = bar.0.lock().unwrap();
        if st.shortcut_answers != before {
            return st.shortcut_problem.clone();
        }
    }
    None
}

/// The agent started again, which reads the shortcut from the config: only when the old one is gone, since the new
/// one appears in the Dock for a moment. Its problem, if the shortcut couldn't be registered.
#[cfg(not(windows))]
fn restart_agent(app: &AppHandle) -> Option<String> {
    {
        let bar = app.state::<MenuBar>();
        let mut st = bar.0.lock().unwrap();
        st.agent_in.as_ref()?;
        st.agent_in = None; // its stdin closes: the old agent quits
        st.shortcut_problem = None;
    }
    std::thread::sleep(Duration::from_millis(400));
    start_agent(app);
    std::thread::sleep(Duration::from_millis(800));
    app.state::<MenuBar>().0.lock().unwrap().shortcut_problem.clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shortcut_as_windows_has_it() {
        let parts = |ctrl, alt, shift, key: &str| Some(ShortcutParts { ctrl, alt, shift, key: key.into() });
        assert_eq!(shortcut_parts("cmd+shift+ü"), parts(true, false, true, "ü"));
        assert_eq!(shortcut_parts("Ctrl+Opt+F5"), parts(true, true, false, "f5"));
        assert_eq!(shortcut_parts("cmd+shift++"), parts(true, false, true, "+"));
        assert_eq!(shortcut_parts("opt+space"), parts(false, true, false, "space"));
        assert_eq!(shortcut_parts("shift+p"), None); // needs Ctrl or Alt too, as config.py says
        assert_eq!(shortcut_parts("hyper+p"), None);
        assert_eq!(shortcut_parts("p"), None);
    }

    #[test]
    fn notification_area_icon_is_square_and_white_on_dark() {
        // 1×2: black and opaque at the top, clear below
        let tall = Image::new_owned(vec![0, 0, 0, 255, 0, 0, 0, 0], 1, 2);
        let white = notification_area_icon(&tall, true);
        assert_eq!((white.width(), white.height()), (2, 2));
        assert_eq!(&white.rgba()[..4], &[255, 255, 255, 255]);
        assert_eq!(&notification_area_icon(&tall, false).rgba()[..4], &[0, 0, 0, 255]);
        let wide = Image::new_owned(vec![0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255], 3, 1);
        let square = notification_area_icon(&wide, false);
        assert_eq!((square.width(), square.height()), (3, 3));
        assert_eq!(square.rgba()[(3 + 1) * 4 + 3], 255); // the middle row
        assert_eq!(square.rgba()[3], 0);
    }
}
