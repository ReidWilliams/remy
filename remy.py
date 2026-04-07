import calendar
import curses
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

# ── Helper binary ─────────────────────────────────────────────────────────────

HELPER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remy-helper")
LIST_NAMES = ["Reminders", "Recurring"]

# ── Layout constants ──────────────────────────────────────────────────────────

TITLE_WIDTH = 36
DATE_WIDTH  = 14
TIME_WIDTH  = 8
MIN_WIDTH   = TITLE_WIDTH + DATE_WIDTH + TIME_WIDTH + 8  # 66
MIN_HEIGHT  = 6  # tabs + sep + header + sep + 1 row + status

COL_TITLE = 0
COL_DATE  = 1
COL_TIME  = 2
NUM_COLS  = 3

TAB_TODAY    = 0
TAB_UPCOMING = 1
TAB_FUTURE   = 2

# ── Swift helper calls ────────────────────────────────────────────────────────

def load_reminders():
    """Fetch all incomplete reminders from the helper. Returns list of dicts."""
    cmd = [HELPER, "list"]
    for name in LIST_NAMES:
        cmd += ["--list", name]
    result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "remy-helper list failed")
    items = json.loads(result.stdout)
    return [
        {
            "id":        item["id"],
            "title":     item["title"],
            "date":      date.fromisoformat(item["date"]) if item.get("date") else None,
            "hour":      item.get("hour"),
            "completed": False,
            "list":      item["list"],
        }
        for item in items
    ]


def toggle_completion(r):
    """Toggle completed state, persisting to Reminders. Returns error string or None."""
    new_state = not r.get("completed", False)
    cmd = [HELPER, "complete" if new_state else "uncomplete", r["id"], "--list", r.get("list", LIST_NAMES[0])]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            return result.stderr.strip() or "toggle failed"
        r["completed"] = new_state
    except FileNotFoundError:
        return f"binary not found: {HELPER}"
    return None


def save_reminder(r):
    """Create or update a reminder. Sets r['id'] when newly created.
    Returns an error string on failure, or None on success."""
    date_arg  = r["date"].isoformat() if r["date"] else "null"
    list_name = r.get("list", LIST_NAMES[0])
    try:
        if not r.get("id"):
            r["list"] = list_name
            cmd = [HELPER, "create", "--list", list_name,
                   "--title", r["title"], "--date", date_arg]
            if r["hour"] is not None:
                cmd += ["--hour", str(r["hour"])]
            result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if result.returncode != 0:
                return result.stderr.strip() or "create failed"
            r["id"] = result.stdout.strip()
        else:
            cmd = [HELPER, "update", r["id"], "--list", list_name,
                   "--title", r["title"], "--date", date_arg]
            if r["hour"] is not None:
                cmd += ["--hour", str(r["hour"])]
            result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if result.returncode != 0:
                return result.stderr.strip() or "update failed"
    except FileNotFoundError:
        return f"binary not found: {HELPER} — run 'make build'"
    return None

# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_date(d):
    if d is None:
        return "—"
    today = date.today()
    if d == today:
        return "today"
    elif d == today + timedelta(days=1):
        return "tomorrow"
    else:
        return d.strftime("%a %b %-d")


def fmt_time(hour):
    if hour is None:
        return "—"
    if hour == 0:
        return "12 AM"
    elif hour < 12:
        return f"{hour} AM"
    elif hour == 12:
        return "12 PM"
    else:
        return f"{hour - 12} PM"

# ── Sorting / views ───────────────────────────────────────────────────────────

def sort_key(r):
    today = date.today()
    d = r["date"] if r["date"] is not None else today
    h = r["hour"] if r["hour"] is not None else 99
    return (d, h)


def min_hour(r):
    if r["date"] is None or r["date"] == date.today():
        return datetime.now().hour
    return 0


SEP = {"_sep": True}  # sentinel for day-separator rows

def is_sep(r):
    return r is SEP or r.get("_sep", False)


def _split_sort(items):
    """Return (done, undone) each sorted by (date, hour); unsaved new items sort first on ties."""
    key = lambda r: (r["date"] or date.today(), r["hour"] if r["hour"] is not None else 99, 0 if r["id"] is None else 1)
    done   = sorted([r for r in items if     r.get("completed")], key=key)
    undone = sorted([r for r in items if not r.get("completed")], key=key)
    return done, undone


def build_view(reminders, tab):
    today    = date.today()
    upcoming = today + timedelta(days=7)
    if tab == TAB_TODAY:
        items = [r for r in reminders if r["date"] is None or r["date"] <= today]
        done, undone = _split_sort(items)
        return done + undone
    elif tab == TAB_UPCOMING:
        items = [r for r in reminders if r["date"] is not None and today < r["date"] <= upcoming]
        done, undone = _split_sort(items)
        # date separators only among undone items
        result    = list(done)
        last_date = None
        for r in undone:
            if last_date is not None and r["date"] != last_date:
                result.append(SEP)
            result.append(r)
            last_date = r["date"]
        return result
    else:  # TAB_FUTURE
        items = [r for r in reminders if r["date"] is not None and r["date"] > upcoming]
        done, undone = _split_sort(items)
        return done + undone


def skip_sep(view, row, direction):
    """Advance row in direction while it points to a separator, staying in bounds."""
    while 0 <= row < len(view) and is_sep(view[row]):
        row += direction
    return max(0, min(len(view) - 1, row))

# ── Drawing ───────────────────────────────────────────────────────────────────

def draw_help(stdscr):
    stdscr.erase()
    sections = [
        ("Navigation", [
            ("↑ ↓ ← →",     "navigate"),
            ("tab",         "cycle tabs  (Today → Upcoming → Future)"),
        ]),
        ("Editing", [
            ("enter",       "edit selected field"),
            ("← →",         "change value  (date / time)"),
            ("[ ]",          "skip back / forward one month  (date)"),
            ("enter",       "confirm change"),
            ("esc",         "cancel change"),
        ]),
        ("Actions", [
            ("space",       "mark complete / mark incomplete"),
            ("n",           "new reminder  (opens in Upcoming)"),
            ("r",           "refresh from Reminders"),
        ]),
        ("", [
            ("?",           "back to app"),
            ("q",           "quit"),
        ]),
    ]
    y = 1
    for heading, bindings in sections:
        if heading:
            stdscr.addstr(y, 2, heading, curses.color_pair(4) | curses.A_BOLD)
            y += 1
        for key, desc in bindings:
            stdscr.addstr(y, 4, f"{key:<14}{desc}")
            y += 1
        y += 1
    stdscr.refresh()
    stdscr.getch()


def draw_tabs(stdscr, active_tab):
    labels = ["  Today  ", "  Upcoming  ", "  Future  "]
    x = 0
    for i, label in enumerate(labels):
        attr = (curses.color_pair(1) | curses.A_BOLD) if i == active_tab else curses.color_pair(3)
        stdscr.addstr(0, x, label, attr)
        x += len(label) + 1

# ── Key reading ───────────────────────────────────────────────────────────────

def add_months(d, n):
    """Return date d shifted by n months, clamping the day to the last day of the target month."""
    month = d.month - 1 + n
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

# ── Main ──────────────────────────────────────────────────────────────────────

def main(stdscr, reminders):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, 8)                    # active tab / selected cell (dark gray bg)
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # editing cell
    curses.init_pair(3, -1, -1)                                    # normal text
    curses.init_pair(4, curses.COLOR_CYAN, -1)                    # header / status
    curses.init_pair(5, curses.COLOR_RED,  -1)                    # error
    curses.init_pair(6, 8, -1)                                    # day separator dots (dark gray)
    curses.init_pair(7, 238, -1)                                  # completed item text (dark gray)

    active_tab = TAB_TODAY
    view       = build_view(reminders, active_tab)

    tab_row = [0, 0, 0]
    tab_col = [0, 0, 0]

    editing     = False
    original    = None
    text_cursor = 0
    is_new      = False
    error_msg   = None  # shown in status bar after a failed save

    while True:
        row = tab_row[active_tab]
        col = tab_col[active_tab]

        in_text_edit = editing and col == COL_TITLE
        curses.curs_set(0)  # always hidden; title cursor drawn explicitly below
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        if h < MIN_HEIGHT or w < MIN_WIDTH:
            msg = f"Window too small ({w}x{h}) — need {MIN_WIDTH}x{MIN_HEIGHT}"
            stdscr.addstr(0, 0, msg[:w])
            stdscr.refresh()
            stdscr.getch()
            continue

        # ── Tabs ──────────────────────────────────────────────────────────────
        draw_tabs(stdscr, active_tab)
        stdscr.addstr(1, 0, "─" * min(TITLE_WIDTH + DATE_WIDTH + TIME_WIDTH + 8, w - 1))

        # ── Column headers ────────────────────────────────────────────────────
        stdscr.addstr(
            2, 4,
            f"{'TITLE':<{TITLE_WIDTH}}  {'DUE DATE':<{DATE_WIDTH}}  TIME",
            curses.color_pair(4) | curses.A_BOLD,
        )
        stdscr.addstr(3, 2, "─" * min(TITLE_WIDTH + DATE_WIDTH + TIME_WIDTH + 6, w - 3))

        # ── Reminder rows ─────────────────────────────────────────────────────
        if not view:
            stdscr.addstr(4, 2, "(no reminders)", curses.color_pair(3))
        else:
            for i, r in enumerate(view):
                y = i + 4
                if y >= h - 1:
                    break

                if is_sep(r):
                    dot_line = ("·  " * 30)[:TITLE_WIDTH + DATE_WIDTH + TIME_WIDTH + 6]
                    stdscr.addstr(y, 2, dot_line, curses.color_pair(6))
                    continue

                title    = r["title"]
                date_str = fmt_date(r["date"])
                time_str = fmt_time(r["hour"])
                done     = r.get("completed", False)
                check    = "✓" if done else " "
                dim_pair = curses.color_pair(7) if done else curses.color_pair(3)

                stdscr.addstr(y, 0, "▶" if i == row else " ")
                stdscr.addstr(y, 2, check, dim_pair)

                # Title cell
                display = title if len(title) <= TITLE_WIDTH else title[:TITLE_WIDTH - 1] + "…"
                if i == row and col == COL_TITLE:
                    if editing:
                        # Draw explicit block cursor via A_REVERSE at text_cursor position
                        attr   = curses.color_pair(2) | curses.A_BOLD
                        padded = f"{display:<{TITLE_WIDTH}}"
                        cpos   = min(text_cursor, TITLE_WIDTH - 1)
                        stdscr.addstr(y, 4,          padded[:cpos],      attr)
                        stdscr.addstr(y, 4 + cpos,   padded[cpos],       attr | curses.A_REVERSE)
                        stdscr.addstr(y, 4 + cpos+1, padded[cpos+1:],    attr)
                    else:
                        stdscr.addstr(y, 4, f"{display:<{TITLE_WIDTH}}", curses.color_pair(1))
                else:
                    stdscr.addstr(y, 4, f"{display:<{TITLE_WIDTH}}", dim_pair)

                # Date and time cells
                for c, text, width in [
                    (COL_DATE, date_str, DATE_WIDTH),
                    (COL_TIME, time_str, TIME_WIDTH),
                ]:
                    x_off = 4 + TITLE_WIDTH + 2 + (0 if c == COL_DATE else DATE_WIDTH + 2)
                    if i == row and c == col:
                        attr = (curses.color_pair(2) | curses.A_BOLD) if editing else curses.color_pair(1)
                    else:
                        attr = dim_pair
                    stdscr.addstr(y, x_off, f"{text:<{width}}", attr)

        # ── Status bar ────────────────────────────────────────────────────────
        if error_msg:
            stdscr.addstr(h - 1, 0, error_msg[: w - 1], curses.color_pair(5))
        elif in_text_edit:
            stdscr.addstr(h - 1, 0, "type title   ←→ move cursor   enter confirm   esc cancel"[: w - 1], curses.color_pair(4))
        elif editing:
            stdscr.addstr(h - 1, 0, "↑↓ change   [ ] skip month   enter confirm   esc cancel"[: w - 1], curses.color_pair(4))
        else:
            stdscr.addstr(h - 1, 0, "? help   q quit"[: w - 1], curses.color_pair(4))


        stdscr.refresh()
        key = stdscr.getch()

        # any keypress clears error message
        if error_msg:
            error_msg = None

        # ── Edit mode ─────────────────────────────────────────────────────────
        if editing:
            r = view[row]

            # ── Text edit (title) ──────────────────────────────────────────────
            if col == COL_TITLE:
                if key in (curses.KEY_ENTER, 10, 13):
                    err = save_reminder(r)
                    if err:
                        error_msg = f"save failed: {err}"
                    else:
                        saved   = view[row]
                        view[:] = build_view(reminders, active_tab)
                        tab_row[active_tab] = next(
                            (i for i, x in enumerate(view) if x is saved), 0
                        )
                    editing     = False
                    original    = None
                    is_new      = False
                    text_cursor = 0

                elif key == ord("\t"):  # Tab: save title, move to date cell
                    err = save_reminder(r)
                    if err:
                        error_msg = f"save failed: {err}"
                    else:
                        saved   = view[row]
                        view[:] = build_view(reminders, active_tab)
                        tab_row[active_tab] = next(
                            (i for i, x in enumerate(view) if x is saved), 0
                        )
                        is_new      = False
                        text_cursor = 0
                        original    = saved["date"]
                        if saved["date"] is None:
                            saved["date"] = date.today()
                        tab_col[active_tab] = COL_DATE
                        # editing stays True — now in date edit mode

                elif key == 27:  # Esc
                    if is_new:
                        reminders.remove(r)
                        view.remove(r)
                        tab_row[active_tab] = min(row, max(0, len(view) - 1))
                    else:
                        r["title"] = original
                    editing     = False
                    original    = None
                    is_new      = False
                    text_cursor = 0

                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    if text_cursor > 0:
                        r["title"]  = r["title"][:text_cursor - 1] + r["title"][text_cursor:]
                        text_cursor -= 1

                elif key == curses.KEY_DC:
                    if text_cursor < len(r["title"]):
                        r["title"] = r["title"][:text_cursor] + r["title"][text_cursor + 1:]

                elif key == curses.KEY_LEFT:
                    text_cursor = max(0, text_cursor - 1)

                elif key == curses.KEY_RIGHT:
                    text_cursor = min(len(r["title"]), text_cursor + 1)

                elif key == curses.KEY_HOME:
                    text_cursor = 0

                elif key == curses.KEY_END:
                    text_cursor = len(r["title"])

                elif 32 <= key <= 126:
                    r["title"]  = r["title"][:text_cursor] + chr(key) + r["title"][text_cursor:]
                    text_cursor += 1

            # ── Value edit (date / time) ───────────────────────────────────────
            else:
                if key == curses.KEY_RIGHT:
                    if col == COL_DATE:
                        if r["date"] is None:
                            r["date"] = date.today()
                        else:
                            r["date"] += timedelta(days=1)
                    else:
                        if r["hour"] is None:
                            r["hour"] = min_hour(r)
                        elif r["hour"] < 23:
                            r["hour"] += 1

                elif key == curses.KEY_LEFT:
                    if col == COL_DATE:
                        if r["date"] is None:
                            pass
                        elif r["date"] == date.today():
                            r["date"] = None
                        else:
                            r["date"] -= timedelta(days=1)
                            if r["date"] == date.today() and r["hour"] is not None:
                                r["hour"] = max(r["hour"], min_hour(r))
                    else:
                        if r["hour"] is not None:
                            floor = min_hour(r)
                            if r["hour"] <= floor:
                                r["hour"] = None
                            else:
                                r["hour"] -= 1

                elif key == ord(']'):
                    if col == COL_DATE:
                        if r["date"] is None:
                            r["date"] = add_months(date.today(), 1)
                        else:
                            r["date"] = add_months(r["date"], 1)

                elif key == ord('['):
                    if col == COL_DATE:
                        if r["date"] is not None:
                            new_date = add_months(r["date"], -1)
                            if new_date <= date.today():
                                r["date"] = date.today()
                            else:
                                r["date"] = new_date

                elif key in (curses.KEY_ENTER, 10, 13):
                    # no time without a date
                    if col == COL_DATE and r["date"] is None:
                        r["hour"] = None
                    # time-first edit: anchor date to today
                    if col == COL_TIME and r["date"] is None and r["hour"] is not None:
                        r["date"] = date.today()
                    err = save_reminder(r)
                    if err:
                        # revert the in-memory change and show error
                        if col == COL_DATE:
                            r["date"] = original
                        else:
                            r["hour"] = original
                        error_msg = f"save failed: {err}"
                    else:
                        saved  = view[row]
                        # Re-sort current view items without re-filtering by tab,
                        # so a rescheduled reminder stays visible until the user
                        # switches tabs (sticky behaviour).
                        items = [r for r in view if not is_sep(r)]
                        done, undone = _split_sort(items)
                        if active_tab == TAB_UPCOMING:
                            result    = list(done)
                            last_date = None
                            for r in undone:
                                if last_date is not None and r["date"] != last_date:
                                    result.append(SEP)
                                result.append(r)
                                last_date = r["date"]
                            view[:] = result
                        else:
                            view[:] = done + undone
                        tab_row[active_tab] = next(
                            (i for i, x in enumerate(view) if x is saved), 0
                        )
                    editing  = False
                    original = None

                elif key == ord("\t"):  # Tab: save, advance to next cell (or exit if rightmost)
                    if col == COL_DATE and r["date"] is None:
                        r["hour"] = None
                    if col == COL_TIME and r["date"] is None and r["hour"] is not None:
                        r["date"] = date.today()
                    err = save_reminder(r)
                    if err:
                        if col == COL_DATE:
                            r["date"] = original
                        else:
                            r["hour"] = original
                        error_msg = f"save failed: {err}"
                    else:
                        saved = view[row]
                        items = [x for x in view if not is_sep(x)]
                        done, undone = _split_sort(items)
                        if active_tab == TAB_UPCOMING:
                            result    = list(done)
                            last_date = None
                            for x in undone:
                                if last_date is not None and x["date"] != last_date:
                                    result.append(SEP)
                                result.append(x)
                                last_date = x["date"]
                            view[:] = result
                        else:
                            view[:] = done + undone
                        tab_row[active_tab] = next(
                            (i for i, x in enumerate(view) if x is saved), 0
                        )
                        if col == COL_DATE:
                            # advance to time cell
                            original = saved["hour"]
                            if saved["hour"] is None:
                                saved["hour"] = min_hour(saved)
                            tab_col[active_tab] = COL_TIME
                            # editing stays True
                        else:
                            # COL_TIME is rightmost — just exit
                            editing  = False
                            original = None

                elif key == 27:  # Esc — revert (no save)
                    if col == COL_DATE:
                        r["date"] = original
                    else:
                        r["hour"] = original
                    editing  = False
                    original = None

        # ── Normal mode ───────────────────────────────────────────────────────
        else:
            if key == ord("q"):
                break

            elif key == ord("?"):
                draw_help(stdscr)

            elif key == ord(" "):
                if view and not is_sep(view[row]) and view[row].get("id"):
                    err = toggle_completion(view[row])
                    if err:
                        error_msg = f"toggle failed: {err}"
                    else:
                        saved   = view[row]
                        view[:] = build_view(reminders, active_tab)
                        tab_row[active_tab] = next(
                            (i for i, x in enumerate(view) if x is saved), 0
                        )

            elif key == ord("r"):
                try:
                    reminders[:] = load_reminders()
                except Exception as e:
                    error_msg = f"refresh failed: {e}"
                else:
                    view = build_view(reminders, active_tab)
                    clamped = min(tab_row[active_tab], max(0, len(view) - 1))
                    tab_row[active_tab] = skip_sep(view, clamped, 1)

            elif key == ord("n"):
                _now = datetime.now()
                _next_hour = _now.hour + 1 if _now.minute > 0 else _now.hour
                new_r = {"id": None, "title": "", "date": date.today(), "hour": min(_next_hour, 23)}
                reminders.append(new_r)
                active_tab = TAB_TODAY
                view = build_view(reminders, active_tab)
                tab_row[active_tab] = next(
                    (i for i, x in enumerate(view) if x is new_r), 0
                )
                tab_col[active_tab] = COL_TITLE
                original    = ""
                text_cursor = 0
                is_new      = True
                editing     = True

            elif key == ord("\t"):
                active_tab = (active_tab + 1) % 3
                view = build_view(reminders, active_tab)
                clamped = min(tab_row[active_tab], max(0, len(view) - 1))
                tab_row[active_tab] = skip_sep(view, clamped, 1)

            elif key == curses.KEY_UP:
                tab_row[active_tab] = skip_sep(view, row - 1, -1)

            elif key == curses.KEY_DOWN:
                tab_row[active_tab] = skip_sep(view, row + 1, 1)

            elif key == curses.KEY_LEFT:
                tab_col[active_tab] = max(0, col - 1)

            elif key == curses.KEY_RIGHT:
                tab_col[active_tab] = min(NUM_COLS - 1, col + 1)

            elif key in (curses.KEY_ENTER, 10, 13):
                if view:
                    r = view[row]
                    if col == COL_TITLE:
                        original    = r["title"]
                        text_cursor = len(r["title"])
                        editing     = True
                    elif col == COL_DATE:
                        original = r["date"]
                        if r["date"] is None or r["date"] < date.today():
                            r["date"] = date.today()
                        editing = True
                    else:
                        original = r["hour"]
                        if r["hour"] is None:
                            r["hour"] = min_hour(r)
                        editing = True


os.environ.setdefault("ESCDELAY", "25")  # ms to wait after ESC before treating as standalone key

try:
    reminders = load_reminders()
except Exception as e:
    print(f"error loading reminders: {e}", file=sys.stderr)
    sys.exit(1)

curses.wrapper(main, reminders)
