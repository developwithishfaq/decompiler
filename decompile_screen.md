# Decompile Screen

## Purpose
Decompile an APK file into its source representation (smali, resources, manifest)
using apktool, ready for inspection or modification. Split-APK apps (Play / App
Bundle installs) are automatically merged into one complete APK first, so the
decompiled project includes everything — native libraries (`lib/<abi>/*.so`),
per-density drawables and per-language strings.

## Controls

| Control | Description |
|---|---|
| **APK file** | Path to the `.apk` to decompile, or a split bundle (`.xapk` / `.apkm` / `.apks` / `.apkx`). "Browse…" opens a file picker. Picking a file auto-fills the folder name from its base name. |
| **Output base dir** | Parent directory where the decompiled folder will be created. Defaults to the app directory. |
| **Folder name** | Name of the output folder (e.g. `com.example.app`). The full output path is `<base dir>/<folder name>`. |
| **Overwrite if folder exists (-f)** | Pass `-f` to apktool so an existing output folder is overwritten without prompting. On by default. |
| **Merge split APKs first** | On by default. If the chosen file is a split bundle, or a `base.apk` sitting next to its `split_*.apk` / `config*.apk` siblings, all parts are merged into one standalone `<name>_universal.apk` (via APKEditor) **before** decompiling. This is what puts the `.so` files and split resources into the project. For an ordinary standalone APK it does nothing. |
| **Decompile** | Run `apktool d <source> -o <output>` in a background thread, streaming output to the shared Output pane. After it finishes, a one-line summary reports how many native `.so` libraries (and which ABIs) made it into the project. |

## Why merging matters
An app installed from Google Play is usually an **App Bundle** split across
several APKs: `base.apk` holds the code and most resources, while separate
*config splits* carry the native libraries (`libgame.so`, …), per-density
drawables and per-language strings. Decompiling `base.apk` alone loses all of
that, so the rebuilt app crashes with `dlopen failed: library "libXxx.so" not
found`. Merging first avoids this entirely. The standalone Hill Climb Racing
case is the textbook example: `libgame.so` lives only in
`split_config.arm64_v8a.apk`.

## Requirements
- **apktool** must be found (auto-detected or set via ⚙ Tools).
- Merging additionally needs **APKEditor.jar** (shipped in this app's `tools/`
  folder) and **Java** (auto-detected from PATH / `JAVA_HOME` / Android Studio's
  bundled JRE). If either is missing, decompiling falls back to the base APK
  only and warns that native libs / split resources will be absent.
