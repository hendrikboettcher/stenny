//! The menu bar's native details that Tauri doesn't expose.
//!
//! - The pause shortcut next to Pause/Resume, as the character printed on the key (⇧⌘Ü). Tauri only passes
//!   physical accelerators to the menu, and macOS's automatic key-equivalent localization turns those into
//!   another key on a German keyboard (⇧⌘Ö). Tauri doesn't hand out its NSMenu either, so the shortcut is set on
//!   the NSMenuItem when the menu opens, with that localization off.
//! - The clock next to the icon in a font with equal-width digits, so the icon doesn't move while it counts
//!   (the Swift app drew both with monospacedDigitSystemFont, app/Sources/Stenny/MenuBarImage.swift).

use objc2::rc::Retained;
use objc2_app_kit::{
    NSEventModifierFlags, NSFont, NSFontWeightRegular, NSMenu, NSMenuDidBeginTrackingNotification, NSStatusItem,
};
use objc2_foundation::{MainThreadMarker, NSNotification, NSNotificationCenter, NSString};
use std::ptr::NonNull;
use std::sync::Mutex;

/// The menu items that show the pause shortcut, by title.
const PAUSE_TITLES: [&str; 2] = ["Pause", "Resume"];

/// The pause shortcut's key as typed (lowercase, "ü") and its modifiers; None: no shortcut to show.
static PAUSE_KEY: Mutex<Option<(String, NSEventModifierFlags)>> = Mutex::new(None);

/// "cmd+shift+ü" → shown as ⇧⌘Ü next to Pause/Resume. False if the key isn't a single character (F5, space):
/// those keep Tauri's physical accelerator, which macOS doesn't relabel.
pub fn set_pause_shortcut(shortcut: &str) -> bool {
    let lower = shortcut.to_lowercase();
    let parts: Vec<&str> = lower.split('+').collect();
    let Some((key, mods)) = parts.split_last() else { return false };
    if key.chars().count() != 1 {
        *PAUSE_KEY.lock().unwrap() = None;
        return false;
    }
    let mut mask = NSEventModifierFlags::empty();
    for m in mods {
        mask |= match *m {
            "cmd" => NSEventModifierFlags::Command,
            "shift" => NSEventModifierFlags::Shift,
            "ctrl" => NSEventModifierFlags::Control,
            "opt" => NSEventModifierFlags::Option,
            _ => NSEventModifierFlags::empty(),
        };
    }
    *PAUSE_KEY.lock().unwrap() = Some((key.to_string(), mask));
    true
}

/// Watches every menu opening in Stenny and puts the pause shortcut on Pause/Resume. Call once, on the main thread.
pub fn watch_menus() {
    let block = block2::RcBlock::new(|note: NonNull<NSNotification>| {
        let note = unsafe { note.as_ref() };
        let Some(menu) = note.object().and_then(|o| o.downcast::<NSMenu>().ok()) else { return };
        show_pause_shortcut(&menu);
    });
    let center = NSNotificationCenter::defaultCenter();
    let token = unsafe {
        center.addObserverForName_object_queue_usingBlock(Some(NSMenuDidBeginTrackingNotification), None, None, &block)
    };
    std::mem::forget(token); // observes for as long as Stenny runs
}

fn show_pause_shortcut(menu: &Retained<NSMenu>) {
    let key = PAUSE_KEY.lock().unwrap().clone();
    let Some((key, mask)) = key else { return };
    for item in menu.itemArray().iter() {
        if PAUSE_TITLES.contains(&item.title().to_string().as_str()) {
            item.setAllowsAutomaticKeyEquivalentLocalization(false);
            item.setKeyEquivalent(&NSString::from_str(&key));
            item.setKeyEquivalentModifierMask(mask);
        }
    }
}

/// The clock next to the icon in the menu bar's font, with digits of equal width. On the main thread.
pub fn monospaced_clock(status_item: &NSStatusItem) {
    let Some(mtm) = MainThreadMarker::new() else { return };
    if let Some(button) = status_item.button(mtm) {
        let size = NSFont::menuBarFontOfSize(0.0).pointSize();
        let font = NSFont::monospacedDigitSystemFontOfSize_weight(size, unsafe { NSFontWeightRegular });
        button.setFont(Some(&font));
    }
}
