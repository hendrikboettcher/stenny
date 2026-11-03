import AppKit
import SwiftUI

// Stenny's window (prototype): every lecture and talk by course, the summary, and Share…, which sends a
// clean copy (`stenny share`) through the macOS share sheet. The lists come from `stenny library`.

/// One lecture or talk, as `stenny library --json` lists it.
struct LibraryEntry: Identifiable, Hashable, Sendable {
    let kind: String           // lecture | event
    let course: String         // the course, or a talk's title
    let folder: String         // "2026-10-20" or "2026-10-20 (2)"
    let number: Int            // lecture number, 0 for talks
    let date: String
    let start: String?
    let duration: String?
    let language: String?
    let status: String         // final | draft | live
    let transcript: String
    let summary: String?

    init?(_ json: [String: Any]) {
        guard let kind = json["kind"] as? String, let course = json["course"] as? String,
              let folder = json["folder"] as? String, let transcript = json["transcript"] as? String else { return nil }
        self.kind = kind
        self.course = course
        self.folder = folder
        self.transcript = transcript
        number = json["number"] as? Int ?? 0
        date = json["date"] as? String ?? String(folder.prefix(10))
        start = json["start"] as? String
        duration = json["duration"] as? String
        language = json["language"] as? String
        status = json["status"] as? String ?? "final"
        summary = json["summary"] as? String
    }

    var id: String { "\(kind)/\(course)/\(folder)" }
    var isEvent: Bool { kind == "event" }
    var isFinal: Bool { status == "final" }
    var group: LibraryGroup { LibraryGroup(course: course, isEvent: isEvent) }

    var prettyDate: String {
        let parse = DateFormatter()
        parse.dateFormat = "yyyy-MM-dd"
        parse.locale = Locale(identifier: "en_US_POSIX")
        guard let day = parse.date(from: date) else { return date }
        let show = DateFormatter()
        show.setLocalizedDateFormatFromTemplate("EEEdMMMyyyy")
        let second = folder.count > 10 ? " \(folder.dropFirst(10).trimmingCharacters(in: .whitespaces))" : ""
        return show.string(from: day) + second
    }

    var rowTitle: String { number > 0 ? "Lecture \(number)" : prettyDate }
    var rowDetail: String {
        [number > 0 ? prettyDate : start, duration].compactMap { $0 }.joined(separator: " · ")
    }
    var heading: String { number > 0 ? "\(course) – Lecture \(number)" : course }
    var details: String {
        let languages = ["en": "English", "de": "German", "fr": "French", "it": "Italian", "es": "Spanish",
                         "mixed": "Mixed languages"]
        let when = [prettyDate, start].compactMap { $0 }.joined(separator: ", ")
        return [when, duration, language.map { languages[$0] ?? $0 }].compactMap { $0 }.joined(separator: " · ")
    }
}

/// A course, or the title several talks share, in the sidebar.
struct LibraryGroup: Hashable {
    let course: String
    let isEvent: Bool
}

@MainActor
final class LibraryModel: ObservableObject {
    @Published private(set) var entries: [LibraryEntry] = []
    @Published private(set) var loaded = false
    @Published private(set) var failed = false
    @Published var group: LibraryGroup? {
        didSet { if group != oldValue { selection = group.flatMap { entries(in: $0).first?.id } } }
    }
    @Published var selection: LibraryEntry.ID?

    var courses: [LibraryGroup] { groups(events: false) }
    var events: [LibraryGroup] { groups(events: true) }
    var selected: LibraryEntry? { entries.first { $0.id == selection } }

    private func groups(events: Bool) -> [LibraryGroup] {
        var seen: [LibraryGroup] = []
        for entry in entries where entry.isEvent == events && !seen.contains(entry.group) { seen.append(entry.group) }
        return seen
    }

    /// Newest first.
    func entries(in group: LibraryGroup) -> [LibraryEntry] {
        entries.filter { $0.group == group }.reversed()
    }

    func reload() {
        Task {
            let list = await Task.detached { StennyCLI.library() }.value
            failed = list == nil
            entries = list ?? []
            loaded = true
            if group == nil || !entries.contains(where: { $0.group == group }) {
                group = courses.first ?? events.first
            } else if selected == nil {
                selection = group.flatMap { entries(in: $0).first?.id }
            }
        }
    }
}

struct LibraryView: View {
    @ObservedObject var model: LibraryModel

    var body: some View {
        NavigationSplitView {
            List(selection: $model.group) {
                if !model.courses.isEmpty {
                    Section("Courses") {
                        ForEach(model.courses, id: \.self) { Label($0.course, systemImage: "book.closed").tag($0) }
                    }
                }
                if !model.events.isEmpty {
                    Section("Talks & Events") {
                        ForEach(model.events, id: \.self) { Label($0.course, systemImage: "mic").tag($0) }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 200, ideal: 250)
        } content: {
            if let group = model.group {
                List(model.entries(in: group), selection: $model.selection) { entry in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(entry.rowTitle).font(.headline)
                            if !entry.isFinal {
                                Text(entry.status == "live" ? "Live draft" : "Draft")
                                    .font(.caption).foregroundStyle(.orange)
                            }
                        }
                        Text(entry.rowDetail).font(.subheadline).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                }
                .navigationSplitViewColumnWidth(min: 200, ideal: 240)
            } else {
                Color.clear
            }
        } detail: {
            if let entry = model.selected {
                LectureDetail(entry: entry).id(entry.id)
            } else if model.failed {
                ContentUnavailableView("Couldn't read your lectures", systemImage: "exclamationmark.triangle",
                                       description: Text("The `stenny` command didn't answer."))
            } else if model.loaded {
                ContentUnavailableView("No lectures yet", systemImage: "doc.text",
                                       description: Text("Transcripts appear here after your first recording."))
            } else {
                ProgressView()
            }
        }
    }
}

struct LectureDetail: View {
    let entry: LibraryEntry
    @AppStorage("shareWithSummary") private var withSummary = true
    @State private var sharing = false
    @State private var error: String?
    @State private var anchor = ShareAnchor()
    @State private var summary: [SummaryLine]?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.heading).font(.title2.bold()).textSelection(.enabled)
                    Text(entry.details).foregroundStyle(.secondary)
                }
                if !entry.isFinal {
                    Label("This is still the \(entry.status == "live" ? "live draft" : "draft"). The final transcript "
                          + "replaces it once the recording is finished.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
                HStack(spacing: 10) {
                    Button(action: share) {
                        Label(sharing ? "Preparing…" : "Share…", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(sharing)
                    .background(ShareAnchorView(anchor: anchor))
                    Button("Open Transcript", action: open)
                    Button("Show in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: entry.transcript)])
                    }
                }
                if entry.summary != nil {
                    Toggle("Put the summary at the top of the shared copy", isOn: $withSummary)
                }
                if let error {
                    Text(error).foregroundStyle(.red).textSelection(.enabled)
                }
                Divider()
                if let summary, !summary.isEmpty {
                    Text("Summary").font(.title3.bold())
                    ForEach(summary) { $0.view }
                } else if summary != nil {
                    Text("No summary for this lecture.").foregroundStyle(.secondary)
                }
            }
            .padding(24)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            let path = entry.summary
            summary = await Task.detached { path.map(SummaryLine.read) ?? [] }.value
        }
    }

    private func share() {
        sharing = true
        error = nil
        let entry = entry, withSummary = withSummary && entry.summary != nil
        Task {
            let result = await Task.detached { StennyCLI.share(entry, withSummary: withSummary) }.value
            sharing = false
            switch result {
            case .success(let url):
                guard let view = anchor.view else { return }
                NSSharingServicePicker(items: [url]).show(relativeTo: view.bounds, of: view, preferredEdge: .minY)
            case .failure(let failure):
                error = failure.message
            }
        }
    }

    private func open() {
        var components = URLComponents(string: "obsidian://open")!
        components.queryItems = [URLQueryItem(name: "path", value: entry.transcript)]
        if let url = components.url, NSWorkspace.shared.open(url) { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: entry.transcript))
    }
}

/// The share sheet needs an AppKit view to point at: the one behind the Share button.
final class ShareAnchor {
    weak var view: NSView?
}

struct ShareAnchorView: NSViewRepresentable {
    let anchor: ShareAnchor
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        anchor.view = view
        return view
    }
    func updateNSView(_ view: NSView, context: Context) { anchor.view = view }
}

/// A line of the summary file, for showing it without its frontmatter, title and note.
struct SummaryLine: Identifiable, Sendable {
    enum Kind: Sendable { case heading, subheading, bullet, text }
    let id: Int
    let kind: Kind
    let text: String

    static func read(_ path: String) -> [SummaryLine] {
        guard var text = try? String(contentsOfFile: path, encoding: .utf8) else { return [] }
        if text.hasPrefix("---\n"), let end = text.range(of: "\n---\n", range: text.index(text.startIndex, offsetBy: 4)..<text.endIndex) {
            text = String(text[end.upperBound...])
        }
        // [[target|label]] → label, [[target]] → target
        text = text.replacingOccurrences(of: #"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]"#, with: "$1", options: .regularExpression)
        var lines: [SummaryLine] = []
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("# "), !line.hasPrefix(">") else { continue }
            let (kind, body): (Kind, String) =
                line.hasPrefix("#") ? (.heading, line.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces))
                : line.hasPrefix("**") && line.hasSuffix("**") ? (.subheading, String(line.dropFirst(2).dropLast(2)))
                : line.hasPrefix("- ") ? (.bullet, String(line.dropFirst(2)))
                : (.text, line)
            lines.append(SummaryLine(id: lines.count, kind: kind, text: body))
        }
        return lines
    }

    @MainActor @ViewBuilder var view: some View {
        let inline = (try? AttributedString(markdown: text, options: .init(
            interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
        switch kind {
        case .heading: Text(inline).font(.headline).padding(.top, 6)
        case .subheading: Text(inline).bold().padding(.top, 2)
        case .bullet:
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("•").foregroundStyle(.secondary)
                Text(inline).textSelection(.enabled)
            }
        case .text: Text(inline).textSelection(.enabled)
        }
    }
}

/// The window, created on first use and kept while Stenny runs.
@MainActor
final class LibraryWindow {
    static let shared = LibraryWindow()
    private var window: NSWindow?
    private let model = LibraryModel()

    func show() {
        if window == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 640),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                                  backing: .buffered, defer: false)
            window.title = "Stenny"
            window.contentViewController = NSHostingController(rootView: LibraryView(model: model))
            window.setContentSize(NSSize(width: 1000, height: 640))
            window.isReleasedWhenClosed = false
            window.center()
            window.setFrameAutosaveName("StennyLibrary")
            self.window = window
        }
        model.reload()
        NSApp.activate()
        window?.makeKeyAndOrderFront(nil)
    }
}
