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

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = LectureController()
        LoginItem.registerOnFirstLaunch()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        controller?.confirmQuit() ?? .terminateNow
    }
}
