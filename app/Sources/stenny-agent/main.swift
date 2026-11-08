import AppKit
import Carbon
import EventKit
import ServiceManagement
import UserNotifications

// stenny-agent: the macOS parts of Stenny's new app (desktop/) that Tauri doesn't have, as a helper it starts.
// One line per event on stdout, one command per line on stdin; it quits when its stdin closes (the app quit).
//
//   stenny-agent --shortcut "cmd+shift+ü" [--calendar --data-dir <~/Lectures Data/_System>]
//   stenny-agent --pdf <copy.html>: makes the shared PDF from the HTML copy ("pdf <path>"), then quits
//   out: "ready <key code> ⌘⇧Ü" (or "error …"); "hotkey" on every press;
//        with --calendar, every 30 s: "events [{title, start, end, range, id}, …]" (on now or within 10 min);
//        "start <course>" when "Start Recording" is clicked in a lecture notification;
//        "quit" when macOS asks Stenny to quit and reaches this helper (see QuitForwarder)
//   in:  "notify {"id": …, "course": …, "range": …}" posts that notification ("Record this lecture?");
//        "login" makes Stenny open at login (System Settings ▸ General ▸ Login Items) → "login ok" / "login error …";
//        "login off" stops that; "login status" → "login on", "login off" or "login approval" (still to be allowed)
//
// It runs inside Stenny.app (Contents/MacOS), so the calendar permission and the notifications are Stenny's.
// The key is given as printed on the keyboard and looked up in the current layout, since Carbon hotkeys use
// physical key codes (Ü is "[" on a US keyboard). Same rules as the Swift app's Shortcut.swift.

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
    _ = NSApplication.shared
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
app.setActivationPolicy(.prohibited)

// MARK: Calendar and the lecture notification (as in the Swift app's CalendarWatcher and LectureController)

let lead: TimeInterval = 10 * 60          // offer a lecture this long before it starts, and until it ends
let minimumLength: TimeInterval = 20 * 60 // shorter events (deadlines, reminders) are never lectures
let store = EKEventStore()

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

/// This term's event titles, so setup can suggest the courses (calendar-titles.json, as the Swift app wrote it).
func exportTitles(to dir: String) {
    let now = Date()
    let predicate = store.predicateForEvents(withStart: now.addingTimeInterval(-28 * 86400),
                                             end: now.addingTimeInterval(56 * 86400), calendars: nil)
    var counts: [String: Int] = [:]
    store.events(matching: predicate)
        .filter { !$0.isAllDay && $0.endDate.timeIntervalSince($0.startDate) >= minimumLength }
        .compactMap(\.title).forEach { counts[$0, default: 0] += 1 }
    let rows = counts.sorted { ($0.value, $1.key) > ($1.value, $0.key) }.map { ["title": $0.key, "count": $0.value] as [String: Any] }
    guard let data = try? JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted]) else { return }
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    try? data.write(to: URL(fileURLWithPath: dir).appendingPathComponent("calendar-titles.json"), options: .atomic)
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
    content.body = "Record this lecture?\n\(course) · \(info["range"] as? String ?? "")"
    content.categoryIdentifier = "lecture"
    content.userInfo = ["course": course]
    UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content, trigger: nil))
}

if args.contains("--calendar") {
    let dataDir = args.firstIndex(of: "--data-dir").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
    if Bundle.main.bundleIdentifier != nil {
        let center = UNUserNotificationCenter.current()
        center.delegate = notifications
        // one action only: with two, macOS folds them into an "Options" menu
        let start = UNNotificationAction(identifier: "start", title: "Start Recording", options: [])
        center.setNotificationCategories([UNNotificationCategory(identifier: "lecture", actions: [start], intentIdentifiers: [])])
    }
    Task {
        let granted: Bool
        switch EKEventStore.authorizationStatus(for: .event) {
        case .fullAccess: granted = true
        case .notDetermined: granted = (try? await store.requestFullAccessToEvents()) ?? false
        default: granted = false
        }
        guard granted else {
            say("calendar-denied")
            return
        }
        if Bundle.main.bundleIdentifier != nil {
            _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
        }
        await MainActor.run {
            if let dataDir { exportTitles(to: dataDir) }
            sayEvents()
            let timer = Timer(timeInterval: 30, repeats: true) { _ in sayEvents() }
            RunLoop.main.add(timer, forMode: .common)
        }
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

guard let (code, modifiers, label) = parse(args[i + 1]) else {
    say("error The key of the pause shortcut \(args[i + 1]) isn't on this keyboard.")
    exit(1)
}
var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
    say("hotkey")
    return noErr
}, 1, &eventType, nil, nil)
var ref: EventHotKeyRef?
let id = EventHotKeyID(signature: OSType(0x53544E59) /* "STNY" */, id: 1)
let status = RegisterEventHotKey(UInt32(code), UInt32(modifiers), id, GetApplicationEventTarget(), 0, &ref)
say(status == noErr ? "ready \(code) \(label)" : "error \(label) is taken by another app (status \(status)).")
app.run()
