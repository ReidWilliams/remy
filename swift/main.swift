import Foundation
import EventKit

// ── Argument parsing ──────────────────────────────────────────────────────────

let args = CommandLine.arguments

func argValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

guard args.count >= 2 else {
    fputs("usage: remy-helper <list|update|create> --list <name> [options]\n", stderr)
    exit(1)
}

let command  = args[1]

guard let listName = argValue("--list") else {
    fputs("error: --list <name> required\n", stderr)
    exit(1)
}

// ── EventKit access ───────────────────────────────────────────────────────────

let store = EKEventStore()
let sema  = DispatchSemaphore(value: 0)

var accessGranted = false
store.requestFullAccessToReminders { granted, error in
    if let error = error {
        fputs("error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
    accessGranted = granted
    sema.signal()
}
sema.wait()

guard accessGranted else {
    fputs("error: reminders access denied — grant access in System Settings › Privacy & Security › Reminders\n", stderr)
    exit(1)
}

guard let reminderList = store.calendars(for: .reminder).first(where: { $0.title == listName }) else {
    let available = store.calendars(for: .reminder).map { $0.title }.joined(separator: ", ")
    fputs("error: list '\(listName)' not found. available: \(available)\n", stderr)
    exit(1)
}

// ── Shared helpers ────────────────────────────────────────────────────────────

struct ReminderItem: Codable {
    let id:    String
    let title: String
    let date:  String?  // "YYYY-MM-DD" or null
    let hour:  Int?     // 0-23 or null
}

func fetchIncomplete() -> [EKReminder] {
    var result: [EKReminder] = []
    let pred = store.predicateForIncompleteReminders(
        withDueDateStarting: nil, ending: nil, calendars: [reminderList]
    )
    store.fetchReminders(matching: pred) { reminders in
        result = reminders ?? []
        sema.signal()
    }
    sema.wait()
    return result
}

func reminderToItem(_ r: EKReminder) -> ReminderItem {
    var dateStr: String? = nil
    var hour:    Int?    = nil
    if let due = r.dueDateComponents {
        let hasHour = due.hour != nil
        // Convert via Date so that a UTC-based calendar in the stored components
        // is correctly mapped to local year/month/day/hour before we output them.
        let cal = due.calendar ?? Calendar.current
        if let d = cal.date(from: due) {
            let local = Calendar.current.dateComponents([.year, .month, .day, .hour], from: d)
            if let y = local.year, let m = local.month, let day = local.day {
                dateStr = String(format: "%04d-%02d-%02d", y, m, day)
            }
            if hasHour { hour = local.hour }
        } else {
            // Fallback: use raw components as-is
            if let y = due.year, let m = due.month, let d = due.day {
                dateStr = String(format: "%04d-%02d-%02d", y, m, d)
            }
            hour = due.hour
        }
    }
    return ReminderItem(id: r.calendarItemIdentifier, title: r.title ?? "", date: dateStr, hour: hour)
}

func makeDateComponents(dateStr: String, hour: Int?) -> DateComponents {
    let parts = dateStr.split(separator: "-").compactMap { Int($0) }
    var c = DateComponents()
    c.calendar = Calendar.current   // anchor to local timezone so EventKit stores correctly
    c.year  = parts[0]
    c.month = parts[1]
    c.day   = parts[2]
    if let h = hour {
        c.hour   = h
        c.minute = 0
        c.second = 0
    }
    return c
}

// ── list ──────────────────────────────────────────────────────────────────────

if command == "list" {
    let items   = fetchIncomplete().map { reminderToItem($0) }
    let encoder = JSONEncoder()
    encoder.outputFormatting = .prettyPrinted
    let data = try! encoder.encode(items)
    print(String(data: data, encoding: .utf8)!)

// ── update <id> ───────────────────────────────────────────────────────────────

} else if command == "update" {
    guard args.count >= 3 else {
        fputs("error: update requires <id>\n", stderr)
        exit(1)
    }
    let targetId = args[2]

    guard let calItem  = store.calendarItem(withIdentifier: targetId),
          let reminder = calItem as? EKReminder else {
        fputs("error: reminder not found: \(targetId)\n", stderr)
        exit(1)
    }

    if let title = argValue("--title") {
        reminder.title = title
    }

    let dateArg = argValue("--date")
    let hourArg = argValue("--hour").flatMap { Int($0) }

    if dateArg == "null" {
        // clear date and any alarms (alarm without date makes no sense)
        reminder.dueDateComponents = nil
        reminder.alarms            = nil
    } else if let dateStr = dateArg {
        reminder.dueDateComponents = makeDateComponents(dateStr: dateStr, hour: hourArg)
        // sync alarm to due time: present when a time is set, absent when date-only
        reminder.alarms = hourArg != nil ? [EKAlarm(relativeOffset: 0)] : nil
    } else if let h = hourArg, var existing = reminder.dueDateComponents {
        // hour-only update — preserve existing date
        existing.hour   = h
        existing.minute = 0
        existing.second = 0
        reminder.dueDateComponents = existing
        reminder.alarms = [EKAlarm(relativeOffset: 0)]
    }

    do {
        try store.save(reminder, commit: true)
    } catch {
        fputs("error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

// ── create ────────────────────────────────────────────────────────────────────

} else if command == "create" {
    let reminder      = EKReminder(eventStore: store)
    reminder.calendar = reminderList
    reminder.title    = argValue("--title") ?? "New Reminder"

    if let dateStr = argValue("--date"), dateStr != "null" {
        let hourArg = argValue("--hour").flatMap { Int($0) }
        reminder.dueDateComponents = makeDateComponents(dateStr: dateStr, hour: hourArg)
        if hourArg != nil {
            reminder.alarms = [EKAlarm(relativeOffset: 0)]
        }
    }

    do {
        try store.save(reminder, commit: true)
        print(reminder.calendarItemIdentifier)
    } catch {
        fputs("error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

// ── complete <id> ─────────────────────────────────────────────────────────────

} else if command == "complete" {
    guard args.count >= 3 else {
        fputs("error: complete requires <id>\n", stderr)
        exit(1)
    }
    let targetId = args[2]
    guard let calItem  = store.calendarItem(withIdentifier: targetId),
          let reminder = calItem as? EKReminder else {
        fputs("error: reminder not found: \(targetId)\n", stderr)
        exit(1)
    }
    reminder.isCompleted    = true
    reminder.completionDate = Date()
    do {
        try store.save(reminder, commit: true)
    } catch {
        fputs("error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

// ── uncomplete <id> ───────────────────────────────────────────────────────────

} else if command == "uncomplete" {
    guard args.count >= 3 else {
        fputs("error: uncomplete requires <id>\n", stderr)
        exit(1)
    }
    let targetId = args[2]
    guard let calItem  = store.calendarItem(withIdentifier: targetId),
          let reminder = calItem as? EKReminder else {
        fputs("error: reminder not found: \(targetId)\n", stderr)
        exit(1)
    }
    reminder.isCompleted    = false
    reminder.completionDate = nil
    do {
        try store.save(reminder, commit: true)
    } catch {
        fputs("error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

} else {
    fputs("error: unknown command '\(command)'\n", stderr)
    exit(1)
}
