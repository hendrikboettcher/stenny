//! The menu bar: Stenny's icon with the recording's clock next to it, and a classic macOS menu to start, pause,
//! resume and stop recordings, pick the microphone and open the library. Everything goes through the engine
//! (`stenny app-server`): `status` every second while recording, every three seconds otherwise.
//!
//! Like the Swift app it replaces (app/Sources/Stenny/LectureController.swift): it asks once per course whether
//! the lecturer agreed to being recorded; a recording that vanishes without stopping (killed, crashed) is
//! recovered (`recover`) and started again without the live draft; quitting during a recording asks first.

use crate::engine::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind, MessageDialogResult};
use tauri_plugin_notification::NotificationExt;

const TRAY: &str = "stenny";

#[derive(Serialize, Deserialize, Default, Clone)]
struct Prefs {
    microphone: Option<String>, // None: the system's input device
    no_draft: bool,             // record only, no live text
    #[serde(default)]
    login_item: bool,           // added itself to the login items once; after that it's the user's choice
}

#[derive(Default)]
struct State {
    status: Value,
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
    notified: HashSet<String>,               // calendar events already announced
    agent_in: Option<std::process::ChildStdin>,
    status_item: Option<MenuItem<Wry>>,      // the menu's first line, updated in place
    icon: String,                            // the icon shown, so it is only set when it changes (no flicker)
    pause_accel: Option<String>,             // the pause shortcut as a physical accelerator ("Cmd+F1"), if not a character
}

/// The pause shortcut for the menu by physical key (the agent tells which: "ready 122 ⌘F1"), for keys that aren't a
/// single character; those go through macos.rs. Carbon key codes → the W3C key names muda uses.
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

#[derive(Clone, Debug, PartialEq)]
struct Suggestion {
    course: String,
    range: String, // "09:40–11:10"
}

#[derive(Default)]
pub struct MenuBar(Mutex<State>);

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
    Image::from_bytes(bytes).expect("tray icon")
}

fn notify(app: &AppHandle, message: &str) {
    let _ = app.notification().builder().title("Stenny").body(message).show();
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
        "recording" if s.get("input_lost").and_then(Value::as_bool) == Some(true) => {
            "No audio from the microphone · reconnecting …".to_string()
        }
        "recording" if !str_of(s, "warning").is_empty() => str_of(s, "warning").to_string(),
        "recording" => format!("Recording · {course} · {} words", s.get("words").and_then(Value::as_u64).unwrap_or(0)),
        "paused" => format!("Paused for {} · mic off · {course}", clock(num(s, "current_pause_s"))),
        "stopping" => "Finishing the live draft …".into(),
        "finalizing" | "done" => "Making the final transcript …".into(),
        "starting" => "Starting …".into(),
        _ if st.idle_message.is_empty() => "Ready".into(),
        _ => st.idle_message.clone(),
    }
}

fn build_menu(app: &AppHandle, st: &State) -> tauri::Result<(Menu<Wry>, MenuItem<Wry>)> {
    let s = &st.status;
    let state = str_of(s, "state");
    let active = is_active(s);
    let status_item = MenuItem::with_id(app, "status", status_line(st), false, None::<&str>)?;
    let mut items: Vec<Box<dyn IsMenuItem<Wry>>> = vec![Box::new(status_item.clone()), Box::new(PredefinedMenuItem::separator(app)?)];

    if active {
        let pause = if state == "paused" { "Resume" } else { "Pause" };
        items.push(Box::new(MenuItem::with_id(app, "toggle", pause, true, st.pause_accel.as_deref())?));
        items.push(Box::new(MenuItem::with_id(app, "stop", "Stop && Make Final Transcript", true, None::<&str>)?)); // && shows one &
        items.push(Box::new(PredefinedMenuItem::separator(app)?));
    } else if state == "idle" || state.is_empty() {
        if let Some(sg) = &st.suggestion {
            items.push(Box::new(MenuItem::with_id(app, format!("start:{}", sg.course),
                format!("Start {} · {}", sg.course, sg.range), true, None::<&str>)?));
        }
        let starts: Vec<MenuItem<Wry>> = st
            .courses
            .iter()
            .map(|c| MenuItem::with_id(app, format!("start:{c}"), c, true, None::<&str>))
            .collect::<tauri::Result<_>>()?;
        let refs: Vec<&dyn IsMenuItem<Wry>> = starts.iter().map(|m| m as &dyn IsMenuItem<Wry>).collect();
        let empty = MenuItem::with_id(app, "nocourse", "No courses yet: add one in Stenny's window", false, None::<&str>)?;
        let start = if refs.is_empty() {
            Submenu::with_id_and_items(app, "start", "Start Lecture", true, &[&empty])?
        } else {
            Submenu::with_id_and_items(app, "start", "Start Lecture", true, &refs)?
        };
        items.push(Box::new(start));
        items.push(Box::new(MenuItem::with_id(app, "talk", "Record a Talk or Event…", true, None::<&str>)?));
        items.push(Box::new(PredefinedMenuItem::separator(app)?));
    }

    // the microphone: while recording the one in use (switching it switches the recording), else the next one's
    let in_use = if active { s.get("microphone").and_then(Value::as_str).map(String::from) } else { None };
    let chosen = in_use.clone().or_else(|| st.prefs.microphone.clone());
    let label = chosen.clone().unwrap_or_else(|| "System Default".into());
    let mut mics: Vec<CheckMenuItem<Wry>> = Vec::new();
    if !active {
        let default = st.default_device.as_deref().map(|d| format!("System Default ({d})")).unwrap_or("System Default".into());
        mics.push(CheckMenuItem::with_id(app, "mic:", default, true, chosen.is_none(), None::<&str>)?);
    }
    for d in &st.devices {
        mics.push(CheckMenuItem::with_id(app, format!("mic:{d}"), d, true, chosen.as_deref() == Some(d), None::<&str>)?);
    }
    let mic_refs: Vec<&dyn IsMenuItem<Wry>> = mics.iter().map(|m| m as &dyn IsMenuItem<Wry>).collect();
    let mic_enabled = state != "starting" && (!active || in_use.is_some());
    items.push(Box::new(Submenu::with_id_and_items(app, "mics", format!("Microphone: {label}"), mic_enabled, &mic_refs)?));
    if !active && state != "starting" {
        items.push(Box::new(CheckMenuItem::with_id(app, "draft", "Live Draft While Recording", true, !st.prefs.no_draft, None::<&str>)?));
    }
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    if !str_of(s, "transcript").is_empty() {
        items.push(Box::new(MenuItem::with_id(app, "transcript", "Open Transcript", true, None::<&str>)?));
    }
    items.push(Box::new(MenuItem::with_id(app, "open", "Open Stenny…", true, None::<&str>)?));
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(app, "feedback", "Send Feedback…", true, None::<&str>)?));
    items.push(Box::new(MenuItem::with_id(app, "updates", "Check for Updates…", true, None::<&str>)?));
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(app, "quit", "Quit Stenny", true, None::<&str>)?));
    let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
    Ok((Menu::with_items(app, &refs)?, status_item))
}

fn shape(st: &State) -> String {
    let s = &st.status;
    // what the menu's items depend on; the clock goes to the title and the status line is updated in place,
    // because rebuilding the menu closes it while it is open
    format!(
        "{}|{}|{}|{}|{:?}|{:?}|{:?}|{}|{:?}",
        str_of(s, "state"),
        str_of(s, "course"),
        str_of(s, "microphone"),
        str_of(s, "transcript").is_empty(),
        st.courses,
        st.devices,
        st.prefs.microphone,
        st.prefs.no_draft,
        st.suggestion,
    )
}

fn redraw(app: &AppHandle, force: bool) {
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
        "stopping" | "finalizing" | "done" => ("finishing", None),
        "starting" => ("starting", None),
        _ => ("idle", None),
    };
    if st.icon != name {
        st.icon = name.to_string();
        let _ = tray.set_icon(Some(icon(name)));
        let _ = tray.set_icon_as_template(true);
    }
    let _ = tray.set_title(title.as_deref());
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
    let devices = e.call("devices", json!({})).unwrap_or(Value::Null);
    let bar = app.state::<MenuBar>();
    let mut st = bar.0.lock().unwrap();
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

/// One status answer: notes and warnings, the "vanished recording" check, then the menu bar.
fn update(app: &AppHandle, status: Value) {
    let bar = app.state::<MenuBar>();
    let mut vanished = None;
    let mut messages = Vec::new();
    {
        let mut st = bar.0.lock().unwrap();
        let state = str_of(&status, "state").to_string();
        if let Some(error) = status.get("error").and_then(Value::as_str) {
            messages.push(format!("The recording didn't start.\n\n{error}"));
        }
        let note = str_of(&status, "note");
        if !note.is_empty() {
            st.last_note = note.to_string();
        }
        let warning = str_of(&status, "warning").to_string();
        if !warning.is_empty() && warning != st.last_warning {
            messages.push(warning.clone());
        }
        st.last_warning = warning;
        let was_session = !matches!(str_of(&st.status, "state"), "idle" | "" | "starting");
        if was_session && state == "idle" {
            let queued = status.get("queued").and_then(Value::as_u64).unwrap_or(0);
            let on_power = status.get("on_power").and_then(Value::as_bool).unwrap_or(true);
            st.idle_message = if queued > 0 && !on_power {
                "Ready · the final transcript waits until the Mac is plugged in".into()
            } else if queued > 0 {
                "Ready · the final transcript is made soon".into()
            } else if !st.last_note.is_empty() {
                format!("Ready · {}", st.last_note)
            } else {
                "Ready · last lecture saved".into()
            };
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
        st.status = status;
    }
    for m in messages {
        if m.starts_with("The recording didn't start") {
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
        notify(app, &format!("Recording {course} again after a problem. Nothing recorded is lost; the transcript comes later."));
    } else {
        notify(app, &format!("The recording of {course} stopped. What was recorded is saved; its transcript comes later."));
    }
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
        let status = engine(&app).call("status", json!({})).unwrap_or_else(|_| json!({"state": "idle"}));
        let fast = matches!(str_of(&status, "state"), "recording" | "paused" | "starting" | "stopping");
        update(&app, status);
        std::thread::sleep(Duration::from_secs(if fast { 1 } else { 3 }));
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
    let who = if event { "speaker" } else { "lecturer" };
    let (question, why, yes) = if use_ == "record" {
        (format!("Did the {who} agree to being recorded?"),
         format!("Recording {} usually needs the {who}'s OK.", if event { "a talk" } else { "a lecture" }),
         "Yes, Record")
    } else {
        (format!("Did the {who} agree to this?"),
         format!("Transcripts of {} usually need the {who}'s OK before they're passed on.", if event { "a talk" } else { "a lecture" }),
         "Yes, Share")
    };
    let answer = app
        .dialog()
        .message(format!("{why} Stenny asks once for {course}."))
        .title(question)
        .buttons(MessageDialogButtons::OkCancelCustom(yes.into(), "Not Yet".into()))
        .blocking_show_with_result();
    let agreed = matches!(&answer, MessageDialogResult::Ok) || matches!(&answer, MessageDialogResult::Custom(t) if t == yes);
    if agreed {
        let _ = e.call("give_consent", params);
    }
    agreed
}

/// Starts a recording of a course, or of a talk (`event`: its title). Runs off the main thread (dialogs).
pub fn start(app: &AppHandle, course: &str, event: bool) -> Result<(), String> {
    if !lecturer_agreed(app, "record", course, event) {
        return Ok(());
    }
    let prefs = app.state::<MenuBar>().0.lock().unwrap().prefs.clone();
    let mut params = if event { json!({"event": course}) } else { json!({"course": course}) };
    if let Some(m) = prefs.microphone {
        params["device"] = json!(m);
    }
    if prefs.no_draft {
        params["draft"] = json!(false);
    }
    // development: record an audio file instead of the microphone (with STENNY_HOME pointing at a test copy)
    if let Ok(file) = std::env::var("STENNY_TEST_FILE") {
        params["file"] = json!(file);
        params["llm"] = json!(false);
    }
    let status = engine(app).call("start", params)?;
    update(app, status);
    Ok(())
}

fn send(app: &AppHandle, cmd: &str) {
    match engine(app).call("command", json!({"cmd": cmd})) {
        Ok(status) => update(app, status),
        Err(e) => notify(app, &e),
    }
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
    let answer = app
        .dialog()
        .message("Stop it now (the final transcript is made afterwards), or keep it recording in the background? \
                  You can control it again by reopening Stenny.")
        .title("A lecture is being recorded")
        .buttons(MessageDialogButtons::YesNoCancelCustom(
            "Stop Recording & Quit".into(), "Keep Recording & Quit".into(), "Cancel".into()))
        .blocking_show_with_result();
    match answer {
        MessageDialogResult::Yes => { let _ = engine(app).call("command", json!({"cmd": "stop"})); true }
        MessageDialogResult::Custom(t) if t.starts_with("Stop") => { let _ = engine(app).call("command", json!({"cmd": "stop"})); true }
        MessageDialogResult::No => true,
        MessageDialogResult::Custom(t) if t.starts_with("Keep") => true,
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
        "talk" => crate::show_window(&app, "talk"),
        "open" => crate::show_window(&app, "library"),
        "feedback" => crate::show_window(&app, "feedback"),
        "updates" => check_updates(&app),
        "transcript" => open_transcript(&app),
        "draft" => {
            let prefs = {
                let bar = app.state::<MenuBar>();
                let mut st = bar.0.lock().unwrap();
                st.prefs.no_draft = !st.prefs.no_draft;
                st.prefs.clone()
            };
            save_prefs(&app, &prefs);
            redraw(&app, true);
        }
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

/// "Check for Updates…": whether a newer Stenny is out. Installing it comes with the app's own updater; until
/// then "Download" opens the release page.
pub fn check_updates(app: &AppHandle) {
    let result = engine(app).call("check_update", json!({}));
    let dialog = app.dialog();
    match result {
        Err(e) => {
            dialog.message(e).title("Couldn't check for updates").kind(MessageDialogKind::Warning).blocking_show();
        }
        Ok(r) if r.get("update_available").and_then(Value::as_bool) != Some(true) => {
            let current = str_of(&r, "current").to_string();
            dialog.message(format!("You have the newest version, {current}.")).title("Stenny is up to date").blocking_show();
        }
        Ok(r) => {
            let (current, latest) = (str_of(&r, "current").to_string(), str_of(&r, "latest").to_string());
            let download = dialog
                .message(format!("You have {current}."))
                .title(format!("Stenny {latest} is available"))
                .buttons(MessageDialogButtons::OkCancelCustom("Download".into(), "Not Now".into()))
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
        #[cfg(target_os = "macos")]
        let _ = std::process::Command::new("osascript").args(["-e", "beep"]).spawn(); // nothing to pause
    }
}

/// stenny-agent (app/Sources/stenny-agent): the global pause shortcut. Next to Stenny's own program in the app
/// bundle; $STENNY_AGENT or the Swift build folder during development.
pub(crate) fn agent_path() -> Option<std::path::PathBuf> {
    let candidates = [
        std::env::var("STENNY_AGENT").ok().map(std::path::PathBuf::from),
        std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join("stenny-agent"))),
        Some(std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../app/.build/release/stenny-agent"))),
    ];
    candidates.into_iter().flatten().find(|p| p.exists())
}

fn start_agent(app: &AppHandle) {
    let Some(exe) = agent_path() else {
        eprintln!("stenny-agent not found: no pause shortcut");
        return;
    };
    let app = app.clone();
    std::thread::spawn(move || {
        use std::io::BufRead;
        let shortcut = engine(&app)
            .call("settings", json!({}))
            .ok()
            .and_then(|s| s.get("pause_shortcut").and_then(Value::as_str).map(String::from))
            .unwrap_or_else(|| "cmd+shift+ü".into());
        let data_dir = engine(&app)
            .call("settings", json!({}))
            .ok()
            .and_then(|s| s.get("data_dir").and_then(Value::as_str).map(String::from))
            .unwrap_or_default();
        let child = std::process::Command::new(exe)
            .args(["--shortcut", &shortcut, "--calendar", "--data-dir", &data_dir])
            .stdin(std::process::Stdio::piped()) // closes when Stenny quits, and the agent with it
            .stdout(std::process::Stdio::piped())
            .spawn();
        let Ok(mut child) = child else { return };
        {
            let bar = app.state::<MenuBar>();
            let mut st = bar.0.lock().unwrap();
            st.agent_in = child.stdin.take(); // open while Stenny runs
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
            } else if let Some(events) = line.strip_prefix("events ") {
                calendar(&app, events);
            } else if let Some(course) = line.strip_prefix("start ") {
                let (app, course) = (app.clone(), course.to_string());
                std::thread::spawn(move || if let Err(e) = start(&app, &course, false) { alert(&app, "Stenny", &e) });
            } else if line == "login ok" {
                let prefs = {
                    let bar = app.state::<MenuBar>();
                    let mut st = bar.0.lock().unwrap();
                    st.prefs.login_item = true;
                    st.prefs.clone()
                };
                save_prefs(&app, &prefs);
            } else if let Some(problem) = line.strip_prefix("error ") {
                notify(&app, &format!("The pause shortcut doesn't work: {problem}"));
            }
        }
    });
}

/// The agent's calendar events (on now or within 10 minutes): the first that belongs to a course becomes the
/// menu's suggestion, announced once by a notification with "Start Recording" unless a recording runs.
fn calendar(app: &AppHandle, events: &str) {
    let Ok(Value::Array(events)) = serde_json::from_str::<Value>(events) else { return };
    let bar = app.state::<MenuBar>();
    let unknown: Vec<String> = {
        let st = bar.0.lock().unwrap();
        events.iter().map(|e| str_of(e, "title").to_string()).filter(|t| !st.course_for_title.contains_key(t)).collect()
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
            Some((Suggestion { course, range: str_of(e, "range").to_string() }, str_of(e, "id").to_string()))
        });
        st.suggestion = first.as_ref().map(|(s, _)| s.clone());
        let busy = !matches!(str_of(&st.status, "state"), "idle" | "");
        if let Some((sg, id)) = first {
            if !busy && st.notified.insert(id.clone()) {
                announce = Some(json!({"id": id, "course": sg.course, "range": sg.range}));
            }
        }
        if let (Some(note), Some(stdin)) = (announce.take(), st.agent_in.as_mut()) {
            let _ = writeln!(stdin, "notify {note}");
        }
    }
    redraw(app, false);
}

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    {
        let bar = app.state::<MenuBar>();
        bar.0.lock().unwrap().prefs = load_prefs(app);
    }
    let menu = Menu::with_items(app, &[&MenuItem::with_id(app, "status", "Starting …", false, None::<&str>)?])?;
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
    Ok(())
}
