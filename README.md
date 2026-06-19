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

**Generic, zero-config tool discovery**
- Finds every external tool (`apktool`, `zipalign`, `apksigner`, `adb`,
  `frida`/`frida-ps`) automatically — no machine-specific paths baked in
- Looks in: saved overrides → system PATH → Android SDK (`build-tools`,
  `platform-tools`, picked up from `ANDROID_HOME`/`ANDROID_SDK_ROOT` or the
  standard Studio install on Windows/macOS/Linux) → common emulator installs
  (Nox / BlueStacks / LDPlayer / MEmu / Genymotion)
- If something can't be found, the app asks you to point it at the binary and
  remembers your choice (`apk_tool_gui.tools.json`, git-ignored)
- A **⚙ Tools** button (top-right) lets you view, edit, browse or re-detect
  every tool path at any time

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

## Configuration

All tunable defaults live in one hand-editable, committed file: **`settings.ini`**
(next to `apk_tool_gui.py`). It's a plain INI file — edit it to change behaviour,
no code changes needed. **Windows paths can be pasted exactly as-is** (no
escaping, no slash-flipping). Any missing or invalid key silently falls back to
a built-in default, so you can keep only what you change (or delete the file to
regenerate it).

| Section / key | What it controls |
|---|---|
| `[ui]` `title` / `geometry` / `min_width` / `min_height` | Window title and sizing |
| `[adb]` `default_host` | Prefilled ADB device (host:port) |
| `[adb]` `frida_remote` | Default on-device `frida-server` path |
| `[adb]` `prefer` | Which adb to auto-pick first: `emulator` (Nox/BlueStacks/… — default, matches the emulator and avoids version conflicts) or `sdk` |
| `[keystore]` `auto_detect_globs` | Patterns used to auto-fill the keystore field |
| `[tools.path]` `<tool>` | **Exact full path** to a tool's binary — pin it explicitly (wins over everything); leave blank to auto-detect |
| `[tools.names]` `<tool>` | Executable filenames searched for each tool when its path is blank |
| `[search_paths]` `android_sdk_roots` | Extra Android SDK roots to scan |
| `[search_paths]` `build_tools_dirs` | Extra dirs holding `zipalign` / `apksigner` |
| `[search_paths]` `platform_tools_dirs` | Extra dirs holding `adb` |
| `[search_paths]` `emulator_dirs` | Extra emulator bin folders |
| `[search_paths]` `extra_tool_dirs` | Generic extra dirs searched for every tool |

**Example — pin adb to your emulator's binary.** Just paste the path:

```ini
[tools.path]
adb = D:\Program Files\Nox\bin\adb.exe
```

Lists (`names`, `search_paths`, `auto_detect_globs`) can be comma-separated or
one item per line.

The app also keeps two **machine-specific, git-ignored** files it manages for
you (no need to edit by hand):

- `apk_tool_gui.config.json` — remembers your field values between launches
- `apk_tool_gui.tools.json` — remembers tool paths you picked in **⚙ Tools**

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
- External tools (`apktool`, `zipalign`, `apksigner`, `adb`) — installed
  anywhere the app can discover them (PATH, Android SDK, or an emulator's bin
  folder). If they're somewhere unusual, just point the **⚙ Tools** dialog at
  them once.

## Run

```bash
python apk_tool_gui.py
```

or double-click **`APK Tool GUI.bat`** on Windows (launches with `pythonw`, no console).

## Notes

- The ADB device field defaults to `127.0.0.1:62001` (a common emulator port)
  but works with any adb-reachable device/emulator — just edit the field.
- `frida` on the PC must match the `frida-server` version on the device.
