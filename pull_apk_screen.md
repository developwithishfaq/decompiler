# Pull APK Screen

## Purpose
List the packages installed on the connected Android device and pull their APK
files — including any architecture-specific splits — to a local folder, ready
for decompilation or inspection.

## Controls

| Control | Description |
|---|---|
| **Filter** | Live search over the package list. Narrows the list as you type. |
| **3rd-party only** | When checked, `pm list packages -3` is used so system apps are excluded. |
| **↻ Refresh list** | Query the device and populate the package list. |
| **Package list** | All matching packages. Ctrl-click / Shift-click for multi-select. |
| **Save to** | Local folder where pulled APKs are written. Each package lands in its own `<save-to>/<package>/` subfolder. |
| **Browse…** | Pick the save-to folder via a directory dialog. |
| **Pull APK(s)** | Pull all APK paths for every selected package. Handles monolithic and split APKs. With **Merge splits** on, a split app is also combined into one standalone `<package>_universal.apk`. |
| **Pull + Decompile** | Pull every APK for the selected package, merge the splits (if any), then immediately decompile the result with apktool into `<save-to>/<package>_decompiled/`. Single-package only. |
| **Merge splits into one APK** | On by default. When the pulled app is a split set, all parts are merged into `<save-to>/<package>_universal.apk` (via APKEditor) so native libs and split resources are kept together. That universal APK is what gets decompiled. |

## How it works

1. `adb shell pm path <package>` returns **all** APK paths for the package.
   A split APK produces multiple lines, e.g.:
   ```
   package:/data/app/.../base.apk
   package:/data/app/.../split_config.arm64_v8a.apk
   package:/data/app/.../split_config.en.apk
   package:/data/app/.../split_config.xhdpi.apk
   ```
2. Every path is pulled with `adb pull` into `<save-to>/<package>/`, keeping
   the original filenames (`base.apk`, `split_config.*.apk`, …).
3. If splits are present and **Merge splits** is on, they are merged into one
   standalone `<save-to>/<package>_universal.apk` with APKEditor.
4. For **Pull + Decompile**, the merged universal APK (or `base.apk` if there
   were no splits) is decompiled, and a one-line summary reports how many native
   `.so` libraries (and which ABIs) ended up in the project.

## Split APKs and the `libXxx.so not found` crash

Apps installed from Google Play (and on emulators like Nox) are usually
**split APKs**. The native `.so` libraries live in the architecture-specific
split, not in `base.apk`:

| Split file | Contains |
|---|---|
| `base.apk` | Java/Kotlin code, most resources, manifest |
| `split_config.arm64_v8a.apk` | `lib/arm64-v8a/*.so` (e.g. `libgame.so`) |
| `split_config.x86_64.apk` | `lib/x86_64/*.so` — for x86 emulator images |
| `split_config.<lang>.apk` | per-language strings (e.g. `.en`) |
| `split_config.<density>.apk` | per-density drawables (e.g. `.xhdpi`) |

**Symptom (the old, manual workflow):** pulling and recompiling `base.apk`
alone produced an app that crashed with
`UnsatisfiedLinkError: dlopen failed: library "libgame.so" not found`.

**Now it's automatic:** with **Merge splits** on (the default), the splits are
combined into one complete APK before decompiling, so the `.so` files and split
resources are already present — no manual copying needed. If you ever need to do
it by hand (e.g. APKEditor unavailable), open the matching split (it's a zip)
and copy `lib/<abi>/*.so` into the decompiled folder's `lib/<abi>/` directory,
then recompile.

## Persistence
**Save to** directory and the **3rd-party only** toggle are saved between
sessions in `apk_tool_gui.config.json`.

## Requirements
- **adb** must be found (auto-detected or set via ⚙ Tools), and the device must
  be connected (set host in the Frida/ADB tab).
- **apktool** is required for "Pull + Decompile".
- **Merging** needs **APKEditor.jar** (in this app's `tools/` folder) and
  **Java**. If either is missing, merging is skipped and a warning explains that
  `base.apk` alone will be missing its native libs / split resources.
