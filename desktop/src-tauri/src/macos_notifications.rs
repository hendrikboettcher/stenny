//! Stenny's notifications on a Mac, from Stenny's own process (#60).
//!
//! macOS only lets an app's main program ask for notifications and post them: a request from stenny-agent (a second
//! program in Contents/MacOS) is turned down at once ("Notifications are not allowed for this application") and
//! without a prompt. So everything about notifications happens here, through the UserNotifications framework:
//!
//! - **Permission:** `request` shows macOS's own question (once: after an answer only System Settings can change
//!   it); `state` is what Settings ▸ Calendar shows ("allowed", "denied", "ask"). If macOS turns the request down,
//!   `problem` says why, and Settings shows it next to Allow….
//! - **"Record this lecture?":** `announce_lecture`, with one button, Start Recording (the "lecture" category, in
//!   Stenny's language: `set_language` after a change). Its click comes back to the delegate, which starts the
//!   course as the menu does (`menubar::start_from_notification`). Banners also show while Stenny is in front.
//! - **Stenny's other messages** (a recording restarted or stopped, a broken pause shortcut): `post`.
//!
//! Notifications need the app bundle: a program run outside Stenny.app (`cargo run`) has none, and the framework
//! throws when asked there. Then nothing is posted and `state` is None (Settings doesn't show the row).

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::runtime::{Bool, NSObject, NSObjectProtocol, ProtocolObject};
use objc2::{define_class, msg_send, AnyThread};
use objc2_foundation::{NSArray, NSBundle, NSDictionary, NSError, NSSet, NSString};
use objc2_user_notifications::{
    UNAuthorizationOptions, UNAuthorizationStatus, UNMutableNotificationContent, UNNotification,
    UNNotificationAction, UNNotificationActionOptions, UNNotificationCategory, UNNotificationCategoryOptions,
    UNNotificationPresentationOptions, UNNotificationRequest, UNNotificationResponse, UNNotificationSettings,
    UNUserNotificationCenter, UNUserNotificationCenterDelegate,
};
use std::ptr::NonNull;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

use crate::i18n::tr;

/// The running app, for the delegate (a click on Start Recording).
static APP: OnceLock<AppHandle> = OnceLock::new();
/// Why macOS turned the last request for notifications down (its own words), until it is asked again.
static PROBLEM: Mutex<Option<String>> = Mutex::new(None);

const LECTURE: &str = "lecture"; // the category of "Record this lecture?"
const START: &str = "start"; // its button
const COURSE: &str = "course"; // the course, in the notification's userInfo

/// Inside Stenny.app, where notifications can work.
fn available() -> bool {
    let bundle = NSBundle::mainBundle();
    bundle.bundleIdentifier().is_some() && bundle.bundlePath().to_string().ends_with(".app")
}

fn center() -> Option<Retained<UNUserNotificationCenter>> {
    available().then(UNUserNotificationCenter::currentNotificationCenter)
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "StennyNotifications"]
    struct Delegate;

    unsafe impl NSObjectProtocol for Delegate {}

    unsafe impl UNUserNotificationCenterDelegate for Delegate {
        /// Shown as a banner (with its sound) while Stenny is in front too.
        #[unsafe(method(userNotificationCenter:willPresentNotification:withCompletionHandler:))]
        fn will_present(
            &self,
            _center: &UNUserNotificationCenter,
            _notification: &UNNotification,
            handler: &block2::DynBlock<dyn Fn(UNNotificationPresentationOptions)>,
        ) {
            handler.call((UNNotificationPresentationOptions::Banner
                | UNNotificationPresentationOptions::List
                | UNNotificationPresentationOptions::Sound,));
        }

        /// "Start Recording" starts the course; clicking the notification itself or closing it does nothing:
        /// Stenny never starts by itself.
        #[unsafe(method(userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:))]
        fn did_receive(
            &self,
            _center: &UNUserNotificationCenter,
            response: &UNNotificationResponse,
            handler: &block2::DynBlock<dyn Fn()>,
        ) {
            if response.actionIdentifier().to_string() == START {
                let info = response.notification().request().content().userInfo();
                let course = info
                    .objectForKey(&NSString::from_str(COURSE))
                    .and_then(|c| c.downcast::<NSString>().ok())
                    .map(|c| c.to_string());
                if let (Some(course), Some(app)) = (course, APP.get()) {
                    crate::menubar::start_from_notification(app, &course);
                }
            }
            handler.call(());
        }
    }
);

/// Takes the clicks on Stenny's notifications and sets up Start Recording. Once, early (Stenny's setup), and only in
/// the menu bar app: a --library window next to the installed Stenny leaves its notifications to it.
pub fn install(app: &AppHandle) {
    let Some(center) = center() else { return };
    let _ = APP.set(app.clone());
    let delegate: Retained<Delegate> = unsafe { msg_send![Delegate::alloc(), init] };
    center.setDelegate(Some(ProtocolObject::from_ref(&*delegate)));
    std::mem::forget(delegate); // the center holds its delegate weakly; this one answers for as long as Stenny runs
    set_language();
}

/// Start Recording in Stenny's language (at start and after the language changed).
pub fn set_language() {
    let Some(center) = center() else { return };
    // one button only: with two, macOS folds them into an "Options" menu
    let start = UNNotificationAction::actionWithIdentifier_title_options(
        &NSString::from_str(START),
        &NSString::from_str(&tr!("note.lecture.start")),
        UNNotificationActionOptions::empty(),
    );
    let category = UNNotificationCategory::categoryWithIdentifier_actions_intentIdentifiers_options(
        &NSString::from_str(LECTURE),
        &NSArray::from_retained_slice(&[start]),
        &NSArray::new(),
        UNNotificationCategoryOptions::empty(),
    );
    center.setNotificationCategories(&NSSet::from_retained_slice(&[category]));
}

/// May Stenny show notifications: "allowed", "denied" or "ask" (macOS hasn't asked yet). None outside the app
/// bundle, or if macOS doesn't answer within two seconds. Any thread but the main one (it waits for macOS).
pub fn state() -> Option<&'static str> {
    let center = center()?;
    let (tx, rx) = std::sync::mpsc::channel();
    let block = RcBlock::new(move |settings: NonNull<UNNotificationSettings>| {
        let status = unsafe { settings.as_ref() }.authorizationStatus();
        let _ = tx.send(match status {
            UNAuthorizationStatus::NotDetermined => "ask",
            UNAuthorizationStatus::Denied => "denied",
            _ => "allowed", // also provisional and ephemeral: they show
        });
    });
    center.getNotificationSettingsWithCompletionHandler(&block);
    rx.recv_timeout(Duration::from_secs(2)).ok()
}

/// Why macOS turned the last request down, if it did.
pub fn problem() -> Option<String> {
    PROBLEM.lock().unwrap().clone()
}

/// Asks macOS for notifications in its own dialog (only while it hasn't been answered). Returns at once; when macOS
/// has answered, the windows hear "calendar" and look again (Settings ▸ Calendar), with `problem` set if it refused.
pub fn request(app: &AppHandle) {
    let Some(center) = center() else { return };
    *PROBLEM.lock().unwrap() = None;
    let app = app.clone();
    let block = RcBlock::new(move |_granted: Bool, error: *mut NSError| {
        let refused = unsafe { error.as_ref() }.map(|e| e.localizedDescription().to_string());
        if let Some(why) = &refused {
            eprintln!("notifications: macOS refused: {why}");
        }
        *PROBLEM.lock().unwrap() = refused;
        let _ = app.emit("calendar", ());
    });
    center.requestAuthorizationWithOptions_completionHandler(
        UNAuthorizationOptions::Alert | UNAuthorizationOptions::Sound,
        &block,
    );
}

/// "Stenny" / "Record this lecture?" / "<course> · <time>", with Start Recording. `id`: the lecture's, so it is
/// shown once.
pub fn announce_lecture(id: &str, course: &str, range: &str) {
    let body = format!("{}\n{course} · {range}", tr!("note.lecture.question"));
    let info = NSDictionary::<NSString, NSString>::from_retained_objects(
        &[&*NSString::from_str(COURSE)],
        &[NSString::from_str(course)],
    );
    show(id, &body, Some((LECTURE, info)));
}

/// One of Stenny's messages, as a notification.
pub fn post(message: &str) {
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    show(&format!("message-{nanos}"), message, None);
}

fn show(id: &str, body: &str, category: Option<(&str, Retained<NSDictionary<NSString, NSString>>)>) {
    let Some(center) = center() else { return };
    let content = UNMutableNotificationContent::new();
    content.setTitle(&NSString::from_str("Stenny"));
    content.setBody(&NSString::from_str(body));
    if let Some((category, info)) = category {
        content.setCategoryIdentifier(&NSString::from_str(category));
        // only strings in it, as userInfo must be (a property list)
        let info: Retained<NSDictionary> = unsafe { Retained::cast_unchecked(info) };
        unsafe { content.setUserInfo(&info) };
    }
    let request = UNNotificationRequest::requestWithIdentifier_content_trigger(&NSString::from_str(id), &content, None);
    let done = RcBlock::new(|error: *mut NSError| {
        if let Some(e) = unsafe { error.as_ref() } {
            eprintln!("notification not shown: {}", e.localizedDescription());
        }
    });
    center.addNotificationRequest_withCompletionHandler(&request, Some(&done));
}
