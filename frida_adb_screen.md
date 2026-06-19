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

## Device info line
Just below the status dots in the header, a one-line summary of the connected
device is shown (read via `adb shell getprop`). It refreshes whenever the state
is re-queried — on **Connect**, **↻ Refresh**, or after a frida-server
start/kill:

```
samsung SM-G988N   ·   Android 12 (API 32)   ·   x86_64,arm64-v8a,x86,armeabi-v7a,armeabi
```

- **Name** — manufacturer + model (`ro.product.manufacturer` + `ro.product.model`)
- **Android version** — release + API level (`ro.build.version.release` /
  `ro.build.version.sdk`)
- **Architecture** — the ABIs the device supports (`ro.product.cpu.abilist`),
  which tells you which `split_config.<abi>.apk` / `lib/<abi>/*.so` you need.

When no device is connected the line reads **No device connected**.

## Requirements
adb must be found. frida-ps is optional (needed for Check and target loading).
