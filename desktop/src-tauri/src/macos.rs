//! The menu bar's native details that Tauri doesn't expose.
//!
//! - The pause shortcut next to Pause/Resume, as the character printed on the key (⇧⌘Ü). Tauri only passes
//!   physical accelerators to the menu, and macOS's automatic key-equivalent localization turns those into
//!   another key on a German keyboard (⇧⌘Ö). Tauri doesn't hand out its NSMenu either, so the shortcut is set on
//!   the NSMenuItem when the menu opens, with that localization off.
//! - The clock next to the icon in a font with equal-width digits, so the icon doesn't move while it counts
//!   (the Swift app drew both with monospacedDigitSystemFont, app/Sources/Stenny/MenuBarImage.swift).
//! - Quitting when macOS asks (`osascript -e 'quit app "Stenny"'`, an installer, logging out). AppKit's own
//!   handler for that Apple Event never ends this menu bar app, so Stenny answers it itself and quits the
//!   same way as its Quit menu item: right away, or after asking while a lecture records.

use objc2::rc::Retained;
use objc2_app_kit::{
    NSEventModifierFlags, NSFont, NSFontWeightRegular, NSMenu, NSMenuDidBeginTrackingNotification, NSStatusItem,
};
use objc2_foundation::{MainThreadMarker, NSNotification, NSNotificationCenter, NSString};
use objc2::runtime::{AnyClass, AnyObject, NSObject};
use objc2::{define_class, msg_send, sel, AnyThread};
use std::ptr::NonNull;
use std::sync::{Mutex, OnceLock};

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

/// What to do when macOS asks Stenny to quit.
static ON_QUIT: OnceLock<Box<dyn Fn() + Send + Sync>> = OnceLock::new();

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "StennyQuitHandler"]
    struct QuitHandler;

    impl QuitHandler {
        #[unsafe(method(handleQuit:withReply:))]
        fn handle_quit(&self, _event: *mut AnyObject, _reply: *mut AnyObject) {
            if let Some(on_quit) = ON_QUIT.get() {
                on_quit();
            }
        }
    }
);

/// Answers macOS's "quit" Apple Event with `on_quit`, in place of AppKit's handler. Call once, on the main
/// thread, after the app has finished launching (AppKit installs its own handler while launching).
pub fn handle_quit_event(on_quit: impl Fn() + Send + Sync + 'static) {
    if ON_QUIT.set(Box::new(on_quit)).is_err() {
        return;
    }
    let Some(manager_class) = AnyClass::get(c"NSAppleEventManager") else { return };
    let handler: Retained<QuitHandler> = unsafe { msg_send![QuitHandler::alloc(), init] };
    let manager: *mut AnyObject = unsafe { msg_send![manager_class, sharedAppleEventManager] };
    let (core_events, quit) = (u32::from_be_bytes(*b"aevt"), u32::from_be_bytes(*b"quit"));
    unsafe {
        let _: () = msg_send![manager, setEventHandler: &*handler, andSelector: sel!(handleQuit:withReply:),
                              forEventClass: core_events, andEventID: quit];
    }
    std::mem::forget(handler); // the manager doesn't retain it; it answers for as long as Stenny runs
}

/// True when macOS opened Stenny as a login item: the launch's Apple Event says so (keyAEPropData =
/// keyAELaunchedAsLoginItem). Ask while launching, on the main thread; afterwards there's no launch event to read.
pub fn launched_at_login() -> bool {
    let Some(manager_class) = AnyClass::get(c"NSAppleEventManager") else { return false };
    unsafe {
        let manager: *mut AnyObject = msg_send![manager_class, sharedAppleEventManager];
        let event: *mut AnyObject = msg_send![manager, currentAppleEvent];
        if event.is_null() {
            return false;
        }
        let property: *mut AnyObject = msg_send![event, paramDescriptorForKeyword: u32::from_be_bytes(*b"prdt")];
        if property.is_null() {
            return false;
        }
        let code: u32 = msg_send![property, enumCodeValue];
        code == u32::from_be_bytes(*b"lgit")
    }
}

// ---------------------------------------------------------------- Share…

thread_local! {
    /// The share menu on screen: kept until the next one, since AppKit doesn't hold on to it while it's open.
    static PICKER: std::cell::RefCell<Option<Retained<AnyObject>>> = const { std::cell::RefCell::new(None) };
}

/// The standard macOS share menu (AirDrop, Messages, Mail, Notes …) for these files, opening below the rectangle
/// (`x`, `y`, `width`, `height` in the page's CSS pixels, from its top left) of the window's page. Main thread only.
pub fn share_files(view: *mut std::ffi::c_void, paths: &[String], x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    use objc2_foundation::{NSArray, NSPoint, NSRect, NSSize, NSURL};
    let view = unsafe { (view as *mut AnyObject).as_ref() }.ok_or("No window to share from.")?;
    let urls: Vec<Retained<NSURL>> = paths.iter().map(|p| NSURL::fileURLWithPath(&NSString::from_str(p))).collect();
    let items = NSArray::from_retained_slice(&urls);
    let class = AnyClass::get(c"NSSharingServicePicker").ok_or("The share menu isn't available.")?;
    unsafe {
        let picker: *mut AnyObject = msg_send![class, alloc];
        let picker: *mut AnyObject = msg_send![picker, initWithItems: &*items];
        let picker = Retained::from_raw(picker).ok_or("The share menu couldn't be opened.")?;
        // the page's coordinates start at the top; the view's may start at the bottom
        let flipped: bool = msg_send![view, isFlipped];
        let bounds: NSRect = msg_send![view, bounds];
        let top = if flipped { y } else { bounds.size.height - y - height };
        let rect = NSRect::new(NSPoint::new(x, top), NSSize::new(width, height));
        let below: usize = if flipped { 3 } else { 1 }; // NSRectEdge: MaxY / MinY, whichever is the bottom here
        let _: () = msg_send![&*picker, showRelativeToRect: rect, ofView: view, preferredEdge: below];
        PICKER.with(|p| *p.borrow_mut() = Some(picker));
    }
    Ok(())
}
