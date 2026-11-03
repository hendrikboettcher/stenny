import AppKit
import SwiftUI

// Stenny's window (prototype): every lecture and talk by course, the summary, and Share…, which sends a
// clean copy (`stenny share`) through the macOS share sheet. The lists come from `stenny library`.
// Styled like stenny.eu (StennyStyle.swift).

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
    /// Above the title: the course, or that it's a talk.
    var kicker: String { isEvent ? "Talk or event" : course }
    var title: String { number > 0 ? "Lecture \(number)" : isEvent ? course : prettyDate }
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

/// Space at the top for the window's close/minimize/zoom buttons (the title bar is transparent).
private let titleBarHeight: CGFloat = 52

struct LibraryView: View {
    @ObservedObject var model: LibraryModel

    var body: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 232).background(Palette.bar)
            Hairline(vertical: true)
            lectureList.frame(width: 248)
            Hairline(vertical: true)
            detail.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Palette.bg)
        .foregroundStyle(Palette.ink)
        .tint(Palette.ink)
        .ignoresSafeArea()
        .frame(minWidth: 860, minHeight: 540)
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            sidebarGroups
            Hairline()
            VStack(alignment: .leading, spacing: 0) {
                footerButton("Send Feedback…", symbol: "bubble.left", action: AppActions.sendFeedback)
                footerButton("Check for Updates…", symbol: "arrow.down.circle", action: AppActions.checkForUpdates)
                if let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String {
                    Text("Version \(version)").font(Typeface.sans(11)).foregroundStyle(Palette.muted)
                        .padding(.horizontal, 10).padding(.top, 6)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
        }
    }

    private func footerButton(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(Typeface.sans(12.5))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(FooterButtonStyle())
    }

    private var sidebarGroups: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 2) {
                // the website's brand: the stick figure next to the name
                HStack(alignment: .center, spacing: 10) {
                    StennyLogo(height: 30)
                    Text("Stenny").font(Typeface.serif(22, weight: 480))
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 18)
                .accessibilityElement(children: .combine)
                if !model.courses.isEmpty {
                    Kicker("Courses").padding(.horizontal, 10).padding(.bottom, 4)
                    ForEach(model.courses, id: \.self) { groupRow($0) }
                }
                if !model.events.isEmpty {
                    Kicker("Talks & events").padding(.horizontal, 10).padding(.top, 16).padding(.bottom, 4)
                    ForEach(model.events, id: \.self) { groupRow($0) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)  // "Stenny" stays left while the list loads
            .padding(.horizontal, 10)
            .padding(.top, titleBarHeight)
            .padding(.bottom, 16)
        }
    }

    private func groupRow(_ group: LibraryGroup) -> some View {
        Button { model.group = group } label: {
            Text(group.course)
                .font(Typeface.serif(15, weight: model.group == group ? 440 : 360))
                .lineLimit(1)
                .truncationMode(.tail)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(model.group == group ? Palette.mark : .clear, in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(group.course)
    }

    @ViewBuilder private var lectureList: some View {
        if let group = model.group {
            let entries = model.entries(in: group)
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    Kicker(entries.count == 1 ? "1 recording" : "\(entries.count) recordings")
                        .padding(.horizontal, 10).padding(.bottom, 6)
                    ForEach(entries) { entryRow($0) }
                }
                .padding(.horizontal, 10)
                .padding(.top, titleBarHeight + 6)
                .padding(.bottom, 16)
            }
        } else {
            Color.clear
        }
    }

    private func entryRow(_ entry: LibraryEntry) -> some View {
        Button { model.selection = entry.id } label: {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(entry.rowTitle).font(Typeface.serif(16, weight: 440))
                    if !entry.isFinal { LiveBadge(status: entry.status) }
                }
                Text(entry.rowDetail).font(Typeface.sans(12)).foregroundStyle(Palette.muted)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(model.selection == entry.id ? Palette.mark : .clear, in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder private var detail: some View {
        if let entry = model.selected {
            LectureDetail(entry: entry).id(entry.id)
        } else {
            VStack(spacing: 6) {
                Text(model.failed ? "Couldn't read your lectures" : model.loaded ? "No lectures yet" : "Loading…")
                    .font(Typeface.serif(19, weight: 440))
                if model.failed || model.loaded {
                    Text(model.failed ? "The stenny command didn't answer." :
                            "Transcripts appear here after your first recording.")
                        .font(Typeface.serif(15)).foregroundStyle(Palette.muted)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

/// "Live draft" or "Draft", with the website's recording dot.
struct LiveBadge: View {
    let status: String
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(status == "live" ? Palette.rec : Palette.warn).frame(width: 6, height: 6)
            Text(status == "live" ? "Live draft" : "Draft").font(Typeface.sans(10.5, weight: .medium))
        }
        .foregroundStyle(status == "live" ? Palette.rec : Palette.warn)
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .overlay(Capsule().strokeBorder(status == "live" ? Palette.rec : Palette.warn, lineWidth: 1))
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
            VStack(alignment: .leading, spacing: 0) {
                Kicker(entry.kicker)
                Text(entry.title).font(Typeface.serif(30, weight: 460)).textSelection(.enabled).padding(.top, 6)
                Text(entry.details).font(Typeface.sans(13)).foregroundStyle(Palette.muted).padding(.top, 6)

                if !entry.isFinal {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Circle().fill(Palette.rec).frame(width: 7, height: 7)
                        Text("This is still the \(entry.status == "live" ? "live draft" : "draft"). The final "
                             + "transcript replaces it once the recording is finished.")
                            .font(Typeface.serif(14.5))
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 11)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Palette.mark, in: RoundedRectangle(cornerRadius: 12))
                    .padding(.top, 18)
                }

                HStack(spacing: 8) {
                    Button(action: share) {
                        Label(sharing ? "Preparing…" : "Share…", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(PillButtonStyle(primary: true))
                    .disabled(sharing)
                    .background(ShareAnchorView(anchor: anchor))
                    Button("Open Transcript", action: open).buttonStyle(PillButtonStyle())
                    Button("Show in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: entry.transcript)])
                    }
                    .buttonStyle(PillButtonStyle())
                }
                .padding(.top, 22)

                if entry.summary != nil {
                    Toggle(isOn: $withSummary) {
                        Text("Put the summary at the top of the shared copy").font(Typeface.sans(12.5))
                            .foregroundStyle(Palette.muted)
                    }
                    .toggleStyle(.checkbox)
                    .padding(.top, 12)
                }
                if let error {
                    Text(error).font(Typeface.sans(12.5)).foregroundStyle(Palette.rec).textSelection(.enabled)
                        .padding(.top, 10)
                }

                Hairline().padding(.vertical, 26)

                if let summary, !summary.isEmpty {
                    Kicker("Summary").padding(.bottom, 4)
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(summary) { $0.view }
                    }
                } else if summary != nil {
                    Text("No summary for this lecture.").font(Typeface.serif(15)).foregroundStyle(Palette.muted)
                }
            }
            .frame(maxWidth: 640, alignment: .leading)
            .padding(.horizontal, 40)
            .padding(.top, titleBarHeight + 6)
            .padding(.bottom, 40)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            let path = entry.summary
            summary = await Task.detached { path.map(SummaryLine.read) ?? [] }.value
        }
    }

    private func share() {
        guard AppActions.lecturerAgreed(to: .share, course: entry.course, isEvent: entry.isEvent) else { return }
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

    /// Inline Markdown, with timestamps like [0:08:02] set small and muted so the sentence reads first.
    private var inline: AttributedString {
        var out = (try? AttributedString(markdown: text, options: .init(
            interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
        let plain = String(out.characters)
        let pattern = try! NSRegularExpression(pattern: #"\[\d+:\d{2}:\d{2}\]"#)
        for match in pattern.matches(in: plain, range: NSRange(plain.startIndex..., in: plain)).reversed() {
            guard let range = Range(match.range, in: plain) else { continue }
            let lower = plain.distance(from: plain.startIndex, to: range.lowerBound)
            let upper = plain.distance(from: plain.startIndex, to: range.upperBound)
            let start = out.characters.index(out.startIndex, offsetBy: lower)
            let end = out.characters.index(out.startIndex, offsetBy: upper)
            out[start..<end].foregroundColor = Palette.muted
            out[start..<end].font = Typeface.sans(11.5)
        }
        return out
    }

    @MainActor @ViewBuilder var view: some View {
        switch kind {
        case .heading:
            Text(inline).font(Typeface.serif(19, weight: 460)).padding(.top, 14)
        case .subheading:
            Text(inline).font(Typeface.serif(15.5, weight: 500)).padding(.top, 6)
        case .bullet:
            HStack(alignment: .firstTextBaseline, spacing: 9) {
                Text("–").font(Typeface.serif(15.5)).foregroundStyle(Palette.muted)
                Text(inline).font(Typeface.serif(15.5)).lineSpacing(3).textSelection(.enabled)
            }
        case .text:
            Text(inline).font(Typeface.serif(15.5)).lineSpacing(3).textSelection(.enabled)
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
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1060, height: 680),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                                  backing: .buffered, defer: false)
            window.title = "Stenny"
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.backgroundColor = Palette.bgNSColor
            window.contentViewController = NSHostingController(rootView: LibraryView(model: model))
            window.setContentSize(NSSize(width: 1060, height: 680))
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
