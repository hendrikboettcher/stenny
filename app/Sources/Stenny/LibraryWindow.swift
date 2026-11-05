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
    let status: String         // final | draft | live | queued | finalizing (the last three: the live draft)
    let draftNote: String?     // a live draft: where its final transcript is ("… made once the Mac is plugged in …")
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
        draftNote = json["draft_note"] as? String
        summary = json["summary"] as? String
    }

    var id: String { "\(kind)/\(course)/\(folder)" }
    var isEvent: Bool { kind == "event" }
    var isFinal: Bool { status == "final" }
    /// Where the final transcript is, so a draft never looks forgotten.
    var draftNotice: String {
        "This is still the live draft. " + (draftNote ?? "The final transcript replaces it once the recording is finished.")
    }
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
    /// The courses in Stenny's menu (`stenny courses`), recorded or not, in the config's order.
    @Published private(set) var configured: [String] = []
    @Published private(set) var loaded = false
    @Published private(set) var failed = false
    @Published var group: LibraryGroup? {
        didSet { if group != oldValue { selection = group.flatMap { entries(in: $0).first?.id } } }
    }
    @Published var selection: LibraryEntry.ID?

    /// The courses in the menu, also those without a recording yet.
    var courses: [LibraryGroup] { configured.map { LibraryGroup(course: $0, isEvent: false) } }
    /// Removed courses (or ones never added) that still have transcripts.
    var pastCourses: [LibraryGroup] { groups(events: false).filter { !isConfigured($0.course) } }
    var events: [LibraryGroup] { groups(events: true) }
    var selected: LibraryEntry? { entries.first { $0.id == selection } }

    private var all: [LibraryGroup] { courses + pastCourses + events }

    func isConfigured(_ course: String) -> Bool {
        configured.contains { $0.caseInsensitiveCompare(course) == .orderedSame }
    }

    private func groups(events: Bool) -> [LibraryGroup] {
        var seen: [LibraryGroup] = []
        for entry in entries where entry.isEvent == events && !seen.contains(entry.group) { seen.append(entry.group) }
        return seen
    }

    /// Newest first. Course names match regardless of case, as they do in the config.
    func entries(in group: LibraryGroup) -> [LibraryEntry] {
        entries.filter { $0.isEvent == group.isEvent && $0.course.caseInsensitiveCompare(group.course) == .orderedSame }
            .reversed()
    }

    init() {
        NotificationCenter.default.addObserver(forName: AppActions.coursesChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.reload() }
        }
    }

    func reload() {
        Task {
            async let list = Task.detached { StennyCLI.library() }.value
            async let menu = Task.detached { StennyCLI.courses() }.value
            let (found, names) = await (list, menu)
            let added = names.first { !configured.isEmpty && !isConfigured($0) }  // just added: show it
            failed = found == nil
            entries = found ?? []
            configured = names
            loaded = true
            if let added {
                group = LibraryGroup(course: added, isEvent: false)
            } else if group == nil || !all.contains(group!) {
                group = all.first
            } else if selected == nil {
                selection = group.flatMap { entries(in: $0).first?.id }
            }
        }
    }
}

/// A course or talk in the sidebar. Courses show a ⋯ on hover (Remove Course…, or Bring Back for a past one),
/// right under the + that adds them; the same actions are on right-click.
private struct GroupRow<Actions: View>: View {
    let group: LibraryGroup
    let selected: Bool
    let select: () -> Void
    @ViewBuilder let actions: () -> Actions
    @State private var hovering = false

    var body: some View {
        ZStack(alignment: .trailing) {
            Button(action: select) {
                Text(group.course)
                    .font(Typeface.serif(15, weight: selected ? 440 : 360))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .padding(.leading, 10)
                    .padding(.trailing, hovering && !group.isEvent ? 30 : 10)
                    .padding(.vertical, 6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(selected ? Palette.mark : .clear, in: RoundedRectangle(cornerRadius: 8))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(group.course)
            if hovering && !group.isEvent {
                Menu(content: actions) {
                    Image(systemName: "ellipsis").font(.system(size: 12, weight: .semibold))
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .foregroundStyle(Palette.muted)
                .padding(.trailing, 8)
                .help("Course options")
                .accessibilityLabel("Options for \(group.course)")
            }
        }
        .onHover { hovering = $0 }
        .contextMenu(menuItems: actions)
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
                HStack {
                    Kicker("Courses")
                    Spacer()
                    Button(action: AppActions.addCourse) {
                        Image(systemName: "plus").font(.system(size: 11, weight: .semibold)).frame(width: 22, height: 20)
                    }
                    .buttonStyle(FooterButtonStyle())
                    .help("Add a course")
                    .accessibilityLabel("Add Course")
                }
                .padding(.leading, 10).padding(.trailing, 4).padding(.bottom, 2)
                if model.loaded && model.courses.isEmpty {
                    Button(action: AppActions.addCourse) {
                        Text("Add your first course").font(Typeface.serif(15)).padding(.horizontal, 10).padding(.vertical, 6)
                    }
                    .buttonStyle(FooterButtonStyle())
                }
                ForEach(model.courses, id: \.self) { groupRow($0) }
                if !model.pastCourses.isEmpty {
                    Kicker("Past courses").padding(.horizontal, 10).padding(.top, 16).padding(.bottom, 4)
                    ForEach(model.pastCourses, id: \.self) { groupRow($0) }
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
        GroupRow(group: group, selected: model.group == group, select: { model.group = group }) {
            courseActions(group)
        }
    }

    /// Remove Course… for a course in the menu, Bring Back for a past one; nothing for talks.
    @ViewBuilder private func courseActions(_ group: LibraryGroup) -> some View {
        if !group.isEvent {
            if model.isConfigured(group.course) {
                Button("Remove Course…") {
                    AppActions.removeCourse(group.course, recordings: model.entries(in: group).count)
                }
            } else {
                Button("Bring Back to the Menu") { AppActions.bringBack(group.course) }
            }
        }
    }

    @ViewBuilder private var lectureList: some View {
        if let group = model.group {
            let entries = model.entries(in: group)
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    Kicker(entries.isEmpty ? "No recordings yet" : entries.count == 1 ? "1 recording" : "\(entries.count) recordings")
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
            LectureDetail(entry: entry, reload: model.reload).id(entry.id)
        } else if let group = model.group, model.loaded, !model.failed {
            VStack(spacing: 6) {
                Text("Nothing recorded for \(group.course) yet").font(Typeface.serif(19, weight: 440))
                Text("Start a recording from Stenny in the menu bar; its transcript appears here.")
                    .font(Typeface.serif(15)).foregroundStyle(Palette.muted)
            }
            .multilineTextAlignment(.center)
            .padding(32)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
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

/// "Live draft", "Waiting", "Finishing" or "Draft", with the website's recording dot.
struct LiveBadge: View {
    let status: String
    private var label: String {
        switch status {
        case "live": "Live draft"
        case "queued": "Waiting"
        case "finalizing": "Finishing"
        default: "Draft"
        }
    }
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(status == "live" ? Palette.rec : Palette.warn).frame(width: 6, height: 6)
            Text(label).font(Typeface.sans(10.5, weight: .medium))
        }
        .foregroundStyle(status == "live" ? Palette.rec : Palette.warn)
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .overlay(Capsule().strokeBorder(status == "live" ? Palette.rec : Palette.warn, lineWidth: 1))
    }
}

struct LectureDetail: View {
    let entry: LibraryEntry
    let reload: () -> Void
    @AppStorage("shareWithSummary") private var withSummary = true
    @State private var sharing = false
    @State private var error: String?
    @State private var anchor = ShareAnchor()
    @State private var summary: [SummaryLine]?
    @State private var finishing = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Kicker(entry.kicker)
                Text(entry.title).font(Typeface.serif(30, weight: 460)).textSelection(.enabled).padding(.top, 6)
                Text(entry.details).font(Typeface.sans(13)).foregroundStyle(Palette.muted).padding(.top, 6)

                if !entry.isFinal {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Circle().fill(Palette.rec).frame(width: 7, height: 7)
                        VStack(alignment: .leading, spacing: 9) {
                            Text(entry.draftNotice)
                                .font(Typeface.serif(14.5))
                            if entry.status == "queued" {
                                Button(finishing ? "Starting…" : "Make Final Transcript Now", action: finishNow)
                                    .buttonStyle(PillButtonStyle(small: true))
                                    .disabled(finishing)
                            }
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 11)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Palette.mark, in: RoundedRectangle(cornerRadius: 12))
                    .padding(.top, 18)
                }

                HStack(spacing: 8) {
                    Button(action: chooseFormat) {
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

    /// Share… first asks which kind of file, in a menu under the button, then opens the share sheet there.
    private func chooseFormat() {
        guard let view = anchor.view else { return }
        let menu = NSMenu()
        for format in ShareFormat.allCases {
            let item = ClosureMenuItem(title: format.title, symbol: format.symbol) { share(format) }
            item.subtitle = format.help
            menu.addItem(item)
        }
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: view.isFlipped ? view.bounds.height + 4 : -4), in: view)
    }

    /// The clean copy, with the summary on top if the box is ticked.
    private func share(_ format: ShareFormat) {
        guard AppActions.lecturerAgreed(to: .share, course: entry.course, isEvent: entry.isEvent) else { return }
        sharing = true
        error = nil
        let entry = entry, withSummary = withSummary && entry.summary != nil
        Task {
            var result = await Task.detached { StennyCLI.share(entry, withSummary: withSummary, format: format) }.value
            if format == .pdf, case .success(let page) = result {
                result = PDFExport.make(from: page)
            }
            sharing = false
            switch result {
            case .success(let url):
                guard let view = anchor.view else { return }
                ShareSheet.show(url, asText: format == .md, at: view)
            case .failure(let failure):
                error = failure.message
            }
        }
    }

    /// The waiting final pass, now (also on battery). The draft turns "Finishing" as soon as it starts, and the
    /// window shows the final transcript once it's done.
    private func finishNow() {
        finishing = true
        error = nil
        let args = ["queue", "run", "--lecture", entry.course, entry.folder] + (entry.isEvent ? ["--event"] : [])
        let reload = reload
        Task {
            let run = Task.detached { StennyCLI.run(args) }
            try? await Task.sleep(for: .seconds(4))
            reload()  // now "Finishing"
            let result = await run.value
            reload()
            finishing = false
            if let result, !result.ok {
                let reason = result.errors.trimmingCharacters(in: .whitespacesAndNewlines)
                error = reason.split(separator: "\n").last.map(String.init) ?? "The final transcript couldn't be made."
            } else if result == nil {
                error = "The `stenny` command was not found."
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

/// The macOS share sheet with Copy added at the top: the file itself (paste it into Finder, Mail or a chat),
/// and for Markdown also its text (paste it straight into an AI chat or a note).
@MainActor
final class ShareSheet: NSObject, NSSharingServicePickerDelegate {
    private static var current: ShareSheet?  // the picker doesn't keep its delegate
    private let file: URL
    private let asText: Bool

    private init(file: URL, asText: Bool) {
        self.file = file
        self.asText = asText
    }

    static func show(_ file: URL, asText: Bool, at view: NSView) {
        let sheet = ShareSheet(file: file, asText: asText)
        current = sheet
        let picker = NSSharingServicePicker(items: [file])
        picker.delegate = sheet
        picker.show(relativeTo: view.bounds, of: view, preferredEdge: .minY)
    }

    nonisolated func sharingServicePicker(_ picker: NSSharingServicePicker, sharingServicesForItems items: [Any],
                                          proposedSharingServices proposed: [NSSharingService]) -> [NSSharingService] {
        let copy = NSSharingService(title: "Copy", image: NSImage(systemSymbolName: "doc.on.doc", accessibilityDescription: nil)!,
                                    alternateImage: nil) { [file, asText] in
            let board = NSPasteboard.general
            board.clearContents()
            let item = NSPasteboardItem()
            item.setString(file.absoluteString, forType: .fileURL)
            if asText, let text = try? String(contentsOf: file, encoding: .utf8) {
                item.setString(text, forType: .string)
            }
            board.writeObjects([item])
        }
        return [copy] + proposed
    }

    nonisolated func sharingServicePicker(_ picker: NSSharingServicePicker, didChoose service: NSSharingService?) {
        MainActor.assumeIsolated { Self.current = nil }
    }
}

/// A menu item that runs a closure (NSMenu wants a target and a selector).
final class ClosureMenuItem: NSMenuItem {
    private let run: () -> Void

    init(title: String, symbol: String, run: @escaping () -> Void) {
        self.run = run
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
        image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
    }

    required init(coder: NSCoder) { fatalError("not used") }

    @objc private func fire() { run() }
}

/// What Share… sends: a PDF to read anywhere, a Word file to edit, or the Markdown the vault keeps.
enum ShareFormat: String, CaseIterable {
    case pdf, docx, md

    var title: String { switch self { case .pdf: "PDF"; case .docx: "Word"; case .md: "Markdown" } }
    var help: String {
        switch self {
        case .pdf: "Opens on any phone or computer"
        case .docx: "To edit in Word, Pages or Google Docs"
        case .md: "Best for Obsidian and AI chats"
        }
    }
    var symbol: String { switch self { case .pdf: "doc.richtext"; case .docx: "doc.text"; case .md: "number" } }
}

/// The HTML copy (`stenny share --format html`) laid out on pages of the Mac's default paper size by the
/// text system, saved as a PDF next to it; the HTML is removed.
@MainActor
enum PDFExport {
    static func make(from page: URL) -> Result<URL, StennyCLI.ShareFailure> {
        defer { try? FileManager.default.removeItem(at: page) }
        let pdf = page.deletingPathExtension().appendingPathExtension("pdf")
        guard let data = try? Data(contentsOf: page),
              let text = NSAttributedString(html: data, baseURL: page.deletingLastPathComponent(), documentAttributes: nil)
        else { return .failure(.init("The PDF couldn't be made.")) }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        (info.topMargin, info.bottomMargin, info.leftMargin, info.rightMargin) = (54, 54, 62, 62)
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isHorizontallyCentered = false
        info.isVerticallyCentered = false
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = pdf
        let width = info.paperSize.width - info.leftMargin - info.rightMargin
        let view = NSTextView(frame: NSRect(x: 0, y: 0, width: width, height: 1))
        view.appearance = NSAppearance(named: .aqua)  // paper is white, also in dark mode
        view.textContainer?.lineFragmentPadding = 0
        view.textStorage?.setAttributedString(text)
        view.layoutManager?.ensureLayout(for: view.textContainer!)
        view.sizeToFit()
        try? FileManager.default.removeItem(at: pdf)
        let operation = NSPrintOperation(view: view, printInfo: info)
        operation.showsPrintPanel = false
        operation.showsProgressPanel = false
        guard operation.run(), FileManager.default.fileExists(atPath: pdf.path) else {
            return .failure(.init("The PDF couldn't be made."))
        }
        return .success(pdf)
    }
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
