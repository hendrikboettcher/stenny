//! What stenny-agent does on a Mac, inside the app on Windows: the pause shortcut, opening at login, the beep when
//! there's nothing to pause, and the clipboard. All through Windows' own calls (the `windows` crate Tauri already
//! uses), no helper program:
//!
//! - **Pause shortcut:** `RegisterHotKey` on a thread of its own, whose message loop gets the presses (WM_HOTKEY). The
//!   key is looked up in the current keyboard layout as printed on it (`VkKeyScanW`), like the agent does with
//!   macOS's layout: Ü is a key of its own on a German keyboard. ⌘ (`cmd` in config.toml) is Ctrl, ⌥ (`opt`) is Alt.
//!   Tauri's global-shortcut plugin takes physical key names instead (US positions), so a key given as printed would
//!   need a second lookup table that has to match the plugin's own.
//! - **Open at login:** the `Run` value in the user's registry, as Windows' own Startup apps list shows it; turned
//!   off there (Task Manager ▸ Startup apps) it counts as off. The program's path is quoted (it may be under
//!   "Program Files"); the autostart plugin writes it without quotes.
//! - **The calendar** has no Windows counterpart yet: the WinRT calendar (`AppointmentManager`) is only for apps with
//!   a package identity (MSIX), and Outlook's COM interface only reaches classic Outlook, with security prompts.
//!   Settings says so (desktop/README.md).

use crate::i18n::tr;
use std::ffi::c_void;
use std::path::PathBuf;
use std::sync::{mpsc, Mutex};
use tauri::AppHandle;
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{ERROR_SUCCESS, HANDLE, LPARAM, WIN32_ERROR, WPARAM};
use windows::Win32::System::DataExchange::{CloseClipboard, EmptyClipboard, OpenClipboard, SetClipboardData};
use windows::Win32::System::Diagnostics::Debug::MessageBeep;
use windows::Win32::Foundation::GlobalFree; // in Foundation since windows 0.62, not System::Memory
use windows::Win32::System::Memory::{GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE};
use windows::Win32::System::Ole::CF_UNICODETEXT;
use windows::Win32::System::Registry::{
    RegCloseKey, RegDeleteKeyValueW, RegGetValueW, RegOpenKeyExW, RegSetKeyValueW, HKEY, HKEY_CLASSES_ROOT,
    HKEY_CURRENT_USER, KEY_READ, REG_BINARY, REG_SZ, RRF_RT_REG_BINARY, RRF_RT_REG_DWORD, RRF_RT_REG_SZ,
};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    MapVirtualKeyW, RegisterHotKey, UnregisterHotKey, VkKeyScanW, HOT_KEY_MODIFIERS, MAPVK_VK_TO_CHAR,
    MAPVK_VSC_TO_VK, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, VK_F1, VK_SPACE,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetMessageW, PeekMessageW, PostThreadMessageW, MB_OK, MSG, PM_NOREMOVE, WM_HOTKEY, WM_QUIT,
};

// ---------------------------------------------------------------- the pause shortcut

/// The listening thread of the pause shortcut now registered: its id (to end it) and handle (to wait for it).
static LISTENER: Mutex<Option<(u32, std::thread::JoinHandle<()>)>> = Mutex::new(None);

/// The key's virtual-key code in the current keyboard layout and its label ("Ü", "F5", "Space"); None if the key
/// isn't on this keyboard. Without Ü (the default's key) it's the key right of P, as on a Mac, labelled with what's
/// printed on it ("[" on a US keyboard).
fn virtual_key(key: &str) -> Option<(u32, String)> {
    if key == "space" {
        return Some((VK_SPACE.0 as u32, tr!("settings.shortcut.space")));
    }
    if let Some(n) = key.strip_prefix('f').and_then(|n| n.parse::<u32>().ok()).filter(|n| (1..=20).contains(n)) {
        return Some((VK_F1.0 as u32 + n - 1, format!("F{n}")));
    }
    let mut chars = key.chars();
    let (Some(c), None) = (chars.next(), chars.next()) else { return None };
    let mut units = [0u16; 2];
    if c.encode_utf16(&mut units).len() == 1 {
        let scan = unsafe { VkKeyScanW(units[0]) } as u16;
        // low byte: the key; high byte: what it takes besides (1 Shift, 2 Ctrl, 4 Alt). AltGr characters don't count.
        if scan != 0xFFFF && (scan >> 8) & 0b110 == 0 {
            return Some(((scan & 0xFF) as u32, c.to_uppercase().collect()));
        }
    }
    if c == 'ü' {
        let vk = unsafe { MapVirtualKeyW(0x1A, MAPVK_VSC_TO_VK) }; // scan code 0x1A: the key right of P
        if vk != 0 {
            let printed = unsafe { MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR) } & 0xFFFF;
            let label = char::from_u32(printed).filter(|c| !c.is_control()).map_or("Ü".to_string(), |c| c.to_uppercase().collect());
            return Some((vk, label));
        }
    }
    None
}

/// Registers the pause shortcut from config.toml ("cmd+shift+ü"), replacing the one before. Its label as Windows
/// writes it ("Strg+Umschalt+Ü"), or why it couldn't be registered.
pub fn register_shortcut(app: &AppHandle, shortcut: &str) -> Result<String, String> {
    unregister_shortcut();
    let parts = crate::menubar::shortcut_parts(shortcut).ok_or_else(|| tr!("note.shortcutNoKey", shortcut = shortcut))?;
    let (vk, key_label) = virtual_key(&parts.key).ok_or_else(|| tr!("note.shortcutNoKey", shortcut = shortcut))?;
    let label = parts.label(&key_label);
    let mut modifiers = MOD_NOREPEAT;
    if parts.ctrl { modifiers = modifiers | MOD_CONTROL; }
    if parts.alt { modifiers = modifiers | MOD_ALT; }
    if parts.shift { modifiers = modifiers | MOD_SHIFT; }
    let (tx, rx) = mpsc::channel::<Result<u32, ()>>();
    let app = app.clone();
    let listener = std::thread::spawn(move || listen(app, modifiers, vk, tx));
    match rx.recv() {
        Ok(Ok(thread)) => {
            *LISTENER.lock().unwrap() = Some((thread, listener));
            Ok(label)
        }
        _ => {
            let _ = listener.join();
            Err(tr!("note.shortcutTaken", shortcut = label))
        }
    }
}

/// The shortcut's own thread: registers it, says whether that worked, then passes every press on until told to quit.
fn listen(app: AppHandle, modifiers: HOT_KEY_MODIFIERS, vk: u32, registered: mpsc::Sender<Result<u32, ()>>) {
    let mut msg = MSG::default();
    unsafe {
        // the thread's message queue, before anyone can post to it
        let _ = PeekMessageW(&mut msg, None, 0, 0, PM_NOREMOVE);
        if RegisterHotKey(None, 1, modifiers, vk).is_err() {
            let _ = registered.send(Err(()));
            return;
        }
        let _ = registered.send(Ok(GetCurrentThreadId()));
        while GetMessageW(&mut msg, None, 0, 0).0 > 0 {
            if msg.message == WM_HOTKEY {
                crate::menubar::toggle_pause(&app);
            }
        }
        let _ = UnregisterHotKey(None, 1);
    }
}

/// Ends the shortcut's thread, which unregisters it.
pub fn unregister_shortcut() {
    let listener = LISTENER.lock().unwrap().take();
    if let Some((thread, handle)) = listener {
        unsafe {
            let _ = PostThreadMessageW(thread, WM_QUIT, WPARAM(0), LPARAM(0));
        }
        let _ = handle.join();
    }
}

/// Nothing to pause: Windows' default sound, as macOS's beep.
pub fn beep() {
    unsafe {
        let _ = MessageBeep(MB_OK);
    }
}

// ---------------------------------------------------------------- open at login

fn run_key() -> PCWSTR {
    w!(r"Software\Microsoft\Windows\CurrentVersion\Run")
}

fn approved_key() -> PCWSTR {
    w!(r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run")
}

const RUN_VALUE: PCWSTR = w!("Stenny");

/// Started by Windows at login (the Run value's argument), not by the user: the library window stays closed.
pub fn launched_at_login() -> bool {
    std::env::args().any(|a| a == "--autostart")
}

/// An installed copy: under Program Files or the user's %LOCALAPPDATA%\Programs, or next to its installer's
/// uninstaller. Never a build copy; only those add themselves to the login items by default.
pub fn installed() -> bool {
    let Ok(exe) = std::env::current_exe() else { return false };
    let mut places: Vec<PathBuf> = ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"]
        .iter()
        .filter_map(std::env::var_os)
        .map(PathBuf::from)
        .collect();
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        places.push(PathBuf::from(local).join("Programs"));
    }
    places.iter().any(|p| exe.starts_with(p)) || exe.parent().is_some_and(|d| d.join("uninstall.exe").exists())
}

/// The commands stenny-agent takes on a Mac: "login", "login off" and anything else (only asks). "on" or "off".
pub fn login_item(command: &str) -> Result<String, String> {
    match command {
        "login" => enable_login()?,
        "login off" => {
            let status = unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, run_key(), RUN_VALUE) };
            if status != ERROR_SUCCESS && login_registered() {
                return Err(message(status));
            }
        }
        _ => {}
    }
    Ok(if login_registered() && !login_turned_off() { "on" } else { "off" }.to_string())
}

fn message(status: WIN32_ERROR) -> String {
    windows::core::Error::from(windows::core::HRESULT::from_win32(status.0)).message()
}

fn enable_login() -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let line: Vec<u16> = format!("\"{}\" --autostart", exe.display()).encode_utf16().chain(Some(0)).collect();
    let status = unsafe {
        RegSetKeyValueW(HKEY_CURRENT_USER, run_key(), RUN_VALUE, REG_SZ.0, Some(line.as_ptr() as *const c_void), (line.len() * 2) as u32)
    };
    if status != ERROR_SUCCESS {
        return Err(message(status));
    }
    // turned off in Task Manager before: the user wants it on now
    if login_turned_off() {
        let enabled = [2u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        unsafe {
            let _ = RegSetKeyValueW(HKEY_CURRENT_USER, approved_key(), RUN_VALUE, REG_BINARY.0,
                Some(enabled.as_ptr() as *const c_void), enabled.len() as u32);
        }
    }
    Ok(())
}

fn login_registered() -> bool {
    unsafe { RegGetValueW(HKEY_CURRENT_USER, run_key(), RUN_VALUE, RRF_RT_REG_SZ, None, None, None) == ERROR_SUCCESS }
}

/// Turned off under Task Manager ▸ Startup apps (or Settings ▸ Apps ▸ Startup): an odd first byte.
fn login_turned_off() -> bool {
    let mut data = [0u8; 12];
    let mut size = data.len() as u32;
    let status = unsafe {
        RegGetValueW(HKEY_CURRENT_USER, approved_key(), RUN_VALUE, RRF_RT_REG_BINARY, None,
            Some(data.as_mut_ptr() as *mut c_void), Some(&mut size as *mut u32))
    };
    status == ERROR_SUCCESS && size > 0 && data[0] & 1 == 1
}

// ---------------------------------------------------------------- the rest

/// The notification area is dark (Windows' dark mode, and Windows 10's default): the menu bar's black icons
/// are drawn white there.
pub fn dark_taskbar() -> bool {
    let mut light: u32 = 0;
    let mut size = std::mem::size_of::<u32>() as u32;
    let status = unsafe {
        RegGetValueW(HKEY_CURRENT_USER, w!(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"),
            w!("SystemUsesLightTheme"), RRF_RT_REG_DWORD, None, Some(&mut light as *mut u32 as *mut c_void), Some(&mut size as *mut u32))
    };
    status != ERROR_SUCCESS || light == 0
}

/// Obsidian is installed: its obsidian:// links are registered.
pub fn obsidian_installed() -> bool {
    let mut key = HKEY::default();
    let found = unsafe { RegOpenKeyExW(HKEY_CLASSES_ROOT, w!("obsidian"), None, KEY_READ, &mut key) } == ERROR_SUCCESS;
    if found {
        unsafe {
            let _ = RegCloseKey(key);
        }
    }
    found
}

/// Text to the clipboard, as pbcopy does on a Mac.
pub fn copy_text(text: &str) -> Result<(), String> {
    let wide: Vec<u16> = text.encode_utf16().chain(Some(0)).collect();
    unsafe {
        OpenClipboard(None).map_err(|e| e.message())?;
        let result = fill_clipboard(&wide);
        let _ = CloseClipboard();
        result.map_err(|e| e.message())
    }
}

/// With the clipboard open: replaces what's on it by this UTF-16 text (ending in 0).
unsafe fn fill_clipboard(wide: &[u16]) -> windows::core::Result<()> {
    unsafe {
        EmptyClipboard()?;
        let memory = GlobalAlloc(GMEM_MOVEABLE, wide.len() * 2)?;
        let target = GlobalLock(memory) as *mut u16;
        if target.is_null() {
            let _ = GlobalFree(Some(memory));
            return Err(windows::core::Error::from_thread());
        }
        std::ptr::copy_nonoverlapping(wide.as_ptr(), target, wide.len());
        let _ = GlobalUnlock(memory);
        if let Err(e) = SetClipboardData(CF_UNICODETEXT.0 as u32, Some(HANDLE(memory.0))) {
            let _ = GlobalFree(Some(memory));
            return Err(e);
        }
        Ok(()) // the clipboard owns the memory now
    }
}
