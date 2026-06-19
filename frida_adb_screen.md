# Frida / ADB Screen

## Purpose
Connect to the Android device over ADB, manage the on-device frida-server
lifecycle (start / stop / check), and verify the Frida client-server connection.

## Controls

| Control | Description |
|---|---|
| **ADB device (host:port)** | Device serial in `host:port` form (e.g. `127.0.0.1:62001` for Nox). Used by all ADB commands and shared with the Logs tab. |
| **Connect** | Run `adb connect <host>` then `adb devices`. |
| **frida-server on device** | Full path to the frida-server binary on the device (default `/data/local/tmp/frida-server`). |
| **Run as root via su** | Wrap device commands in `su -c` — required on many emulators where frida-server needs root. |
| **⚡ Quick start** | One-click: connect → start server → check. The daily ritual in a single button. |
| **▶ Start frida-server** | `chmod 755` the binary then launch it detached in the background. Confirms startup by checking the process list. |
| **■ Kill frida-server** | Find frida-server PIDs via `ps` and `kill -9` them. Falls back gracefully if the process is not running. |
| **✔ Check Frida** | Verify both the device process (`ps`) and the local client (`frida-ps -U`). Reports a combined verdict. |

## Status header
The global status strip at the top of the window shows live device and
frida-server state (green / red / grey). Click **↻ Refresh** to re-query.

## Requirements
adb must be found. frida-ps is optional (needed for Check and target loading).
