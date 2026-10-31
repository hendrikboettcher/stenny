import AppKit
import Carbon
import UserNotifications

/// Menu bar icon + menu for live lecture recording.
/// The recorder itself is `stenny live` (Python); this app starts it and sends
/// pause/resume/stop through its socket, polling the status once a second (the level meter
/// polls a cheap `level` request 15×/s while the menu is open).
@MainActor
final class LectureController: NSObject {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let statusLineItem = NSMenuItem()
    private let levelItem = NSMenuItem()
    private let suggestItem = NSMenuItem()       // "Start Calculus" when the calendar says it's on now
    private let calendar = CalendarWatcher()
    private var suggestion: CalendarWatcher.Lecture?
    private var notified: Set<String> = []       // calendar events we already sent a notification for
    private var calendarTimer: Timer?
    private let levelMeter = LevelMeterView(frame: NSRect(x: 0, y: 0, width: 300, height: 22))
    private var levelTimer: Timer?
    private var levelInFlight = false
    private var levelFrame = 0
    private let micItem = NSMenuItem()
    private let draftItem = NSMenuItem()
    private let micMenu = NSMenu()
    private let startItem = NSMenuItem()
    private let coursesMenu = NSMenu()
    private let pauseItem = NSMenuItem()
    private let stopItem = NSMenuItem()
    private let openItem = NSMenuItem()
    private var status: StennyCLI.Status?
    private var hotKey: GlobalHotKey?
    private var poller: Timer?
    private var starting = false
    private var lastNote = ""
    private var idleMessage = "Ready"
    /// Transcribe while recording (off = record only, transcript after Stop), remembered between launches
    private var liveDraft: Bool {
        get { UserDefaults.standard.object(forKey: "liveDraft") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "liveDraft") }
    }
    /// Chosen microphone (nil = the system's input device), remembered between launches
    private var microphone: String? {
        get { UserDefaults.standard.string(forKey: "microphone") }
        set { UserDefaults.standard.set(newValue, forKey: "microphone") }
    }

    override init() {
        super.init()
        statusItem.autosaveName = "Stenny"
        buildMenu()
        // The pause shortcut (default ⌘⇧Ü, `[app] pause_shortcut` in the config) toggles pause/resume from
        // anywhere; no Accessibility permission needed. Until the config is read, the default applies.
        setShortcut(.standard)
        Task.detached {
            let shortcut = StennyCLI.pauseShortcut()
            await MainActor.run { if let shortcut { self.setShortcut(shortcut) } }
        }
        // .common: keep ticking while a menu is open (menus run the event loop in tracking mode),
        // so the timer, word count and input level stay live in the open menu
        let poller = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
        RunLoop.main.add(poller, forMode: .common)
        self.poller = poller
        poll()
        setUpCalendar()
    }

    private func setShortcut(_ shortcut: Shortcut) {
        hotKey = nil  // unregisters the previous one
        hotKey = GlobalHotKey(keyCode: shortcut.keyCode, modifiers: shortcut.carbonModifiers) { [weak self] in
            Task { @MainActor in self?.togglePause() }
        }
        pauseItem.keyEquivalent = shortcut.keyEquivalent
        pauseItem.keyEquivalentModifierMask = shortcut.modifierMask
        AppLog.write("pause shortcut: \(shortcut.text)")
    }

    // MARK: Calendar (the lecture that's on now, offered with one click)

    private func setUpCalendar() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let start = UNNotificationAction(identifier: "start", title: "Start Recording", options: [])
        // One action only: with two, macOS folds them into an "Options" menu. Closing it means "not now".
        center.setNotificationCategories([UNNotificationCategory(identifier: "lecture", actions: [start],
                                                                 intentIdentifiers: [])])
        // `open -a Stenny --args --test-notification`: shows the notification for a sample lecture
        if CommandLine.arguments.contains("--test-notification") {
            Task {
                _ = try? await center.requestAuthorization(options: [.alert, .sound])
                let start = Date().addingTimeInterval(CalendarWatcher.lead)
                post(CalendarWatcher.Lecture(course: "Calculus", title: "Calculus", start: start,
                                             end: start.addingTimeInterval(3 * 3600), eventID: "test-\(Date().timeIntervalSince1970)"))
            }
        }
        Task {
            guard await calendar.requestAccess() else {
                AppLog.write("no calendar access: the current lecture isn't offered")
                return
            }
            _ = try? await center.requestAuthorization(options: [.alert, .sound])
            checkCalendar()
            let timer = Timer(timeInterval: 30, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.checkCalendar() }
            }
            RunLoop.main.add(timer, forMode: .common)
            calendarTimer = timer
        }
    }

    private func checkCalendar() {
        Task {
            let lecture = await calendar.current()
            if lecture != suggestion {
                AppLog.write(lecture.map { "calendar: \($0.title) → \($0.course)" } ?? "calendar: no lecture now")
            }
            suggestion = lecture
            if let lecture { notifyIfStarting(lecture) }
            refresh()
        }
    }

    /// One notification per lecture, from 10 minutes before it starts, unless a recording is already running.
    /// Laid out like the website's mock-up: "Stenny" / "Record this lecture?" / "<course> · <time>".
    /// macOS draws it in the system's light or dark style, so it needs no colours of its own.
    private func notifyIfStarting(_ lecture: CalendarWatcher.Lecture) {
        guard status == nil, !starting, !notified.contains(lecture.eventID),
              Date() >= lecture.start.addingTimeInterval(-CalendarWatcher.lead) else { return }
        notified.insert(lecture.eventID)
        post(lecture)
    }

    private func post(_ lecture: CalendarWatcher.Lecture) {
        let content = UNMutableNotificationContent()
        content.title = "Stenny"   // bold; macOS draws a subtitle bold too, so both other lines go in the body
        content.body = "Record this lecture?\n\(lecture.course) · \(timeRange(lecture))"
        content.categoryIdentifier = "lecture"
        content.userInfo = ["course": lecture.course]
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: lecture.eventID, content: content, trigger: nil))
        AppLog.write("notified: \(lecture.course)")
    }

    private func timeRange(_ lecture: CalendarWatcher.Lecture) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return "\(f.string(from: lecture.start))–\(f.string(from: lecture.end))"
    }

    @objc private func startSuggested() {
        if let course = suggestion?.course { start(course: course) }
    }

    // MARK: Polling

    private func poll() {
        hotKey?.register()  // no-op while registered; heals it if anything ever unregistered it
        Task.detached {
            let s = StennyCLI.send("status")
            await MainActor.run { self.update(s) }
        }
    }

    private func update(_ s: StennyCLI.Status?) {
        if let s { starting = false; if !s.note.isEmpty { lastNote = s.note } }
        if status != nil && s == nil {  // the session just ended
            idleMessage = lastNote.isEmpty ? "Ready · last lecture saved" : "Ready · \(lastNote)"
            lastNote = ""
        }
        status = s
        refresh()
    }

    // MARK: Courses

    @objc private func addCourse() {
        let alert = NSAlert()
        alert.messageText = "Add a course"
        alert.informativeText = "Mixed is for lecturers who switch languages within a lecture. Auto is for one language per lecture, but not always the same one."
        let stack = NSStackView(frame: NSRect(x: 0, y: 0, width: 280, height: 90))
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        let name = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        name.placeholderString = "Course name, e.g. Microeconomics"
        let language = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 280, height: 26))
        let languages = [("English", "en"), ("German", "de"), ("French", "fr"), ("Mixed", "mixed"), ("Auto", "auto")]
        language.addItems(withTitles: languages.map(\.0))
        let short = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        short.placeholderString = "Short name (optional), e.g. micro"
        [name, language, short].forEach { view in
            stack.addArrangedSubview(view)
            view.widthAnchor.constraint(equalToConstant: 280).isActive = true
        }
        alert.accessoryView = stack
        alert.addButton(withTitle: "Add Course")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = name
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let course = name.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let alias = short.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        var args = ["course", "add", course, "--language", languages[language.indexOfSelectedItem].1]
        if !alias.isEmpty { args += ["--alias", alias] }
        Task.detached {
            let result = StennyCLI.run(args)
            await MainActor.run {
                guard let result, result.ok else {
                    self.show(result?.errors.trimmingCharacters(in: .whitespacesAndNewlines) ?? "The `stenny` command was not found.")
                    return
                }
                self.offerProject(course)
            }
        }
    }

    /// Recommended, never required: Stenny works the same without a Claude project.
    private func offerProject(_ course: String) {
        let alert = NSAlert()
        alert.messageText = "\(course) is in your menu"
        alert.informativeText = "Want a Claude project for it, so its chats read these transcripts? In the Claude desktop app: "
            + "Projects ▸ New project, name it, Instructions ▸ paste ▸ Save."
        alert.addButton(withTitle: "Copy Project Instructions")
        alert.addButton(withTitle: "Not Now")
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        Task.detached {
            let result = StennyCLI.run(["project-instructions", "--course", course])
            await MainActor.run {
                guard let result, result.ok else { return self.show("Couldn't get the instructions.") }
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(result.output, forType: .string)
            }
        }
    }

    @objc private func hideCourse(_ sender: NSMenuItem) {
        guard let course = sender.representedObject as? String else { return }
        let alert = NSAlert()
        alert.messageText = "Hide \(course)?"
        alert.informativeText = "It leaves this menu and calendar matching. Its transcripts and glossary stay where they are. "
            + "To bring it back, add a course with the same name."
        alert.addButton(withTitle: "Hide Course")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        Task.detached {
            let result = StennyCLI.run(["course", "hide", course])
            await MainActor.run {
                if result?.ok != true {
                    self.show(result?.errors.trimmingCharacters(in: .whitespacesAndNewlines) ?? "The `stenny` command was not found.")
                }
            }
        }
    }

    // MARK: Actions

    @objc private func startCourse(_ sender: NSMenuItem) {
        guard let course = sender.representedObject as? String else { return }
        start(course: course)
    }

    /// A talk or event: asks for its title, suggesting a calendar event that's on now and isn't a lecture.
    @objc private func startEvent() {
        Task {
            let suggestion = await calendar.otherEvent() ?? ""
            await MainActor.run { self.askForEventTitle(suggestion) }
        }
    }

    private func askForEventTitle(_ suggestion: String) {
        let alert = NSAlert()
        alert.messageText = "Record a talk or event"
        alert.informativeText = "It gets its own folder in Recordings, next to your lectures."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.stringValue = suggestion
        field.placeholderString = "Title, e.g. Guest talk Müller"
        alert.accessoryView = field
        alert.addButton(withTitle: "Start Recording")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let title = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return show("A talk needs a title.") }
        start(course: title, event: true)
    }

    private func start(course: String, event: Bool = false) {
        guard status == nil, !starting else { return }
        AppLog.write("start \(event ? "event" : "course") \(course)")
        do {
            try StennyCLI.startLive(course: course, event: event, device: microphone, draft: liveDraft) { code in
                Task { @MainActor in self.recorderExited(code) }
            }
            starting = true
            refresh()
        } catch {
            show(error.localizedDescription)
        }
    }

    /// Still "Starting …" when the recorder ended: it never came up, so say why instead of waiting forever.
    private func recorderExited(_ code: Int32) {
        guard starting else { return }
        starting = false
        let reason = StennyCLI.lastLogLine() ?? "exit status \(code)"
        AppLog.write("recorder quit while starting: \(reason)")
        refresh()
        show("The recording didn't start.\n\n\(reason)")
    }

    @objc private func toggleDraft() {
        liveDraft.toggle()
        refresh()
    }

    /// Before a recording: the microphone for the next one. During one: switch it right away.
    @objc private func selectMicrophone(_ sender: NSMenuItem) {
        let device = sender.representedObject as? String
        microphone = device
        if let s = status, s.state == "recording" || s.state == "paused" {
            guard let name = device ?? StennyCLI.devices().systemDefault else { return }
            AppLog.write("switch microphone → \(name)")
            let (updated, error) = StennyCLI.switchMicrophone(name)
            if let error { show("Couldn't switch to \(name): \(error)") }
            if let updated { status = updated }
        }
        refresh()
    }

    @objc private func togglePause() {
        guard let s = status, s.state == "recording" || s.state == "paused" else {
            AppLog.write("pause/resume ignored, no recording running (state: \(status?.state ?? "none"))")
            NSSound.beep()
            return
        }
        AppLog.write("toggle (was \(s.state))")
        send("toggle")
    }

    @objc private func stop() {
        send("stop")
    }

    private func send(_ cmd: String) {
        Task.detached {
            let s = StennyCLI.send(cmd, timeout: 10)
            AppLog.write(s == nil ? "'\(cmd)' got no answer from the recorder" : "'\(cmd)' → \(s!.state)")
            await MainActor.run {
                if s == nil { NSSound.beep() }
                self.update(s)
            }
        }
    }

    @objc private func openTranscript() {
        guard let path = status?.transcript, !path.isEmpty else { return }
        var components = URLComponents(string: "obsidian://open")!
        components.queryItems = [URLQueryItem(name: "path", value: path)]
        if let url = components.url, NSWorkspace.shared.open(url) { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }


    @objc private func checkForUpdates() {
        Task.detached {
            let result = StennyCLI.checkForUpdate()
            await MainActor.run { self.showUpdate(result) }
        }
    }

    private func showUpdate(_ result: StennyCLI.UpdateCheck?) {
        let alert = NSAlert()
        NSApp.activate()
        guard let result else {
            alert.messageText = "Couldn't check for updates"
            alert.informativeText = "Check your internet connection and try again."
            alert.runModal()
            return
        }
        guard result.available else {
            alert.messageText = "Stenny is up to date"
            alert.informativeText = "You have the newest version, \(result.current)."
            alert.runModal()
            return
        }
        alert.messageText = "Stenny \(result.latest) is available"
        alert.informativeText = "You have \(result.current). Stenny closes and reopens by itself; your courses and transcripts stay as they are."
        alert.addButton(withTitle: "Update")
        alert.addButton(withTitle: "Not Now")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        guard status == nil else {
            let busy = NSAlert()
            busy.messageText = "Stenny is working on a recording"
            busy.informativeText = "Update once the recording and its final transcript are finished."
            busy.runModal()
            return
        }
        AppLog.write("updating from \(result.current) to \(result.latest)")
        // On success this app is closed and replaced, so only a failure ever gets back here
        StennyCLI.runUpdate { failure in
            Task { @MainActor in
                let failed = NSAlert()
                failed.messageText = "The update didn't work"
                failed.informativeText = failure
                NSApp.activate()
                failed.runModal()
            }
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    func confirmQuit() -> NSApplication.TerminateReply {
        guard let s = status, s.state == "recording" || s.state == "paused" else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "A lecture is being recorded"
        alert.informativeText = "Stop it now (the final transcript is made afterwards), or keep it recording in the background? "
            + "You can control it again by reopening Stenny or with `stenny stop`."
        alert.addButton(withTitle: "Stop Recording & Quit")
        alert.addButton(withTitle: "Keep Recording & Quit")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate()
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            _ = StennyCLI.send("stop", timeout: 10)
            return .terminateNow
        case .alertSecondButtonReturn:
            return .terminateNow
        default:
            return .terminateCancel
        }
    }

    // MARK: Display

    private func refresh() {
        guard let button = statusItem.button else { return }
        button.contentTintColor = nil
        let active = status.map { $0.state == "recording" || $0.state == "paused" } ?? false

        switch status?.state {
        case "recording" where status!.inputLost:
            button.image = MenuBarImage.make(symbol: "exclamationmark.triangle", text: clock(status!.recorded))
            statusLineItem.title = "No audio from the microphone · reconnecting …"
            pauseItem.title = "Pause"
            pauseItem.image = NSImage(systemSymbolName: "pause.fill", accessibilityDescription: nil)
        case "recording":
            // No tint: a tinted template image turns dark on a dark menu bar (unreadable time). Untinted,
            // macOS draws it white on dark and black on light; its own orange mic indicator shows recording.
            button.image = MenuBarImage.make(symbol: "record.circle", text: clock(status!.recorded))
            statusLineItem.title = "Recording · \(status!.course) · \(status!.words) words"
            pauseItem.title = "Pause"
            pauseItem.image = NSImage(systemSymbolName: "pause.fill", accessibilityDescription: nil)
        case "paused":
            button.image = MenuBarImage.make(symbol: "pause.circle", text: clock(status!.recorded))
            statusLineItem.title = "Paused for \(clock(status!.currentPause)) · mic off · \(status!.course)"
            pauseItem.title = "Resume"
            pauseItem.image = NSImage(systemSymbolName: "record.circle", accessibilityDescription: nil)
        case "stopping", "finalizing", "done":
            button.image = symbol("text.badge.checkmark")
            statusLineItem.title = status!.state == "stopping" ? "Finishing the live draft …"
                : "Making the final transcript …"
        default:
            button.image = starting ? symbol("ellipsis.circle") : MenuBarImage.figure()
            statusLineItem.title = starting ? "Starting …" : idleMessage
            pauseItem.title = "Pause"
            pauseItem.image = NSImage(systemSymbolName: "pause.fill", accessibilityDescription: nil)
        }
        if let s = status, s.state == "recording", !s.inputLost {
            if levelTimer == nil { levelMeter.show(s.levelDb) }  // the fast meter loop updates it while the menu is open
            levelItem.isHidden = false
        } else {
            levelItem.isHidden = true
        }
        if let s = suggestion, status == nil, !starting {
            suggestItem.title = "Start \(s.course)"
            suggestItem.subtitle = "Calendar: \(timeRange(s))"
            suggestItem.isHidden = false
        } else {
            suggestItem.isHidden = true
        }
        // while recording: the microphone in use (switchable); otherwise the one for the next recording
        let recordingMic = active ? status?.microphone : nil
        micItem.title = "Microphone: " + (recordingMic ?? microphone ?? "System Default")
            + (recordingMic != nil && status?.inputLost == true ? " (no audio)" : "")
        micItem.isEnabled = !starting && (status == nil || recordingMic != nil)
        draftItem.state = liveDraft ? .on : .off
        draftItem.isEnabled = status == nil && !starting
        startItem.isEnabled = status == nil && !starting
        pauseItem.isEnabled = active
        stopItem.isEnabled = active
        openItem.isEnabled = status != nil
    }

    // MARK: Level meter (only while the menu is open)

    /// 30 frames/s: the bar glides between measurements; every other frame asks for a new one (20 ms socket call).
    private func startLevelMeter() {
        guard levelTimer == nil else { return }
        let timer = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.levelTick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        levelTimer = timer
    }

    private func stopLevelMeter() {
        levelTimer?.invalidate()
        levelTimer = nil
    }

    private func levelTick() {
        levelFrame += 1
        if levelFrame % 2 == 0, !levelInFlight, status?.state == "recording" {
            levelInFlight = true
            Task.detached {
                let level = StennyCLI.level()
                await MainActor.run {
                    self.levelInFlight = false
                    if let level, !level.inputLost { self.levelMeter.show(level.db) }
                }
            }
        }
        _ = levelMeter.step()
    }

    private func clock(_ seconds: Int) -> String {
        seconds >= 3600 ? String(format: "%d:%02d:%02d", seconds / 3600, seconds % 3600 / 60, seconds % 60)
            : String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    private func symbol(_ name: String) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "Stenny")
        image?.isTemplate = true
        return image
    }

    // MARK: Menu

    private func buildMenu() {
        menu.autoenablesItems = false
        menu.delegate = self
        statusLineItem.isEnabled = false
        levelItem.isEnabled = false
        levelItem.isHidden = true
        levelItem.view = levelMeter

        configure(suggestItem, title: "Start", symbol: "record.circle.fill", action: #selector(startSuggested))
        suggestItem.isHidden = true
        configure(startItem, title: "Start Lecture", symbol: "record.circle", action: nil)
        startItem.submenu = coursesMenu
        coursesMenu.delegate = self
        configure(pauseItem, title: "Pause", symbol: "pause.fill", action: #selector(togglePause))
        // The shortcut shows in the menu (setShortcut); outside the menu GlobalHotKey handles it
        configure(stopItem, title: "Stop & Make Final Transcript", symbol: "stop.fill", action: #selector(stop))
        // Obsidian is optional: without it the transcript opens in the default Markdown app
        let hasObsidian = NSWorkspace.shared.urlForApplication(toOpen: URL(string: "obsidian://open")!) != nil
        configure(openItem, title: hasObsidian ? "Open Transcript in Obsidian" : "Open Transcript", symbol: "doc.text",
                  action: #selector(openTranscript))
        configure(micItem, title: "Microphone", symbol: "mic", action: nil)
        micItem.submenu = micMenu
        micMenu.delegate = self
        configure(draftItem, title: "Live Transcript While Recording", symbol: "text.append", action: #selector(toggleDraft))

        let updateItem = NSMenuItem()
        configure(updateItem, title: "Check for Updates…", symbol: "arrow.down.circle", action: #selector(checkForUpdates))
        let quitItem = NSMenuItem()
        configure(quitItem, title: "Quit Stenny", symbol: "power", action: #selector(quit))

        [statusLineItem, levelItem, .separator(),
         suggestItem, startItem, pauseItem, stopItem, .separator(),
         micItem, draftItem, .separator(),
         openItem, .separator(),
         updateItem, quitItem].forEach(menu.addItem)
        statusItem.menu = menu
        refresh()
    }

    private func reloadCourses() {
        coursesMenu.removeAllItems()
        let courses = StennyCLI.courses()
        if courses.isEmpty {
            let item = NSMenuItem(title: "No courses yet", action: nil, keyEquivalent: "")
            item.isEnabled = false
            coursesMenu.addItem(item)
        }
        for course in courses {
            let item = NSMenuItem(title: course, action: #selector(startCourse(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = course
            coursesMenu.addItem(item)
        }
        coursesMenu.addItem(.separator())
        let event = NSMenuItem(title: "Talk or Event…", action: #selector(startEvent), keyEquivalent: "")
        event.target = self
        event.image = NSImage(systemSymbolName: "person.wave.2", accessibilityDescription: nil)
        coursesMenu.addItem(event)
        coursesMenu.addItem(.separator())
        let add = NSMenuItem(title: "Add Course…", action: #selector(addCourse), keyEquivalent: "")
        add.target = self
        add.image = NSImage(systemSymbolName: "plus", accessibilityDescription: nil)
        coursesMenu.addItem(add)
        if !courses.isEmpty {
            let hide = NSMenuItem(title: "Hide Course", action: nil, keyEquivalent: "")
            hide.image = NSImage(systemSymbolName: "eye.slash", accessibilityDescription: nil)
            let list = NSMenu()
            for course in courses {
                let item = NSMenuItem(title: course, action: #selector(hideCourse(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = course
                list.addItem(item)
            }
            hide.submenu = list
            coursesMenu.addItem(hide)
        }
    }

    private func reloadMicrophones() {
        micMenu.removeAllItems()
        let (devices, systemDefault) = StennyCLI.devices()
        let defaultItem = NSMenuItem(title: "System Default" + (systemDefault.map { " (\($0))" } ?? ""),
                                     action: #selector(selectMicrophone(_:)), keyEquivalent: "")
        defaultItem.target = self
        let active = status.map { $0.state == "recording" || $0.state == "paused" } ?? false
        let current = active ? status?.microphone : microphone   // the checkmark: in use, or chosen for next time
        defaultItem.state = !active && microphone == nil ? .on : .off
        micMenu.addItem(defaultItem)
        micMenu.addItem(.separator())
        for device in devices {
            let item = NSMenuItem(title: device, action: #selector(selectMicrophone(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = device
            item.state = device == current ? .on : .off
            micMenu.addItem(item)
        }
        if let chosen = current, !devices.contains(chosen) {
            let missing = NSMenuItem(title: "\(chosen) (not connected)", action: nil, keyEquivalent: "")
            missing.isEnabled = false
            missing.state = .on
            micMenu.addItem(missing)
        }
    }

    private func configure(_ item: NSMenuItem, title: String, symbol: String, action: Selector?) {
        item.title = title
        item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        item.target = self
        item.action = action
    }

    // MARK: Alerts

    private func show(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Stenny"
        alert.informativeText = message
        NSApp.activate()
        alert.runModal()
    }
}

extension LectureController: UNUserNotificationCenterDelegate {
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions { [.banner, .sound] }

    /// "Start Recording" starts it, closing it does nothing; clicking the notification itself opens the menu.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let course = response.notification.request.content.userInfo["course"] as? String
        let action = response.actionIdentifier
        await MainActor.run {
            if action == "start", let course {
                self.start(course: course)
            } else if action == UNNotificationDefaultActionIdentifier {
                self.statusItem.button?.performClick(nil)
            }
        }
    }
}

extension LectureController: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) {
        if menu === coursesMenu { reloadCourses() } else if menu === micMenu { reloadMicrophones() } else {
            poll()
            startLevelMeter()
        }
    }

    func menuDidClose(_ menu: NSMenu) {
        if menu === self.menu { stopLevelMeter() }
    }
}
