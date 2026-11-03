import AppKit

/// What the user confirmed the lecturer agreed to, per course, kept in this app's defaults.
enum Consent {
    enum Use: String { case record, share }

    private static let key = "lecturerAgreed"

    static func given(_ use: Use, course: String, isEvent: Bool) -> Bool {
        (UserDefaults.standard.stringArray(forKey: key) ?? []).contains(id(use, course, isEvent))
    }

    static func give(_ use: Use, course: String, isEvent: Bool) {
        var all = Set(UserDefaults.standard.stringArray(forKey: key) ?? [])
        all.insert(id(use, course, isEvent))
        UserDefaults.standard.set(all.sorted(), forKey: key)
    }

    private static func id(_ use: Use, _ course: String, _ isEvent: Bool) -> String {
        "\(use.rawValue)/\(isEvent ? "event" : "course")/\(course)"
    }
}

/// Send Feedback… and Check for Updates…, the same from the menu bar menu and from Stenny's window.
@MainActor
enum AppActions {
    // MARK: Feedback

    static func sendFeedback() {
        let alert = NSAlert()
        alert.messageText = "Send feedback"
        alert.informativeText = "What works, what doesn't, what you'd want. It goes straight to the person who makes Stenny."
        let stack = NSStackView(frame: NSRect(x: 0, y: 0, width: 340, height: 196))
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        let scroll = NSTextView.scrollableTextView()
        let message = scroll.documentView as! NSTextView
        message.font = .systemFont(ofSize: NSFont.systemFontSize)
        message.isRichText = false
        scroll.borderType = .bezelBorder
        let contact = NSTextField(frame: NSRect(x: 0, y: 0, width: 340, height: 24))
        contact.placeholderString = "Your email, if you'd like a reply (optional)"
        let attach = NSButton(checkboxWithTitle: "Include Stenny's version, this Mac's model and the last lines of Stenny's log",
                              target: nil, action: nil)
        attach.lineBreakMode = .byWordWrapping
        [scroll, contact, attach].forEach { view in
            stack.addArrangedSubview(view)
            view.widthAnchor.constraint(equalToConstant: 340).isActive = true
        }
        scroll.heightAnchor.constraint(equalToConstant: 120).isActive = true
        alert.accessoryView = stack
        alert.addButton(withTitle: "Send")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = message
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let text = message.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return show("Your message was empty, so nothing was sent.") }
        let reply = contact.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let details = attach.state == .on
        Task.detached {
            let failure = await StennyCLI.sendFeedback(text, contact: reply, includeDetails: details)
            await MainActor.run {
                guard let failure else { return show("Thank you! Your feedback was sent.") }
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                show("\(failure)\n\nYour message is on the clipboard, so it isn't lost.")
            }
        }
    }

    // MARK: Updates

    static func checkForUpdates() {
        Task.detached {
            let result = StennyCLI.checkForUpdate()
            await MainActor.run { showUpdate(result) }
        }
    }

    private static func showUpdate(_ result: StennyCLI.UpdateCheck?) {
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
        // a running recording answers on the control socket, whichever window asked
        guard StennyCLI.send("status", timeout: 2) == nil else {
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

    // MARK: The lecturer's OK

    /// Asked once per course (or talk) right before its first recording and before its first share; a "yes" is
    /// remembered on this Mac. Recording and sharing are asked separately: agreeing to one isn't agreeing to both.
    /// Returns whether to go ahead.
    static func lecturerAgreed(to use: Consent.Use, course: String, isEvent: Bool) -> Bool {
        if Consent.given(use, course: course, isEvent: isEvent) { return true }
        let who = isEvent ? "speaker" : "lecturer"
        let alert = NSAlert()
        alert.messageText = "Did the \(who) agree to \(use == .record ? "being recorded" : "this")?"
        alert.informativeText = (use == .record
            ? "Recording \(isEvent ? "a talk" : "a lecture") usually needs the \(who)'s OK."
            : "Transcripts of \(isEvent ? "a talk" : "a lecture") usually need the \(who)'s OK before they're passed on.")
            + " Stenny asks once for \(course)."
        alert.addButton(withTitle: use == .record ? "Yes, Record" : "Yes, Share")
        alert.addButton(withTitle: "Not Yet")
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn else { return false }
        Consent.give(use, course: course, isEvent: isEvent)
        return true
    }

    private static func show(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Stenny"
        alert.informativeText = message
        NSApp.activate()
        alert.runModal()
    }
}
