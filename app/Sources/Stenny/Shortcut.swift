import AppKit
import Carbon

/// The pause shortcut from the config (`[app] pause_shortcut`, read through `stenny app-settings`).
/// The key is given as printed on the keyboard ("ü", "p"), so it is looked up in the current keyboard
/// layout: Carbon hotkeys use physical key codes, which differ between layouts.
struct Shortcut {
    let keyCode: Int
    let carbonModifiers: Int
    let keyEquivalent: String
    let modifierMask: NSEvent.ModifierFlags
    /// "⌘⇧Ü", for the log and messages
    let text: String

    /// ⌘⇧Ü: Ü is the key right of P on German keyboards (kVK_ANSI_LeftBracket, "[" on US keyboards).
    static let standard = Shortcut(keyCode: kVK_ANSI_LeftBracket, carbonModifiers: cmdKey | shiftKey,
                                   keyEquivalent: "ü", modifierMask: [.command, .shift], text: "⌘⇧Ü")

    private init(keyCode: Int, carbonModifiers: Int, keyEquivalent: String, modifierMask: NSEvent.ModifierFlags,
                 text: String) {
        self.keyCode = keyCode
        self.carbonModifiers = carbonModifiers
        self.keyEquivalent = keyEquivalent
        self.modifierMask = modifierMask
        self.text = text
    }

    /// From `{"modifiers": ["cmd", "shift"], "key": "ü"}` (already checked by `stenny`); nil if the key
    /// isn't on the current keyboard layout.
    init?(modifiers: [String], key: String) {
        var carbon = 0
        var mask: NSEvent.ModifierFlags = []
        var symbols = ""
        // Apple's order: ⌃⌥⇧⌘
        for (name, flag, carbonFlag, symbol) in [("ctrl", NSEvent.ModifierFlags.control, controlKey, "⌃"),
                                                 ("opt", .option, optionKey, "⌥"),
                                                 ("shift", .shift, shiftKey, "⇧"),
                                                 ("cmd", .command, cmdKey, "⌘")] where modifiers.contains(name) {
            carbon |= carbonFlag
            mask.insert(flag)
            symbols += symbol
        }
        let code: Int
        let equivalent: String
        let label: String
        if key == "space" {
            (code, equivalent, label) = (kVK_Space, " ", "Space")
        } else if key.hasPrefix("f"), let n = Int(key.dropFirst()), (1...20).contains(n) {
            let fKeys = [kVK_F1, kVK_F2, kVK_F3, kVK_F4, kVK_F5, kVK_F6, kVK_F7, kVK_F8, kVK_F9, kVK_F10,
                         kVK_F11, kVK_F12, kVK_F13, kVK_F14, kVK_F15, kVK_F16, kVK_F17, kVK_F18, kVK_F19, kVK_F20]
            (code, equivalent, label) = (fKeys[n - 1], String(UnicodeScalar(NSF1FunctionKey + n - 1)!), "F\(n)")
        } else if let found = Shortcut.keyCode(for: key) {
            (code, equivalent, label) = (found, key, key.uppercased())
        } else if key == "ü" {
            // the default on a keyboard without Ü: same physical key as on a German one
            (code, equivalent, label) = (kVK_ANSI_LeftBracket, key, key.uppercased())
        } else {
            return nil
        }
        self.init(keyCode: code, carbonModifiers: carbon, keyEquivalent: equivalent, modifierMask: mask,
                  text: symbols + label)
    }

    /// The key code that types `char` (without modifiers) in the current keyboard layout.
    private static func keyCode(for char: String) -> Int? {
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
                    let status = UCKeyTranslate(layout, UInt16(code), UInt16(kUCKeyActionDisplay), 0,
                                                UInt32(LMGetKbdType()), OptionBits(kUCKeyTranslateNoDeadKeysBit),
                                                &deadKeys, chars.count, &length, &chars)
                    if status == noErr, length > 0,
                       String(utf16CodeUnits: chars, count: length).lowercased() == char {
                        return code
                    }
                }
                return nil
            }
            if let found { return found }
        }
        return nil
    }
}
