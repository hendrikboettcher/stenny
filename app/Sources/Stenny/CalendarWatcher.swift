import EventKit
import Foundation

/// Finds the lecture that is on now in the user's calendars (macOS Calendar, incl. subscribed and
/// Google calendars), so Stenny can offer it with one click. Which course an event belongs to is
/// decided by `stenny match-course` (same rules everywhere, see config.course_for_title).
@MainActor
final class CalendarWatcher {
    struct Lecture: Equatable {
        let course: String
        let title: String
        let start: Date
        let end: Date
        let eventID: String
    }

    /// Offer a lecture this long before it starts, and until it ends.
    static let lead: TimeInterval = 10 * 60
    /// Shorter events (deadlines, reminders) are never offered as a lecture.
    static let minimumLength: TimeInterval = 20 * 60

    private let store = EKEventStore()
    private var courseForTitle: [String: String?] = [:]  // cache: event title → course (nil = none)

    var hasAccess: Bool { EKEventStore.authorizationStatus(for: .event) == .fullAccess }

    /// Asks once (macOS shows its permission dialog the first time); later calls return the answer.
    func requestAccess() async -> Bool {
        switch EKEventStore.authorizationStatus(for: .event) {
        case .fullAccess: return true
        case .notDetermined: return (try? await store.requestFullAccessToEvents()) ?? false
        default: return false
        }
    }

    /// The matching lecture that is on now or starts within `lead`, if any.
    func current(now: Date = Date()) async -> Lecture? {
        guard hasAccess else { return nil }
        let predicate = store.predicateForEvents(withStart: now.addingTimeInterval(-12 * 3600),
                                                 end: now.addingTimeInterval(Self.lead), calendars: nil)
        let events = store.events(matching: predicate)
            // Lectures only: Canvas also puts deadlines in the feed ("Deadline: …"), as events of a few minutes
            .filter { !$0.isAllDay && $0.endDate.timeIntervalSince($0.startDate) >= Self.minimumLength }
            .filter { $0.endDate > now && $0.startDate <= now.addingTimeInterval(Self.lead) }
            .sorted { $0.startDate < $1.startDate }
        let unknown = Array(Set(events.compactMap(\.title).filter { courseForTitle[$0] == nil }))
        if !unknown.isEmpty {
            let matches = await Task.detached { StennyCLI.matchCourses(unknown) }.value
            for title in unknown { courseForTitle[title] = .some(matches[title] ?? nil) }
        }
        for event in events {
            guard let title = event.title, let course = courseForTitle[title] ?? nil else { continue }
            return Lecture(course: course, title: title, start: event.startDate, end: event.endDate,
                           eventID: event.eventIdentifier ?? "\(title)\(event.startDate.timeIntervalSince1970)")
        }
        return nil
    }

    /// Title of an event that is on now (or within `lead`) and belongs to no course, e.g. a guest talk:
    /// the suggested title for "Other…".
    func otherEvent(now: Date = Date()) async -> String? {
        guard hasAccess else { return nil }
        _ = await current(now: now)  // fills the title → course cache for the events around now
        let predicate = store.predicateForEvents(withStart: now.addingTimeInterval(-12 * 3600),
                                                 end: now.addingTimeInterval(Self.lead), calendars: nil)
        return store.events(matching: predicate)
            .filter { !$0.isAllDay && $0.endDate.timeIntervalSince($0.startDate) >= Self.minimumLength }
            .filter { $0.endDate > now && $0.startDate <= now.addingTimeInterval(Self.lead) }
            .sorted { $0.startDate < $1.startDate }
            .compactMap(\.title)
            .first { (courseForTitle[$0] ?? nil) == nil }
    }

    /// Forget cached matches (after the course list may have changed).
    func reset() { courseForTitle = [:] }
}
