import AppKit
import Carbon
import EventKit
import ServiceManagement
import UserNotifications

// stenny-agent: the macOS parts of Stenny's new app (desktop/) that Tauri doesn't have, as a helper it starts.
// One line per event on stdout, one command per line on stdin; it quits when its stdin closes (the app quit).
//
//   stenny-agent --shortcut "cmd+shift+ü" [--calendar --data-dir <Stenny's data folder, from the engine>] [--lang de]
//   stenny-agent --pdf <copy.html> [--lang de]: makes the shared PDF from the HTML copy ("pdf <path>"), then quits
//   out: "ready <key code> ⌘⇧Ü" (or "error …"); "hotkey" on every press;
//        with --calendar, every 30 s: "events [{title, start, end, range, id}, …]" (on now or within 10 min);
//        "calendar-state allowed|denied|ask allowed|denied|ask|unknown" (may Stenny read the calendar, may it send
//        notifications) whenever that changes and after "calendar check"; the same goes into calendar-mac.json in
//        the data folder, with the Mac's accounts and this term's event titles (the engine's calendars.py reads it);
//        "start <course>" when "Start Recording" is clicked in a lecture notification;
//        "quit" when macOS asks Stenny to quit and reaches this helper (see QuitForwarder)
//   in:  "notify {"id": …, "course": …, "range": …}" posts that notification ("Record this lecture?");
//        "calendar check" looks at the calendar and notification permissions and the Mac's accounts again (Settings ▸
//        Calendar opened, or the window got the focus back); "calendar allow" and "notifications allow" ask macOS
//        for them (only possible while macOS hasn't been answered yet) → "calendar-state …";
//        "lang en|de" switches this helper's texts to Stenny's new language (no restart: a new process with Stenny's
//        bundle id would show up in the Dock as a second Stenny, #46);
//        "shortcut cmd+shift+ü" registers a new pause shortcut in place of the old one → "ready …" / "error …";
//        "login" makes Stenny open at login (System Settings ▸ General ▸ Login Items) → "login ok" / "login error …";
//        "login off" stops that; "login status" → "login on", "login off" or "login approval" (still to be allowed)
//
// It runs inside Stenny.app (Contents/MacOS), so the calendar permission and the notifications are Stenny's.
// The key is given as printed on the keyboard and looked up in the current layout, since Carbon hotkeys use
// physical key codes (Ü is "[" on a US keyboard). Same rules as the Swift app's Shortcut.swift.

// Stenny's language (`--lang de` at start, "lang de" later, from the app): the few texts this helper shows itself.
// The app's own texts are in desktop/src/lib/locales; these use the same words ("Vorlesung", "Aufnahme starten").
// Only changed and read on the main thread.
var german = CommandLine.arguments.firstIndex(of: "--lang").map { $0 + 1 < CommandLine.arguments.count
    && CommandLine.arguments[$0 + 1] == "de" } ?? false

func localized(_ english: String, _ deutsch: String) -> String { german ? deutsch : english }

func say(_ line: String) {
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

/// The key code that types `char` (without modifiers) in the current keyboard layout.
func keyCode(for char: String) -> Int? {
    let sources = [TISCopyCurrentKeyboardLayoutInputSource(), TISCopyCurrentASCIICapableKeyboardLayoutInputSource()]
    for source in sources.compactMap({ $0?.takeRetainedValue() }) {
        guard let ptr = TISGetInputSourceProperty(source, kTISPropertyUnicodeKeyLayoutData) else { continue }
        let data = Unmanaged<CFData>.fromOpaque(ptr).takeUnretainedValue() as Data
        let found: Int? = data.withUnsafeBytes { raw in
            guard let layout = raw.baseAddress?.assumingMemoryBound(to: UCKeyboardLayout.self) else { return nil }
            for code in 0..<128 {
                var deadKeys: UInt32 = 0
                var chars = [UniChar](repeating: 0, count: 4)
                var length = 0
                let status = UCKeyTranslate(layout, UInt16(code), UInt16(kUCKeyActionDisplay), 0, UInt32(LMGetKbdType()),
                                            OptionBits(kUCKeyTranslateNoDeadKeysBit), &deadKeys, chars.count, &length, &chars)
                if status == noErr, length > 0, String(utf16CodeUnits: chars, count: length).lowercased() == char {
                    return code
                }
            }
            return nil
        }
        if let found { return found }
    }
    return nil
}

/// "cmd+shift+ü" → (key code, Carbon modifiers, "⌘⇧Ü"); nil if the key isn't on this keyboard.
func parse(_ text: String) -> (Int, Int, String)? {
    let parts = text.lowercased().split(separator: "+").map(String.init)
    guard let key = parts.last else { return nil }
    var carbon = 0
    var symbols = ""
    for (name, flag, symbol) in [("ctrl", controlKey, "⌃"), ("opt", optionKey, "⌥"), ("shift", shiftKey, "⇧"),
                                 ("cmd", cmdKey, "⌘")] where parts.dropLast().contains(name) {
        carbon |= flag
        symbols += symbol
    }
    if key == "space" { return (kVK_Space, carbon, symbols + "Space") }
    if key.hasPrefix("f"), let n = Int(key.dropFirst()), (1...20).contains(n) {
        let fKeys = [kVK_F1, kVK_F2, kVK_F3, kVK_F4, kVK_F5, kVK_F6, kVK_F7, kVK_F8, kVK_F9, kVK_F10,
                     kVK_F11, kVK_F12, kVK_F13, kVK_F14, kVK_F15, kVK_F16, kVK_F17, kVK_F18, kVK_F19, kVK_F20]
        return (fKeys[n - 1], carbon, symbols + "F\(n)")
    }
    if let code = keyCode(for: key) { return (code, carbon, symbols + key.uppercased()) }
    if key == "ü" { return (kVK_ANSI_LeftBracket, carbon, symbols + "Ü") }  // the default on a keyboard without Ü
    return nil
}

let args = CommandLine.arguments

// A one-off job instead of the helper's usual work (PDFExport.swift).
if let p = args.firstIndex(of: "--pdf"), p + 1 < args.count {
    // Never in the Dock. Stenny.app's Info.plist (LSUIElement), which this helper reads too, already sees to that;
    // this is the second guard (#52)
    NSApplication.shared.setActivationPolicy(.prohibited)
    let result = MainActor.assumeIsolated { makePDF(from: URL(fileURLWithPath: args[p + 1])) }
    switch result {
    case .success(let pdf): say("pdf " + pdf.path)
    case .failure(let failure): say("error " + failure.message)
    }
    exit(0)
}

guard let i = args.firstIndex(of: "--shortcut"), i + 1 < args.count else {
    say("error usage: stenny-agent --shortcut \"cmd+shift+ü\"")
    exit(2)
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)  // never in the Dock, a second guard after LSUIElement (see --pdf above)

// MARK: Calendar and the lecture notification (as in the Swift app's CalendarWatcher and LectureController)

let lead: TimeInterval = 10 * 60          // offer a lecture this long before it starts, and until it ends
let minimumLength: TimeInterval = 20 * 60 // shorter events (deadlines, reminders) are never lectures
var store = EKEventStore()                // a new one once access is given: the old one stays empty

func lectureEvents(now: Date = Date()) -> [EKEvent] {
    let predicate = store.predicateForEvents(withStart: now.addingTimeInterval(-12 * 3600),
                                             end: now.addingTimeInterval(lead), calendars: nil)
    return store.events(matching: predicate)
        .filter { !$0.isAllDay && $0.endDate.timeIntervalSince($0.startDate) >= minimumLength }
        .filter { $0.endDate > now && $0.startDate <= now.addingTimeInterval(lead) }
        .sorted { $0.startDate < $1.startDate }
}

func sayEvents() {
    let f = DateFormatter()
    f.dateFormat = "HH:mm"
    let rows: [[String: Any]] = lectureEvents().compactMap { e in
        guard let title = e.title else { return nil }
        return ["title": title, "start": e.startDate.timeIntervalSince1970, "end": e.endDate.timeIntervalSince1970,
                "range": "\(f.string(from: e.startDate))–\(f.string(from: e.endDate))",
                "id": e.eventIdentifier ?? "\(title)\(e.startDate.timeIntervalSince1970)"]
    }
    if let data = try? JSONSerialization.data(withJSONObject: rows), let text = String(data: data, encoding: .utf8) {
        say("events " + text)
    }
}

/// The Mac's calendar accounts (iCloud, Google, Exchange …), each with how many calendars it has and this term's
/// event titles, so Settings ▸ Calendar can say what Stenny sees and how many lectures match the courses, and setup
/// can suggest the courses (the engine puts calendar-titles.json together from this).
func termSources() -> [[String: Any]] {
    let now = Date()
    var bySource: [String: [EKCalendar]] = [:]
    for calendar in store.calendars(for: .event) {
        guard let source = calendar.source, source.sourceType != .birthdays else { continue }
        bySource[source.sourceIdentifier, default: []].append(calendar)
    }
    let rows: [[String: Any]] = bySource.values.compactMap { calendars in
        guard let name = calendars.first?.source?.title, !name.isEmpty else { return nil }
        let predicate = store.predicateForEvents(withStart: now.addingTimeInterval(-28 * 86400),
                                                 end: now.addingTimeInterval(56 * 86400), calendars: calendars)
        var counts: [String: Int] = [:]
        store.events(matching: predicate)
            .filter { !$0.isAllDay && $0.endDate.timeIntervalSince($0.startDate) >= minimumLength }
            .compactMap(\.title).forEach { counts[$0, default: 0] += 1 }
        let titles = counts.sorted { ($0.value, $1.key) > ($1.value, $0.key) }
            .map { ["title": $0.key, "count": $0.value] as [String: Any] }
        return ["name": name, "calendars": calendars.count, "titles": titles]
    }
    return rows.sorted { ($0["name"] as? String ?? "") < ($1["name"] as? String ?? "") }
}

// What Settings ▸ Calendar shows about this Mac. Only changed and read on the main thread.
let dataDir = args.firstIndex(of: "--data-dir").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
var calendarAccess = ""            // "allowed", "denied" (also: only allowed to add events) or "ask" (not asked yet)
var notificationAccess = "unknown" // the same three; "unknown" outside the app bundle
var sources: [[String: Any]] = []
var sourcesStale = true            // the Mac's calendars changed (an account added or removed): read them again
var lastNote = ""

func accessNow() -> String {
    switch EKEventStore.authorizationStatus(for: .event) {
    case .fullAccess: return "allowed"
    case .notDetermined: return "ask"
    default: return "denied"
    }
}

/// calendar-mac.json in Stenny's data folder, written when something in it changed.
func writeNote() {
    guard let dataDir else { return }
    let note: [String: Any] = ["access": calendarAccess, "notifications": notificationAccess, "sources": sources]
    guard let data = try? JSONSerialization.data(withJSONObject: note, options: [.prettyPrinted, .sortedKeys]),
          let text = String(data: data, encoding: .utf8), text != lastNote else { return }
    try? FileManager.default.createDirectory(atPath: dataDir, withIntermediateDirectories: true)
    if (try? data.write(to: URL(fileURLWithPath: dataDir).appendingPathComponent("calendar-mac.json"), options: .atomic)) != nil {
        lastNote = text
    }
}

/// May Stenny send notifications: "allowed", "denied" or "ask" (not asked yet); "unknown" outside the app bundle,
/// which notifications need around this helper. macOS answers on a queue of its own, so waiting here is safe.
func notificationsNow() -> String {
    guard Bundle.main.bundleIdentifier != nil else { return "unknown" }
    final class Answer: @unchecked Sendable { var state = "unknown" }
    let answer = Answer()
    let done = DispatchSemaphore(value: 0)
    UNUserNotificationCenter.current().getNotificationSettings { settings in
        switch settings.authorizationStatus {
        case .notDetermined: answer.state = "ask"
        case .denied: answer.state = "denied"
        default: answer.state = "allowed"
        }
        done.signal()
    }
    return done.wait(timeout: .now() + 2) == .success ? answer.state : "unknown"
}

/// Looks at both permissions again (they can change in System Settings at any time) and, with access, at the
/// events. `report`: say "calendar-state …" even if nothing changed (the app asked). On the main thread.
func checkCalendar(report: Bool) {
    let access = accessNow()
    let notifications = notificationsNow()
    let changed = access != calendarAccess || notifications != notificationAccess
    if access == "allowed" && calendarAccess != "allowed" {
        store = EKEventStore()
        sourcesStale = true
    }
    calendarAccess = access
    notificationAccess = notifications
    if access != "allowed" {
        sources = []
    } else if sourcesStale || report {
        sources = termSources()
        sourcesStale = false
    }
    writeNote()
    if changed || report { say("calendar-state \(access) \(notifications)") }
    if access == "allowed" {
        sayEvents()
    } else if changed {
        say("events []")  // what was offered a moment ago isn't Stenny's to see any more
    }
}

/// Asks macOS for the calendar (its own dialog, once: after an answer only System Settings can change it).
func askForCalendar(then: @escaping () -> Void) {
    guard accessNow() == "ask" else {
        then()
        return
    }
    store.requestFullAccessToEvents { _, _ in DispatchQueue.main.async { then() } }
}

/// Asks macOS for notifications, for "Record this lecture?" (its own dialog, once as well).
func askForNotifications(then: @escaping () -> Void) {
    guard Bundle.main.bundleIdentifier != nil else {
        then()
        return
    }
    UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in
        DispatchQueue.main.async { then() }
    }
}

final class Notifications: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions { [.banner, .sound] }

    /// "Start Recording" starts it; closing it does nothing.
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        if response.actionIdentifier == "start", let course = response.notification.request.content.userInfo["course"] as? String {
            say("start " + course)
        }
    }
}
let notifications = Notifications()

/// Laid out like the website's mock-up: "Stenny" / "Record this lecture?" / "<course> · <time>".
func postLecture(_ json: String) {
    guard Bundle.main.bundleIdentifier != nil,  // notifications need the app bundle around this helper
          let data = json.data(using: .utf8),
          let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let id = info["id"] as? String, let course = info["course"] as? String else { return }
    let content = UNMutableNotificationContent()
    content.title = "Stenny"
    content.body = localized("Record this lecture?", "Diese Vorlesung aufnehmen?") + "\n\(course) · \(info["range"] as? String ?? "")"
    content.categoryIdentifier = "lecture"
    content.userInfo = ["course": course]
    UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content, trigger: nil))
}

/// The lecture notification's button, in Stenny's language (set again when that changes).
func setLectureCategory() {
    guard args.contains("--calendar"), Bundle.main.bundleIdentifier != nil else { return }
    // one action only: with two, macOS folds them into an "Options" menu
    let start = UNNotificationAction(identifier: "start", title: localized("Start Recording", "Aufnahme starten"), options: [])
    UNUserNotificationCenter.current().setNotificationCategories(
        [UNNotificationCategory(identifier: "lecture", actions: [start], intentIdentifiers: [])])
}

if args.contains("--calendar") {
    if Bundle.main.bundleIdentifier != nil {
        UNUserNotificationCenter.current().delegate = notifications
        setLectureCategory()
    }
    NotificationCenter.default.addObserver(forName: .EKEventStoreChanged, object: nil, queue: .main) { _ in
        sourcesStale = true
    }
    // how things are now, and again every 30 s: the permissions can change in System Settings at any time
    checkCalendar(report: true)
    let timer = Timer(timeInterval: 30, repeats: true) { _ in checkCalendar(report: false) }
    RunLoop.main.add(timer, forMode: .common)
    // the first time, macOS asks for the calendar, then for notifications (a calendar by link needs those too)
    askForCalendar {
        checkCalendar(report: true)
        askForNotifications { checkCalendar(report: true) }
    }
}

// Commands from the app; quit with it when its end of our stdin closes.
Thread.detachNewThread {
    while let line = readLine() {
        if line == "login" {
            do {
                try SMAppService.mainApp.register()  // the app around this helper
                say("login ok")
            } catch {
                say("login error \(error.localizedDescription)")
            }
        } else if line == "login off" {
            try? SMAppService.mainApp.unregister()
        } else if line == "login status" {
            switch SMAppService.mainApp.status {
            case .enabled: say("login on")
            case .requiresApproval: say("login approval")
            default: say("login off")
            }
        } else if line.hasPrefix("notify ") {
            let json = String(line.dropFirst("notify ".count))
            DispatchQueue.main.async { postLecture(json) }
        } else if line == "calendar check" {
            DispatchQueue.main.async {
                sourcesStale = true
                checkCalendar(report: true)
            }
        } else if line == "calendar allow" {
            DispatchQueue.main.async { askForCalendar { checkCalendar(report: true) } }
        } else if line == "notifications allow" {
            DispatchQueue.main.async { askForNotifications { checkCalendar(report: true) } }
        } else if line.hasPrefix("lang ") {
            let lang = String(line.dropFirst("lang ".count))
            DispatchQueue.main.async {
                german = lang == "de"
                setLectureCategory()
            }
        } else if line.hasPrefix("shortcut ") {
            let shortcut = String(line.dropFirst("shortcut ".count))
            DispatchQueue.main.async { register(shortcut) }
        }
    }
    exit(0)
}

// MARK: Quitting

/// To macOS this helper is Stenny too (eu.stenny.Stenny, inside Stenny.app), so "quit Stenny" (osascript, an
/// installer) can reach it instead of the app. It passes that on: the app quits as from its menu, this helper with it.
final class QuitForwarder: NSObject {
    @objc func handleQuit(_ event: NSAppleEventDescriptor, withReply reply: NSAppleEventDescriptor) {
        say("quit")
    }
}
let quitForwarder = QuitForwarder()
DispatchQueue.main.async {  // once app.run() has launched: AppKit sets up its own handler while launching
    NSAppleEventManager.shared().setEventHandler(
        quitForwarder, andSelector: #selector(QuitForwarder.handleQuit(_:withReply:)),
        forEventClass: AEEventClass(kCoreEventClass), andEventID: AEEventID(kAEQuitApplication))
}

// MARK: The pause shortcut

var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
    say("hotkey")
    return noErr
}, 1, &eventType, nil, nil)
var hotKey: EventHotKeyRef?

/// Registers `shortcut` as the pause shortcut in place of the one before (at start, and on "shortcut …" from the
/// app): "ready <key code> <label>" or "error <why>". Without a working one there's no shortcut, but the helper
/// stays (the calendar, opening at login). On the main thread.
func register(_ shortcut: String) {
    if let old = hotKey {
        UnregisterEventHotKey(old)
        hotKey = nil
    }
    guard let (code, modifiers, label) = parse(shortcut) else {
        say("error " + localized("The key of the pause shortcut \(shortcut) isn't on this keyboard.",
                                 "Die Taste für den Kurzbefehl \(shortcut) gibt es auf dieser Tastatur nicht."))
        return
    }
    let id = EventHotKeyID(signature: OSType(0x53544E59) /* "STNY" */, id: 1)
    let status = RegisterEventHotKey(UInt32(code), UInt32(modifiers), id, GetApplicationEventTarget(), 0, &hotKey)
    say(status == noErr ? "ready \(code) \(label)"
        : "error " + localized("\(label) is taken by another app (status \(status)).",
                               "\(label) wird bereits von einer anderen App verwendet (Status \(status))."))
}

register(args[i + 1])
app.run()
