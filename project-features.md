# APK Tool GUI — Project Features

A single-window Tkinter app (`apk_tool_gui.py`) for Android app analysis and
manipulation. Tools are discovered automatically (PATH → Android SDK →
emulator install folders) with one-time manual override via the ⚙ Tools dialog.
Settings survive between sessions via `apk_tool_gui.config.json`.

Split-APK apps (Google Play / App Bundle installs) are handled transparently:
their parts are merged into one complete standalone APK (via bundled
`tools/APKEditor.jar` + Java) before decompiling, so native libraries
(`lib/<abi>/*.so`) and split resources are never lost. This is on by default in
both the Decompile and Pull APK screens.

---

## Screens (notebook tabs)

### Decompile
Pick an APK (or a `.xapk`/`.apkm`/`.apks` split bundle), choose an output folder
name, and decompile it with apktool. Split apps are auto-merged into one
complete APK first (so `.so` libs and split resources are included), and an
"overwrite" flag lets you re-decompile in place without deleting the old folder.
→ See [`decompile_screen.md`](decompile_screen.md)

### Recompile + Sign
Rebuild a decompiled code folder back into a signed, aligned APK using
apktool → zipalign → apksigner in a single click.
→ See [`recompile_sign_screen.md`](recompile_sign_screen.md)

### Frida / ADB
Connect to an ADB device, start / stop the on-device frida-server, and
verify the Frida connection. A "Quick start" button runs the entire daily
ritual (connect → start → check) in one click.
→ See [`frida_adb_screen.md`](frida_adb_screen.md)

### Scripts
Manage a local library of reusable Frida `.js` scripts: create, edit,
import, delete, and open the library folder. Ships with built-in starter
scripts (SSL bypass, root bypass, RAM spoof, etc.).
→ See [`scripts_screen.md`](scripts_screen.md)

### Frida Script
Select a script from the library (or browse for one), choose spawn / attach
mode, pick a target package or process, optionally inject a script argument,
preload bypass helpers, and spoof SharedPreferences values — all in one
instrumentation session.
→ See [`frida_script_screen.md`](frida_script_screen.md)

### Prefs
Inspect and edit SharedPreferences XML files on the connected device over
ADB (no Frida required). Supports root-via-su for protected app data.
→ See [`prefs_screen.md`](prefs_screen.md)

### Pull APK
List installed packages on the connected device, select one or more, and pull
all APK files (including architecture-specific splits) to a local folder. Splits
are merged into one standalone `<package>_universal.apk`, and a "Pull + Decompile"
shortcut chains pull → merge → apktool in one click — fixing the
`libXxx.so not found` crash that comes from decompiling `base.apk` alone.
→ See [`pull_apk_screen.md`](pull_apk_screen.md)

### Logs
Attach to and detach from a live `adb logcat` stream. Supports tag and
priority filtering, colour-coded log levels, and saving the captured output
to a file. Uses its own dedicated log area so the stream does not pollute the
shared Output pane.
→ See [`logs_screen.md`](logs_screen.md)
