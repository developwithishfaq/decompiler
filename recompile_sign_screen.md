# Recompile + Sign Screen

## Purpose
Rebuild a decompiled code folder back into a release-ready APK via a
four-step pipeline: build → align → sign → verify.

## Controls

| Control | Description |
|---|---|
| **Code folder** | The decompiled folder produced by apktool (contains `AndroidManifest.xml`, `smali/`, `res/`, etc.). |
| **Output APK** | Destination path for the final signed APK. Auto-filled from the folder name on browse. |
| **Keystore** | `.keystore` or `.jks` file used for signing. Auto-detected from files next to the app matching `*.keystore` / `*.jks`. |
| **Keystore password** | Password for the keystore (masked). |
| **Key alias** | Alias of the signing key within the keystore. Leave empty to use the keystore default. |
| **Key password** | Password for the individual key entry (masked). |
| **Build → Align → Sign → Verify** | Run the full pipeline in a background thread. Each step is labelled in the shared Output pane. |

## Pipeline steps
1. `apktool b <folder> -o <unsigned.apk>`
2. `zipalign -f -p 4 <unsigned.apk> <aligned.apk>`
3. `apksigner sign --ks <keystore> … --out <output.apk> <aligned.apk>`
4. `apksigner verify --verbose <output.apk>`

## Requirements
apktool, zipalign, and apksigner must all be found (auto-detected or set via ⚙ Tools).
