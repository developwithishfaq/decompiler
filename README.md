# Decompiler — APK Toolkit GUI

A single-window Tkinter app that bundles the everyday Android app-analysis workflow:
decompile/recompile/sign APKs, manage a Frida server, run Frida scripts from a
reusable library, inspect SharedPreferences, and spoof typed pref values — all
without re-typing setup every day.

> **Authorized use only.** This is a security-testing tool intended for analyzing
> apps you own or are explicitly authorized to test. The bundled Frida scripts
> (root/SSL/RAM bypasses, billing spoof-resistance check, pref spoofing) are for
> verifying *your own* apps' hardening. Don't use it against apps or services you
> don't have permission to test.

## Features

**Decompile / Recompile + Sign**
- Decompile an APK with `apktool` into a named folder
- Rebuild → `zipalign` → `apksigner` sign → verify, in one click
- Auto-detects `apktool`, `zipalign`, `apksigner` from PATH / Android build-tools

**Frida / ADB**
- ⚡ Quick start: connect → start `frida-server` → check, in one button
- Robust `frida-server` start/kill/status (detects truncated process names)
- Live status header (device + frida-server up/down)
- adb auto-detected from PATH, falling back to a Nox `bin` folder

**Scripts library + Frida Script runner**
- Manage a `.js` library (new/edit/import/delete) in a popup editor
- Run a script over USB (`-q -t inf`, stays attached, streams `console.log`)
- Auto-preload helper scripts (`*bypass*.js` + `current-screen.js`) before your script
- Inject a `Script arg` (JS global `ARG`) into a script at run time
- **Pref spoof**: build typed rules (string/boolean/int/long/float) in a table;
  they're injected into `pref-spoof.js` and hook `SharedPreferences` getters live

**Prefs (static, no Frida)**
- List / view / edit / delete `/data/data/<pkg>/shared_prefs/*.xml` over adb
- Saves back preserving the file's owner/permissions

**Quality-of-life**
- Every field is remembered between launches (`apk_tool_gui.config.json`, git-ignored)
- No flashing `cmd` windows; all subprocess output streams into the app

## Seeded scripts

On first run the app writes starter scripts into `scripts/` (git-ignored, regenerated):

| Script | Purpose |
|---|---|
| `root-detection-bypass.js` | Common root checks → not rooted |
| `ssl-pinning-bypass.js` | TrustManager / OkHttp3 pinning bypass |
| `ram-check-bypass.js` | Report high total RAM for "needs N GB" gates |
| `current-screen.js` | Log the current Activity / Fragment / Compose route / game host |
| `pref-spoof.js` | Return fake SharedPreferences values (driven by the GUI rule builder) |
| `gpay-billing-spoof.js` | **Spoof-resistance test**: fake a client-side purchase to confirm your app rejects it (server-side verification) |
| `class-tracer.js` | Trace every method of a class (`ARG` = class) |
| `list-classes.js` | List loaded classes matching a filter (`ARG` = filter) |

## Requirements

- **Python 3** (uses stdlib `tkinter`)
- `pip install -r requirements.txt` (installs `frida-tools`; pin `frida` to your
  device's `frida-server` version)
- External tools on PATH (or Android SDK build-tools): `apktool`, `zipalign`,
  `apksigner`, `adb`

## Run

```bash
python apk_tool_gui.py
```

or double-click **`APK Tool GUI.bat`** on Windows (launches with `pythonw`, no console).

## Notes

- Target environment during development was the Nox emulator (x86_64, Android 12),
  adb at `127.0.0.1:62001`, but it works with any adb-reachable device/emulator.
- `frida` on the PC must match the `frida-server` version on the device.
