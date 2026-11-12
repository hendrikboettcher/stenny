import AppKit

/// Opening a transcript: in Obsidian when the file sits in one of its vaults, else in the default Markdown app.
/// Obsidian's `open?path=` link only works inside a vault it knows; for any other file it starts anyway and shows
/// "Vault not found", and to macOS that link counts as opened, so Stenny has to ask first.
enum Obsidian {
    static var installed: Bool {
        NSWorkspace.shared.urlForApplication(toOpen: URL(string: "obsidian://open")!) != nil
    }

    /// The folders Obsidian has as vaults, from its own list (obsidian.json in its settings folder).
    static func vaults() -> [String] {
        let list = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/obsidian/obsidian.json")
        guard let data = try? Data(contentsOf: list),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let vaults = json["vaults"] as? [String: [String: Any]] else { return [] }
        return vaults.values.compactMap { $0["path"] as? String }
    }

    /// The file is inside one of these folders (case doesn't matter: the Mac's disks ignore it, and so does Obsidian).
    static func inside(_ file: String, anyOf folders: [String]) -> Bool {
        let file = canonical(file)
        return folders.contains { folder in
            let folder = canonical(folder)
            return file.hasPrefix(folder.hasSuffix("/") ? folder : folder + "/")
        }
    }

    static func inVault(_ file: String) -> Bool { installed && inside(file, anyOf: vaults()) }

    static func open(_ file: String) {
        if inVault(file) {
            var components = URLComponents(string: "obsidian://open")!
            components.queryItems = [URLQueryItem(name: "path", value: file)]
            if let url = components.url, NSWorkspace.shared.open(url) { return }
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: file))
    }

    private static func canonical(_ path: String) -> String {
        URL(fileURLWithPath: (path as NSString).expandingTildeInPath).resolvingSymlinksInPath()
            .standardizedFileURL.path.lowercased()
    }
}
