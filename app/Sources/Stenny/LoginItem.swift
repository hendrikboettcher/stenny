import Foundation
import ServiceManagement

/// Stenny opens at login by default, so the calendar notification works without thinking of it.
/// It adds itself once, on the first launch from an Applications folder. After that the user
/// decides in System Settings ▸ General ▸ Login Items, and Stenny never adds itself back.
enum LoginItem {
    private static let doneKey = "loginItemRegistered"

    static func registerOnFirstLaunch() {
        guard !UserDefaults.standard.bool(forKey: doneKey) else { return }
        // A build copy (app/build.noindex) must not become the login item
        let path = Bundle.main.bundlePath
        guard path.hasPrefix("/Applications/") || path.hasPrefix(NSHomeDirectory() + "/Applications/") else { return }
        do {
            try SMAppService.mainApp.register()
            UserDefaults.standard.set(true, forKey: doneKey)
            AppLog.write("login item registered")
        } catch {
            // not marked as done, so the next launch tries again
            AppLog.write("login item not registered: \(error.localizedDescription)")
        }
    }
}
