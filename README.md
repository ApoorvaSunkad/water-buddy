# Water Buddy

A desktop hydration reminder for Windows. On a schedule you choose, a character
walks in from the bottom-right corner of your screen — right where the taskbar
clock is — reminds you to drink water, waits a few seconds, and walks back out.

Built with Python and PySide6 (Qt). No server, no account, no network access.
Everything runs locally.

---

## Quick start

```powershell
# from the project folder
.venv\Scripts\python.exe run.py
```

The app has no main window. It lives in your system tray as a water droplet
that slowly empties as your next reminder approaches. Left-click it for
settings, right-click for the menu.

To see the character immediately without waiting an hour: tray menu →
**Remind me now**, or **Preview** in the settings window.

### Setting it up from scratch

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
```

### Running the tests

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

---

## Why there's no backend

This is the question the project started with, so it's worth writing down.

A backend is a computer elsewhere that does work your machine can't or
shouldn't. You need one for shared data between users, data that must outlive
your device, secrets that must stay hidden, computation too heavy for a laptop,
or a single source of truth. A hydration reminder needs none of those.

Adding one would mean the app stops working whenever the server isn't running —
trading a guaranteed-working app for one with a new way to fail, in exchange for
no features. So the "database" here is `stats.json`, about 6 KB for a year of
history, and the "API" is a function call.

Dropping the backend removed *server* code, not *code*. What's left is the
genuinely hard part: transparent windows, sleep-aware scheduling, DPI scaling,
and animation.

---

## How it fits together

```
run.py                    entry point
water_buddy/
  app.py                  wiring — the only module that knows about all the others
  config.py               paths and constants
  settings.py             user preferences + JSON persistence
  stats.py                daily glass counts and streaks
  scheduler.py            when to remind (the interesting logic)
  overlay.py              the character window and walk animation
  characters.py           image loading, with generated placeholders
  settings_window.py      the settings UI and countdown dial
  tray.py                 system tray icon and menu
  platform_win.py         Windows-specific calls, isolated
assets/characters/        your artwork goes here
tests/                    47 tests, no UI required
```

The design rule: **every module is ignorant of the others, and one module wires
them together.** `scheduler.py` doesn't know what a character is. `overlay.py`
doesn't know what a glass of water is. `stats.py` doesn't know a UI exists.
Only `app.py` knows about all of them, and all it does is connect signals to
slots.

That's why you can change *when* reminders fire without touching *how they
look*, and it's the habit most worth taking from this project.

---

## The parts that were harder than they looked

### The scheduler, and why it isn't a one-line timer

The obvious implementation is `QTimer.singleShot(3600_000, remind)`. It's wrong
three separate ways:

**1. Laptop sleep.** A timer doesn't advance while the machine is suspended.
Close the lid at 2pm, open it at 5pm, and it still thinks 55 minutes remain. So
the scheduler ticks once a second and compares against the *wall clock*. Time
passing is measured, never assumed.

**2. The burst problem.** Once you compare against the wall clock you get the
opposite bug — wake from a 3-hour sleep and you're three intervals overdue.
Fire naively and three characters stack on top of each other. Missed reminders
are never queued: overdue by any amount produces exactly one reminder.

**3. Waking someone who just sat down.** Even one reminder the instant the lid
opens is bad. A gap larger than 120 seconds between ticks is treated as "the
machine was away" and restarts the countdown silently instead of firing.

The scheduler knows nothing about quiet hours, fullscreen apps, or characters.
It asks a caller-supplied `should_suppress` callback whether now is a bad
moment. That's what makes it testable with a fake clock — a three-hour sleep
becomes one line and runs in microseconds.

### The overlay

Three things make it feel like a character rather than a notification:

- **The window is invisible.** `WA_TranslucentBackground` plus a frameless hint
  means Qt composites only the pixels actually painted — genuinely
  see-through, including antialiased edges and soft shadows.

- **Walking is faked, and that's fine.** A static PNG slid sideways reads as
  sliding. The same PNG slid sideways *while bobbing vertically and rocking a
  couple of degrees* reads as walking, because those are the two motions your
  eye uses to detect a gait. Two sine waves buy most of what a hand-drawn walk
  cycle would. The rotation pivots on the feet, not the image centre —
  otherwise the character swings like a compass needle.

- **It never steals focus.** `Qt.Tool` keeps it out of alt-tab and the taskbar;
  `WA_ShowWithoutActivating` means showing it doesn't move keyboard focus.
  Without those, an hourly reminder would eat an hourly keystroke.

### DPI scaling

This display runs at 150% scaling — one logical pixel is 1.5 physical pixels.
Scaling artwork to 260 logical pixels and stopping there lets Windows stretch it
to 390 and it looks soft. Instead the bitmap is scaled to full physical size and
Qt is told the ratio, so it draws crisply.

### Failing loud instead of silent

Windows' `SHQueryUserNotificationState` reports whether a fullscreen app is
running, so the buddy doesn't interrupt a video call or a presentation. But some
Remote Desktop and VM sessions report "busy" *permanently* — which would mean
the app never reminds you and never says why.

So that check is advisory: it wins three intervals in a row, then gets
overruled with a warning in the log. Deliberate suppression (quiet hours, goal
met) is unlimited; a guess by the OS is not. **A reminder app that never
reminds is broken in the worst way — the way that looks like it's working.**

---

## Bugs the tests caught

Written down because all four were found by testing, not by reading:

1. **Changing the interval silently did nothing.** The settings window mutates
   the settings object *in place* and then emits it, so by the time the app
   compared "old" against "new", both were the new value. Fixed by tracking the
   interval actually applied to the scheduler.

2. **Snooze made the reminder come back sooner.** The backwards-clock guard saw
   a snooze (300s out, on a 60s interval) as an impossible wait and cancelled
   it — then fired four times. Fixed by tracking the maximum *legitimate* wait
   separately from the interval.

3. **Pause fired twice.** App updates the button → button emits `toggled` →
   straight back into the handler. Fixed with a re-entrancy guard.

4. **The settings window was taller than the screen.** 679px of controls on a
   672px work area, with the bottom ones unreachable. Fixed with a scroll area
   sized to the actual work area.

---

## Adding your own artwork

The app ships with no character images and draws crude placeholders instead, so
it runs before any art exists. Drop a transparent PNG at
`assets/characters/female/drinking.png` (and `male/`) and it's picked up
automatically — no code changes.

See [`assets/characters/README.md`](assets/characters/README.md) for size,
transparency, and orientation requirements, plus how to upgrade to a real
multi-frame walk cycle later.

---

## Building a .exe

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller build\water-buddy.spec
```

Output lands in `dist\WaterBuddy\`. The spec file uses `--windowed` so no
console window appears, and bundles `assets/` so the artwork travels with the
executable.

---

## Settings and data locations

| What | Where |
|---|---|
| Preferences | `%APPDATA%\WaterBuddy\settings.json` |
| History | `%APPDATA%\WaterBuddy\stats.json` |
| Log | `%APPDATA%\WaterBuddy\water-buddy.log` |

All plain text — safe to read, edit, or delete. Deleting them resets the app to
defaults; it won't crash on a missing, corrupt, or hand-mangled file (there are
tests for exactly that).

---

## Known limitations

- **Windows only.** The Qt code is cross-platform but `platform_win.py` isn't.
  A macOS or Linux port means writing a sibling module, not hunting scattered
  `ctypes` calls.
- **Fullscreen exclusive apps can cover the overlay.** Games and some
  fullscreen Remote Desktop sessions take exclusive display control. The app
  re-asserts topmost each time it appears, which handles most cases.
- **Reminders inside a remote session need the app installed there.** Nothing
  running locally can paint pixels inside another machine's Windows session.
