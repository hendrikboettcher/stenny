import Foundation

/// Stenny's own log (~/Lectures Data/_System/stenny.log). NSLog lines from this app
/// didn't show up in `log show`, so hotkey problems were impossible to diagnose.
enum AppLog {
    private static let url = StennyCLI.dataDir.appendingPathComponent("stenny.log")
    private static let queue = DispatchQueue(label: "stenny.log")
    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()

    static func write(_ message: String) {
        let line = "[\(formatter.string(from: Date()))] \(message)\n"
        queue.async {
            try? FileManager.default.createDirectory(at: StennyCLI.dataDir, withIntermediateDirectories: true)
            if let handle = try? FileHandle(forWritingTo: url) {
                handle.seekToEndOfFile()
                handle.write(line.data(using: .utf8)!)
                try? handle.close()
            } else {
                try? line.data(using: .utf8)!.write(to: url)
            }
        }
    }
}
