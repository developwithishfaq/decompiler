# Scripts Screen

## Purpose
Manage a local library of reusable Frida JavaScript scripts. Scripts live in
the `scripts/` folder next to the app and are available for selection in the
Frida Script tab.

## Controls

| Control | Description |
|---|---|
| **Script list** | All `.js` files in the library. Double-click to open the editor. |
| **New…** | Open a blank editor pre-filled with a minimal Frida script template. |
| **Edit** | Open the selected script in the inline editor. |
| **Import…** | Copy one or more `.js` files from elsewhere into the library. |
| **Delete** | Remove the selected script from disk (confirms before deleting). |
| **Seed starters** | Write any missing built-in starter scripts into the library. |
| **Open folder** | Open the `scripts/` folder in Windows Explorer. |
| **Refresh** | Rescan `scripts/` and update the list and the Frida Script combobox. |

## Built-in starter scripts
Seeded on first run (or via "Seed starters"):

| Script | Purpose |
|---|---|
| `ssl-pinning-bypass.js` | Bypass default TrustManager and OkHttp3 CertificatePinner |
| `root-detection-bypass.js` | Defeat common File.exists / Runtime.exec / RootBeer checks |
| `ram-check-bypass.js` | Report 8 GB RAM to pass memory-gate checks |
| `gpay-billing-spoof.js` | Client-side Google Play billing spoof (authorized testing only) |
| `pref-spoof.js` | Return fake SharedPreferences values (injected by the Frida Script tab) |
| `class-tracer.js` | Trace every method of a given class |
| `list-classes.js` | Enumerate loaded classes matching a filter |
| `current-screen.js` | Report Activity / Fragment / Compose navigation changes live |

## Inline editor
A dark-themed `Consolas` text editor with undo support. Saves directly to the
library folder on "Save". The file-name field auto-appends `.js` if omitted.
