# Logs Screen

## Purpose
Capture and inspect a live `adb logcat` stream from the connected Android
device. The screen keeps logcat output isolated in its own scrollable text
area so it never mingles with output from other tabs (decompile, Frida, etc.).

## Controls

| Control | Description |
|---|---|
| **Tag filter** | Restrict output to a single logcat tag (e.g. `MyApp`). Leave empty to see all tags. |
| **Priority** | Minimum log level to display: `*:V` (all), `*:D`, `*:I`, `*:W`, `*:E`. |
| **▶ Attach** | Start `adb logcat` with the current filter. Streams output until detached or the device disconnects. |
| **■ Detach** | Terminate the running logcat process. |
| **Clear** | Wipe the log area (drains the pending queue too). |
| **Save…** | Write the current log area content to a `.txt`/`.log` file. |

## Device selection
The ADB device (host:port) is shared with the **Frida / ADB** tab via the
same `frida_host` field. Set the device there first; the Logs tab picks it up
automatically.

## Filter syntax
When a **Tag filter** is provided the command becomes:

```
adb logcat <tag>:<level> *:S
```

`*:S` silences all other tags so only the chosen tag is shown.

When the tag field is empty the command becomes:

```
adb logcat *:<level>
```

which shows all tags at or above the chosen priority.

## Log colour coding
Lines are colour-coded by the log level letter found in each logcat line:

| Level | Colour |
|---|---|
| V (Verbose) | Grey |
| D (Debug) | Blue |
| I (Info) | Green |
| W (Warning) | Yellow |
| E (Error) | Red |
| F (Fatal) | Bright red |

## Persistence
The **Tag filter** and **Priority** values are saved between sessions in
`apk_tool_gui.config.json`.
