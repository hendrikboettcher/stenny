import Foundation

/// Talks to the `stenny` command line tool: its control socket for a running session,
/// and the executable itself to list courses and start a recording.
enum StennyCLI {
    /// The tool's system folder (logs, control socket): ~/Lectures Data/_System, see paths.py.
    static let dataDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Lectures Data/_System")
    static let socketPath = dataDir.appendingPathComponent("live.sock").path
    static let logPath = dataDir.appendingPathComponent("live.log").path

    /// Status of the running session, or nil if none is running.
    struct Status {
        let state: String          // recording | paused | stopping | finalizing | done
        let course: String
        let elapsed: Int
        let pausedSeconds: Int
        let words: Int
        let transcript: String
        let note: String
        let levelDb: Double
        /// lecture time minus pauses and gaps: what the menu bar counter shows, frozen while paused
        var recorded: Int { max(0, elapsed - pausedSeconds) }
        let currentPause: Int
        let inputLost: Bool
        /// the running recording's main microphone (nil for a file played back in tests)
        let microphone: String?
        /// disk or memory running out; empty while all is well
        let warning: String
        var isEvent: Bool

        init?(_ json: [String: Any]) {
            guard json["ok"] as? Bool == true, let state = json["state"] as? String else { return nil }
            self.state = state
            course = json["course"] as? String ?? ""
            elapsed = Int(json["elapsed_s"] as? Double ?? 0)
            pausedSeconds = Int(json["paused_s"] as? Double ?? 0)
            words = json["words"] as? Int ?? 0
            transcript = json["transcript"] as? String ?? ""
            note = json["note"] as? String ?? ""
            levelDb = json["level_db"] as? Double ?? -120
            currentPause = Int(json["current_pause_s"] as? Double ?? 0)
            inputLost = json["input_lost"] as? Bool ?? false
            microphone = json["microphone"] as? String
            warning = json["warning"] as? String ?? ""
            isEvent = json["kind"] as? String == "event"
        }
    }

    // MARK: Control socket (one JSON line per request)

    static func send(_ cmd: String, timeout: Int = 5) -> Status? {
        request(cmd, timeout: timeout).flatMap(Status.init)
    }

    /// Switches the running recording to another microphone; nil + message if that failed.
    static func switchMicrophone(_ device: String) -> (Status?, String?) {
        guard let json = request("microphone", extra: ["device": device], timeout: 5) else {
            return (nil, "The recording didn't answer.")
        }
        return (Status(json), json["ok"] as? Bool == true ? nil : json["error"] as? String ?? "Switching failed.")
    }

    /// Current input level in dB for the level meter, or nil if not recording (cheap `level` request).
    static func level() -> (db: Double, inputLost: Bool)? {
        guard let json = request("level", timeout: 1), json["ok"] as? Bool == true,
              json["state"] as? String == "recording" else { return nil }
        return (json["level_db"] as? Double ?? -120, json["input_lost"] as? Bool ?? false)
    }

    private static func request(_ cmd: String, extra: [String: String] = [:], timeout: Int) -> [String: Any]? {
        guard FileManager.default.fileExists(atPath: socketPath) else { return nil }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var tv = timeval(tv_sec: timeout, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8)
        guard pathBytes.count < MemoryLayout.size(ofValue: addr.sun_path) else { return nil }
        withUnsafeMutableBytes(of: &addr.sun_path) { raw in
            raw.copyBytes(from: pathBytes)
            raw[pathBytes.count] = 0
        }
        let connected = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { return nil }

        var message: [String: String] = extra
        message["cmd"] = cmd
        guard let body = try? JSONSerialization.data(withJSONObject: message) else { return nil }
        let request = Array(body) + [UInt8(ascii: "\n")]
        guard write(fd, request, request.count) == request.count else { return nil }

        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        while !data.contains(UInt8(ascii: "\n")) {
            let n = read(fd, &buffer, buffer.count)
            if n <= 0 { break }
            data.append(contentsOf: buffer[0..<n])
        }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    // MARK: The executable

    /// `stenny` as installed by `uv tool install` (~/.local/bin), or found on the PATH.
    static var executable: String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = ["\(home)/.local/bin/stenny", "/opt/homebrew/bin/stenny", "/usr/local/bin/stenny"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    /// Environment for child processes: GUI apps get a minimal PATH, but ffmpeg lives in Homebrew.
    static var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        return env
    }

    /// Runs `stenny <args>` and parses its JSON output.
    private static func json(_ args: [String]) -> Any? {
        guard let exe = executable else { return nil }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        process.arguments = args
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return try? JSONSerialization.jsonObject(with: data)
    }

    /// The pause shortcut from the config (`stenny app-settings`); nil if the tool isn't there or the key
    /// isn't on this keyboard (then the default stays).
    static func pauseShortcut() -> Shortcut? {
        guard let settings = json(["app-settings"]) as? [String: Any] else { return nil }
        if let error = settings["error"] as? String { AppLog.write("config: \(error)") }
        guard let s = settings["pause_shortcut"] as? [String: Any], let key = s["key"] as? String else { return nil }
        let shortcut = Shortcut(modifiers: s["modifiers"] as? [String] ?? [], key: key)
        if shortcut == nil {
            AppLog.write("pause shortcut \(settings["pause_shortcut_text"] ?? key): key not on this keyboard, using ⌘⇧Ü")
        }
        return shortcut
    }

    static func courses() -> [String] {
        guard let list = json(["courses", "--json"]) as? [[String: Any]] else { return [] }
        return list.compactMap { $0["name"] as? String }
    }

    /// Course for each calendar event title (nil = none), decided by `stenny match-course`.
    static func matchCourses(_ titles: [String]) -> [String: String?] {
        guard let list = json(["match-course", "--json"] + titles) as? [[String: Any]] else { return [:] }
        var out: [String: String?] = [:]
        for m in list {
            if let title = m["title"] as? String { out[title] = .some(m["course"] as? String) }
        }
        return out
    }

    /// Runs `stenny <args>`: its exit status and what it printed, or nil if the tool isn't there.
    static func run(_ args: [String]) -> (ok: Bool, output: String, errors: String)? {
        guard let exe = executable else { return nil }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        process.arguments = args
        process.environment = environment
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        do { try process.run() } catch { return nil }
        let output = out.fileHandleForReading.readDataToEndOfFile()
        let errors = err.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (process.terminationStatus == 0, String(decoding: output, as: UTF8.self), String(decoding: errors, as: UTF8.self))
    }

    /// Recordings whose process died: `stenny recover` stops their recorder and queues the final pass.
    static func recover() -> [String: Bool] {
        guard let list = json(["recover", "--json"]) as? [[String: Any]] else { return [:] }
        var out: [String: Bool] = [:]
        for item in list {
            if let course = item["course"] as? String { out[course] = item["kind"] as? String == "event" }
        }
        return out
    }

    struct UpdateCheck: Sendable {
        let current: String
        let latest: String
        let available: Bool
    }

    /// `stenny update --check`; nil if it couldn't be checked (no internet, GitHub down).
    static func checkForUpdate() -> UpdateCheck? {
        guard let d = json(["update", "--check"]) as? [String: Any],
              let current = d["current"] as? String, let latest = d["latest"] as? String else { return nil }
        return UpdateCheck(current: current, latest: latest, available: d["update_available"] as? Bool ?? false)
    }

    /// Runs `stenny update` on its own, since it closes this app to replace it. `onFailure` gets the reason.
    static func runUpdate(onFailure: @escaping @Sendable (String) -> Void) {
        guard let exe = executable else { return onFailure("The `stenny` command was not found.") }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        process.arguments = ["update"]
        process.environment = environment
        let errors = Pipe()
        process.standardOutput = FileHandle.nullDevice
        process.standardError = errors
        process.terminationHandler = { finished in
            guard finished.terminationStatus != 0 else { return }
            let text = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            onFailure(text.isEmpty ? "Exit status \(finished.terminationStatus)." : text)
        }
        do { try process.run() } catch { onFailure(error.localizedDescription) }
    }

    /// Microphones ffmpeg can record from, and the system's current input device.
    static func devices() -> (devices: [String], systemDefault: String?) {
        guard let d = json(["devices", "--json"]) as? [String: Any] else { return ([], nil) }
        return (d["devices"] as? [String] ?? [], d["default"] as? String)
    }

    /// Starts `stenny live --course <name>` in the background. It keeps running on its own;
    /// this app only talks to it through the socket. Output goes to live.log.
    /// `event`: `course` is the title of a talk or event (`stenny live --event`), not a configured course.
    /// `onExit` gets the recorder's exit status once it ends, however it ends.
    static func startLive(course: String, event: Bool = false, device: String?, draft: Bool = true,
                          onExit: @escaping @Sendable (Int32) -> Void) throws {
        guard let exe = executable else {
            throw NSError(domain: "Stenny", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "The `stenny` command was not found. Install it with `uv tool install -e .` in the project folder."])
        }
        try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logPath, contents: nil)
        let log = FileHandle(forWritingAtPath: logPath)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        process.arguments = ["live", event ? "--event" : "--course", course] + (device.map { ["--device", $0] } ?? [])
            + (draft ? [] : ["--no-draft"])
        process.environment = environment
        process.standardOutput = log
        process.standardError = log
        process.standardInput = FileHandle.nullDevice
        process.terminationHandler = { onExit($0.terminationStatus) }
        try process.run()
    }

    /// The last line the recorder wrote to live.log, e.g. why it quit right after starting.
    static func lastLogLine() -> String? {
        guard let text = try? String(contentsOfFile: logPath, encoding: .utf8) else { return nil }
        return text.split(whereSeparator: \.isNewline).last.map { $0.trimmingCharacters(in: .whitespaces) }
    }
}
