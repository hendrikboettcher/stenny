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
    private let libraryOnly = CommandLine.arguments.contains("--library")

    func applicationDidFinishLaunching(_ notification: Notification) {
        if libraryOnly {
            NSApp.setActivationPolicy(.regular)
            LibraryWindow.shared.show()
            return
        }
        controller = LectureController()
        LoginItem.registerOnFirstLaunch()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        libraryOnly
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        controller?.confirmQuit() ?? .terminateNow
    }
}
