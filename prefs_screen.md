# Prefs Screen

## Purpose
Inspect, edit, and delete SharedPreferences XML files directly on the connected
device over ADB — no Frida required. Useful for resetting state, injecting
test values, or understanding what an app persists.

## Controls

| Control | Description |
|---|---|
| **Package** | App package name (e.g. `com.example.app`). "Use Frida target" copies the current Frida Script target in one click. Picking a package from the list below also fills this field. |
| **Use root via su** | Wrap shell commands in `su -c` — required for most apps whose data is owned by their own UID. |
| **Filter** | Live search over the package list. Narrows the list as you type. |
| **3rd-party only** | When checked, `pm list packages -3` is used so system apps are excluded. |
| **↻ Refresh list** | Query the device and populate the package list. |
| **Package list** | Installed packages on the device. Single-click fills the **Package** field; double-click fills it *and* lists that package's pref files. |
| **Pref file list** | Lists all `.xml` files in `/data/data/<pkg>/shared_prefs/`. Double-click to open the editor. |
| **List files** | Run `adb shell ls /data/data/<pkg>/shared_prefs/` and populate the list. |
| **View / Edit** | Pull the selected XML file and open it in an inline editor. "Save to device" pushes the edited content back. |
| **Delete** | Remove the selected XML file from the device (confirms first). |

## Picking a package
Instead of typing the package name, click **↻ Refresh list** to query the device
(`adb shell pm list packages`, with `-3` when **3rd-party only** is on), filter
to find the app, and click it to fill **Package**. Double-clicking also runs
**List files** for that package in one step. This mirrors the package picker on
the [Pull APK](pull_apk_screen.md) screen.

## Edit & save flow
1. "View / Edit" pulls the file via `adb shell cat`.
2. The XML is opened in a dark `Consolas` editor.
3. "Save to device" pushes the content via `adb push` to `/data/local/tmp/` then
   `cat > <target>` to preserve the file's owner and permissions.
4. Restart the app after saving — Android caches prefs in memory and won't see
   changes until the next cold start.

## Persistence
The **3rd-party only** toggle (and the last-used **Package**) are saved between
sessions in `apk_tool_gui.config.json`.

## Requirements
adb must be found. Root (`su`) is almost always needed for production apps.
The device must be connected (set host in the Frida/ADB tab).
