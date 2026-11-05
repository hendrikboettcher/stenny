import AppKit
import Carbon

// stenny-agent: the macOS parts of Stenny's new app (desktop/) that Tauri doesn't have, as a helper it starts:
// the global pause shortcut. One line per event on stdout; it quits when its stdin closes (the app quit).
//
//   stenny-agent --shortcut "cmd+shift+ü"   →  "ready ⌘⇧Ü" (or "error …"), then "hotkey" on every press
//
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
guard let i = args.firstIndex(of: "--shortcut"), i + 1 < args.count else {
    say("error usage: stenny-agent --shortcut \"cmd+shift+ü\"")
    exit(2)
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

// quit with the app: its end of our stdin closes
Thread.detachNewThread {
    while let _ = readLine() {}
    exit(0)
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
say(status == noErr ? "ready \(label)" : "error \(label) is taken by another app (status \(status)).")
app.run()
