import AppKit
import Carbon

/// System-wide keyboard shortcut via the Carbon hotkey API.
/// Works in every app and needs no Accessibility permission.
///
/// It stays registered all the time. (Unregistering it while a menu is open didn't work: the re-registration
/// on "menu closed" didn't reliably happen, which left the shortcut dead after the first time the menu was
/// opened. While the menu is open, its Pause item handles the shortcut instead.)
final class GlobalHotKey {
    /// Running ID so multiple shortcuts can tell their key presses apart
    private static var nextID: UInt32 = 1

    private let id: UInt32
    private let keyCode: UInt32
    private let modifiers: UInt32
    private let action: () -> Void
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    private(set) var registered = false

    init(keyCode: Int, modifiers: Int, action: @escaping () -> Void) {
        self.id = GlobalHotKey.nextID
        GlobalHotKey.nextID += 1
        self.keyCode = UInt32(keyCode)
        self.modifiers = UInt32(modifiers)
        self.action = action

        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                      eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let event, let userData else { return OSStatus(eventNotHandledErr) }
            var pressed = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &pressed)
            let hotKey = Unmanaged<GlobalHotKey>.fromOpaque(userData).takeUnretainedValue()
            // Not our shortcut → pass it on to the next handler
            guard pressed.id == hotKey.id else { return OSStatus(eventNotHandledErr) }
            AppLog.write("hotkey pressed")
            hotKey.action()
            return noErr
        }, 1, &eventType, Unmanaged.passUnretained(self).toOpaque(), &handlerRef)

        register()
    }

    /// Registers the shortcut if it isn't (cheap to call repeatedly).
    func register() {
        guard hotKeyRef == nil else { return }
        let hotKeyID = EventHotKeyID(signature: OSType(0x4C454354) /* "LECT" */, id: id)
        let status = RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)
        registered = status == noErr
        if status == noErr {
            AppLog.write("hotkey registered")
        } else {
            AppLog.write("could not register hotkey (status \(status)); it may be used by another app")
        }
    }

    private func unregister() {
        if let hotKeyRef { UnregisterEventHotKey(hotKeyRef) }
        hotKeyRef = nil
    }

    deinit {
        unregister()
        if let handlerRef { RemoveEventHandler(handlerRef) }
    }
}
