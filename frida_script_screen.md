# Frida Script Screen

## Purpose
Configure and run a Frida instrumentation session against a target app or
process, with optional helper preloading, argument injection, and
SharedPreferences spoofing — all in one combined script session.

## Controls

| Control | Description |
|---|---|
| **From library** | Pick a script from the Scripts library. Selecting one auto-fills the Script path. |
| **Script (.js)** | Direct path to a `.js` file (overrides library selection). |
| **Mode** | `Spawn (-f)` — launch the app fresh; `Attach by name (-n)` — attach to a running process by name; `Attach by PID (-p)` — attach by PID. |
| **Target** | Package name, process name, or PID. "Load" populates a dropdown from the device (packages for spawn, processes for attach). |
| **Script arg (ARG)** | Value injected as `var ARG = "…";` at the top of the script. Used by `class-tracer.js`, `list-classes.js`, etc. |
| **Auto-load helpers** | Preload every `*bypass*.js` and `current-screen.js` from the library before the main script. |
| **Spoof prefs** | Inject typed SharedPreferences fake values via `pref-spoof.js`. |
| **Edit rules…** | Open the rule builder to add/edit/remove key/type/value spoof entries. |
| **▶ Run script** | Build the full `frida -U … -l … -q -t inf` command and start the session. |
| **■ Stop** | Terminate the running frida process. |

## Pref spoof rule builder
A popup table where each row is one pref override: key name, type (`string /
boolean / int / long / float`), and the fake value to return. Enabling spoofing
and saving rules auto-enables the "Spoof prefs" checkbox.

## Frida command structure
```
frida -U [-f|-n|-p] <target> [-l helper…] [-l pref-spoof.js] [-l main.js] -q -t inf
```
Helpers are loaded first, then the pref spoof (if active), then the main script.
Argument injection prepends `var ARG = "…";` to a temp copy of the script.

## Requirements
frida CLI must be found. frida-ps is optional (needed for "Load" in attach modes).
The Frida/ADB tab must have an ADB host set and frida-server running on the device.
