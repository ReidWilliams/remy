# remy

A keyboard-driven terminal UI for managing Apple Reminders on macOS.

Designed for quickly reviewing and rescheduling reminders without leaving the terminal. Reminders are displayed in a spreadsheet-like table — navigate with arrow keys, edit dates and times inline, and check off items as you go.

## Requirements

- macOS (uses EventKit via a small Swift helper binary)
- Python 3
- Swift compiler (`swiftc`, included with Xcode or the Command Line Tools)
- Reminders access granted in **System Settings › Privacy & Security › Reminders**

## Build

```sh
make build
```

This compiles `swift/main.swift` into the `remy-helper` binary.

## Run

```sh
python3 remy.py
```

Or use the convenience target (builds then runs):

```sh
make run
```

## Usage

Reminders are grouped into three tabs — **Today**, **Upcoming** (next 7 days), and **Future**.

| Key | Action |
|-----|--------|
| `↑ ↓` | Move between reminders |
| `← →` | Move between columns (Title, Due Date, Time) |
| `tab` | Cycle tabs |
| `enter` | Edit selected field |
| `← →` *(editing date/time)* | Change value by one day / one hour |
| `[ ]` *(editing date)* | Skip back / forward one month |
| `enter` *(editing)* | Confirm change |
| `esc` *(editing)* | Cancel change |
| `space` | Mark complete / incomplete |
| `r` | Refresh from Reminders |
| `n` | New reminder |
| `?` | Help screen |
| `q` | Quit |

## Configuration

The Reminders list name is set at the top of `remy.py`:

```python
LIST_NAME = "Reminders"
```

Change this to match whichever list you want remy to manage.
