import AppKit

// Menu bar app that controls `stenny live` (the Python recorder) over its local socket.
// No Dock icon thanks to LSUIElement in Info.plist.
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.run()
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: LectureController?
    /// `Stenny --library`: only the window, no menu bar icon, shortcut or calendar, so it can run next to
    /// the installed Stenny while working on the window.
    private var libraryOnly = CommandLine.arguments.contains("--library")

    func applicationDidFinishLaunching(_ notification: Notification) {
        // One Stenny in the menu bar: a second copy (a test build, a second install, `open -n`) only opens the
        // window. Two menu bar Stennys look alike and would both restart a recording that stopped.
        if !libraryOnly, let other = Self.otherMenuBarStenny() {
            AppLog.write("another Stenny is running (pid \(other.processIdentifier)), so this one only opens the window")
            libraryOnly = true
        }
        if libraryOnly {
            NSApp.setActivationPolicy(.regular)
            LibraryWindow.shared.show()
            return
        }
        controller = LectureController()
        LoginItem.registerOnFirstLaunch()
    }

    /// A Stenny in the menu bar that started before this one (installed or test build, bundle ID `app.stenny…`;
    /// a window-only copy shows in the Dock instead and doesn't count). Only the later one gives way, so two
    /// copies starting together still leave one icon.
    private static func otherMenuBarStenny() -> NSRunningApplication? {
        let me = NSRunningApplication.current
        return NSWorkspace.shared.runningApplications.first { app in
            guard app.processIdentifier != me.processIdentifier, !app.isTerminated else { return false }
            guard let id = app.bundleIdentifier, id == "app.stenny" || id.hasPrefix("app.stenny."),
                  app.activationPolicy == .accessory else { return false }
            guard let theirs = app.launchDate, let mine = me.launchDate else { return true }
            return theirs < mine || (theirs == mine && app.processIdentifier < me.processIdentifier)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        libraryOnly
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        controller?.confirmQuit() ?? .terminateNow
    }
}
