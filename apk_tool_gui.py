#!/usr/bin/env python3
"""
APK Tool GUI
------------
A simple Tkinter GUI for:
  * Decompiling an APK with apktool (into a folder you name)
  * Recompiling a code folder back into an APK, then aligning,
    signing and verifying it.

External tools (apktool, zipalign, apksigner, adb, frida) are discovered
generically — saved overrides first, then PATH, then a standard Android SDK
install, then common emulator install folders. Anything that can't be found is
requested from the user via the "Tools" dialog and remembered between runs, so
there are no machine-specific paths baked into the source.
"""

import os
import sys
import glob
import json
import time
import shutil
import tempfile
import threading
import subprocess
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Remembers your field values between launches (so daily setup is zero-click)
CONFIG_PATH = os.path.join(APP_DIR, "apk_tool_gui.config.json")

# Remembers tool paths the user picked manually (so detection is one-time)
TOOL_PATHS_PATH = os.path.join(APP_DIR, "apk_tool_gui.tools.json")

# Folder where reusable Frida scripts live (managed by the Scripts tab)
SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")

# Stops child console apps (adb, apktool, frida-ps, ...) from flashing a cmd window.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ---------------------------------------------------------------------------
# Editable project settings (settings.json)
#
# Everything a user might reasonably want to tune — window size, the default
# adb host, the executable names we look for, and any extra folders to search
# for tools — lives in settings.json next to this script. It's plain JSON,
# committed to the repo, and safe to hand-edit. Missing/invalid keys silently
# fall back to the built-in defaults below, so you can delete the file or keep
# only the keys you care about.
# ---------------------------------------------------------------------------

SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "ui": {
        "title": "APK Tool GUI",
        "geometry": "820x480",
        "min_width": 700,
        "min_height": 380,
    },
    "adb": {
        # A common emulator adb port (Nox); editable in the Frida/ADB tab too.
        "default_host": "127.0.0.1:62001",
        "frida_remote": "/data/local/tmp/frida-server",
    },
    "keystore": {
        # If exactly these patterns match a file next to the app, prefill it.
        "auto_detect_globs": ["*.keystore", "*.jks"],
    },
    # The executable names to look for on PATH / in the search dirs, per tool.
    "tools": {
        "apktool":   {"names": ["apktool.bat", "apktool", "apktool.jar"]},
        "zipalign":  {"names": ["zipalign.exe", "zipalign"]},
        "apksigner": {"names": ["apksigner.bat", "apksigner", "apksigner.jar"]},
        "adb":       {"names": ["adb.exe", "nox_adb.exe", "adb"]},
        "frida_ps":  {"names": ["frida-ps.exe", "frida-ps"]},
        "frida":     {"names": ["frida.exe", "frida"]},
    },
    # Extra folders to search, on top of the ones auto-detected from the
    # Android SDK / common emulator installs. Add absolute paths here if your
    # tools live somewhere unusual.
    "search_paths": {
        "android_sdk_roots": [],     # extra SDK roots (contain build-tools/, platform-tools/)
        "build_tools_dirs": [],      # extra dirs holding zipalign / apksigner
        "platform_tools_dirs": [],   # extra dirs holding adb
        "emulator_dirs": [],         # extra emulator bin folders
        "extra_tool_dirs": [],       # generic dirs searched for every tool
    },
}


def _deep_merge(base, override):
    """Recursively overlay `override` onto a copy of `base`."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_settings():
    """Built-in defaults overlaid with settings.json (creating it if absent)."""
    user = {}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            user = loaded
    except FileNotFoundError:
        # Seed a starter file so users have something concrete to edit.
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(DEFAULT_SETTINGS, fh, indent=2)
        except Exception:
            pass
    except Exception:
        pass  # malformed file -> just use defaults
    return _deep_merge(DEFAULT_SETTINGS, user)


SETTINGS = _load_settings()

DEFAULT_ADB_HOST = SETTINGS["adb"]["default_host"]
DEFAULT_FRIDA_REMOTE = SETTINGS["adb"]["frida_remote"]

# ---------------------------------------------------------------------------
# Generic tool discovery
#
# Nothing here is hard-coded to a particular machine. We look in, roughly:
#   1. a path the user picked before (saved overrides)
#   2. the system PATH
#   3. a standard Android SDK install (build-tools / platform-tools)
#   4. common Android-emulator install folders (Nox / BlueStacks / LDPlayer / …)
# and only if all of that fails do we ask the user to point us at the binary.
# ---------------------------------------------------------------------------

def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def _dedup_existing_dirs(candidates):
    seen, out = set(), []
    for d in candidates:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _android_sdk_roots():
    """Candidate Android SDK root dirs across OSes and env vars."""
    roots = []
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_SDK"):
        v = os.environ.get(env)
        if v:
            roots.append(v)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Android", "Sdk"))
    roots += [
        _home("AppData", "Local", "Android", "Sdk"),  # Windows (Android Studio default)
        _home("Library", "Android", "sdk"),           # macOS
        _home("Android", "Sdk"),                       # Linux
    ]
    roots += SETTINGS["search_paths"]["android_sdk_roots"]  # user-configured extras
    return _dedup_existing_dirs(roots)


def _build_tools_dirs():
    """Android SDK build-tools version dirs, newest first."""
    dirs = []
    for root in _android_sdk_roots():
        bt = os.path.join(root, "build-tools")
        if os.path.isdir(bt):
            for name in sorted(os.listdir(bt), reverse=True):
                full = os.path.join(bt, name)
                if os.path.isdir(full):
                    dirs.append(full)
    dirs += SETTINGS["search_paths"]["build_tools_dirs"]
    dirs += SETTINGS["search_paths"]["extra_tool_dirs"]
    return _dedup_existing_dirs(dirs)


def _platform_tools_dirs():
    """Android SDK platform-tools dirs (where adb lives)."""
    dirs = [os.path.join(root, "platform-tools") for root in _android_sdk_roots()]
    dirs += SETTINGS["search_paths"]["platform_tools_dirs"]
    dirs += SETTINGS["search_paths"]["extra_tool_dirs"]
    return _dedup_existing_dirs(dirs)


def _emulator_dirs():
    """Common Android-emulator install folders that ship their own adb."""
    bases = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        v = os.environ.get(env)
        if v:
            bases.append(v)
    # macOS / Linux installs (Genymotion, etc.)
    bases += ["/Applications", _home("Applications")]

    # Per-emulator sub-paths (relative to each base) where an adb binary sits.
    subs = [
        ("Nox", "bin"),
        ("Nox", "Nox", "bin"),
        ("BlueStacks_nxt",),
        ("BlueStacks",),
        ("LDPlayer", "LDPlayer9"),
        ("LDPlayer", "LDPlayer4.0"),
        ("LDPlayer9",),
        ("LDPlayer",),
        ("Microvirt", "MEmu"),
        ("MEmu",),
        ("Genymobile", "Genymotion", "tools"),
        ("Genymotion.app", "Contents", "MacOS", "tools"),
    ]
    out = []
    for base in bases:
        for parts in subs:
            out.append(os.path.join(base, *parts))
    out += SETTINGS["search_paths"]["emulator_dirs"]       # user-configured extras
    out += SETTINGS["search_paths"]["extra_tool_dirs"]
    return _dedup_existing_dirs(out)


def _which(names):
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _find_in_dirs(dirs, names):
    for d in dirs:
        for name in names:
            cand = os.path.join(d, name)
            if os.path.isfile(cand):
                return cand
    return None


# -- per-tool finders --------------------------------------------------------
# Each looks for the executable names from settings.json on PATH first, then
# in the relevant search dirs (which already include any user-configured extras).

def _tool_names(key):
    return SETTINGS["tools"].get(key, {}).get("names", [])


def _find_apktool():
    names = _tool_names("apktool")
    return _which(names) or _find_in_dirs([APP_DIR] + _build_tools_dirs(), names)


def _find_zipalign():
    names = _tool_names("zipalign")
    return _which(names) or _find_in_dirs(_build_tools_dirs(), names)


def _find_apksigner():
    names = _tool_names("apksigner")
    return _which(names) or _find_in_dirs(_build_tools_dirs(), names)


def _find_adb():
    names = _tool_names("adb")
    return (_which(names)
            or _find_in_dirs(_platform_tools_dirs(), names)
            or _find_in_dirs(_emulator_dirs(), names))


def _find_frida_ps():
    names = _tool_names("frida_ps")
    return _which(names) or _find_in_dirs(_emulator_dirs(), names)


def _find_frida():
    names = _tool_names("frida")
    return _which(names) or _find_in_dirs(_emulator_dirs(), names)


class ToolSpec:
    """Describes one external tool the app can use."""
    def __init__(self, key, label, finder, required, hint):
        self.key = key            # stable id used in config / code
        self.label = label        # human-friendly name
        self.finder = finder      # callable -> path or None
        self.required = required  # block the relevant feature if missing
        self.hint = hint          # shown in the picker when not found


TOOL_SPECS = [
    ToolSpec("apktool", "apktool", _find_apktool, True,
             "Decompiles / recompiles APKs. Get it from https://apktool.org "
             "(put apktool.bat + apktool.jar on PATH), then re-detect."),
    ToolSpec("zipalign", "zipalign", _find_zipalign, True,
             "Ships with the Android SDK build-tools. Install via Android "
             "Studio or `sdkmanager \"build-tools;<ver>\"`."),
    ToolSpec("apksigner", "apksigner", _find_apksigner, True,
             "Ships with the Android SDK build-tools (apksigner.bat)."),
    ToolSpec("adb", "adb", _find_adb, True,
             "Android SDK platform-tools, or your emulator's bin folder "
             "(Nox / BlueStacks / LDPlayer / MEmu / Genymotion)."),
    ToolSpec("frida_ps", "frida-ps", _find_frida_ps, False,
             "Optional (process listing). Install with: pip install frida-tools"),
    ToolSpec("frida", "frida", _find_frida, False,
             "Optional (script runner). Install with: pip install frida-tools"),
]


class ToolManager:
    """Resolves, persists and exposes the paths of the external tools."""

    def __init__(self):
        self.specs = {s.key: s for s in TOOL_SPECS}
        self.overrides = {}   # key -> user-picked path (persisted)
        self.paths = {}       # key -> resolved path or None
        self._load_overrides()

    def _load_overrides(self):
        try:
            with open(TOOL_PATHS_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.overrides = {k: v for k, v in data.items()
                                  if isinstance(v, str) and v}
        except Exception:
            self.overrides = {}

    def _save_overrides(self):
        try:
            with open(TOOL_PATHS_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.overrides, fh, indent=2)
        except Exception:
            pass

    def resolve(self, key):
        """Resolve one tool: valid override first, else auto-detect."""
        ov = self.overrides.get(key)
        if ov and os.path.isfile(ov):
            self.paths[key] = ov
            return ov
        if ov:  # saved path no longer exists -> forget it
            self.overrides.pop(key, None)
            self._save_overrides()
        found = self.specs[key].finder()
        self.paths[key] = found
        return found

    def resolve_all(self):
        for key in self.specs:
            self.resolve(key)
        return self.paths

    def get(self, key):
        return self.paths.get(key)

    def set_override(self, key, path):
        """Pin a user-chosen path (empty string clears it back to auto-detect)."""
        path = (path or "").strip()
        if path:
            self.overrides[key] = path
        else:
            self.overrides.pop(key, None)
        self._save_overrides()
        return self.resolve(key)

    def missing_required(self):
        return [self.specs[k] for k in self.specs
                if self.specs[k].required and not self.paths.get(k)]

    # convenience accessors used throughout the app
    @property
    def apktool(self):
        return self.paths.get("apktool")

    @property
    def zipalign(self):
        return self.paths.get("zipalign")

    @property
    def apksigner(self):
        return self.paths.get("apksigner")

    @property
    def adb(self):
        return self.paths.get("adb")

    @property
    def frida_ps(self):
        return self.paths.get("frida_ps")

    @property
    def frida(self):
        return self.paths.get("frida")


TOOLS = ToolManager()
TOOLS.resolve_all()


def _default_keystore():
    """Prefill the keystore field if a single .keystore/.jks sits next to the app."""
    for pat in SETTINGS["keystore"]["auto_detect_globs"]:
        hits = sorted(glob.glob(os.path.join(APP_DIR, pat)))
        if hits:
            return hits[0]
    return ""


DEFAULT_KEYSTORE = _default_keystore()


def list_scripts():
    """Return sorted .js filenames inside SCRIPTS_DIR (creating it if needed)."""
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    return sorted(n for n in os.listdir(SCRIPTS_DIR)
                  if n.lower().endswith(".js") and
                  os.path.isfile(os.path.join(SCRIPTS_DIR, n)))


# Starter scripts written into SCRIPTS_DIR on first run (or via the Seed button).
SEED_SCRIPTS = {
    "ssl-pinning-bypass.js": r"""// Universal-ish SSL pinning bypass (best effort)
// Covers default TrustManager, HttpsURLConnection and OkHttp3 CertificatePinner.
Java.perform(function () {
    // 1) Default X509TrustManager -> accept everything
    try {
        var X509TrustManager = Java.use('javax.net.ssl.X509TrustManager');
        var SSLContext = Java.use('javax.net.ssl.SSLContext');
        var TrustManager = Java.registerClass({
            name: 'com.frida.TrustAll',
            implements: [X509TrustManager],
            methods: {
                checkClientTrusted: function (chain, authType) {},
                checkServerTrusted: function (chain, authType) {},
                getAcceptedIssuers: function () { return []; }
            }
        });
        var tms = [TrustManager.$new()];
        var init = SSLContext.init.overload(
            '[Ljavax.net.ssl.KeyManager;',
            '[Ljavax.net.ssl.TrustManager;',
            'java.security.SecureRandom');
        init.implementation = function (km, tm, sr) {
            init.call(this, km, tms, sr);
        };
    } catch (e) {}

    // 2) OkHttp3 CertificatePinner.check() -> no-op
    try {
        var CertificatePinner = Java.use('okhttp3.CertificatePinner');
        CertificatePinner.check.overload('java.lang.String', 'java.util.List')
            .implementation = function (host, peerCertificates) {
                return;
            };
    } catch (e) {}
});
""",
    "root-detection-bypass.js": r"""// Common root-detection bypass (best effort)
Java.perform(function () {
    var suPaths = ['su', '/system/bin/su', '/system/xbin/su', '/sbin/su',
                   '/system/app/Superuser.apk', '/data/local/su'];

    // File.exists() -> false for known su paths
    try {
        var File = Java.use('java.io.File');
        File.exists.implementation = function () {
            var p = this.getAbsolutePath();
            for (var i = 0; i < suPaths.length; i++) {
                if (p.indexOf(suPaths[i]) !== -1) {
                    return false;
                }
            }
            return this.exists();
        };
    } catch (e) {}

    // Runtime.exec("su"/"which su") -> throw as if missing
    try {
        var Runtime = Java.use('java.lang.Runtime');
        Runtime.exec.overload('java.lang.String').implementation = function (cmd) {
            if (cmd && (cmd.indexOf('su') !== -1 || cmd.indexOf('which') !== -1)) {
                throw Java.use('java.io.IOException').$new('not found');
            }
            return this.exec(cmd);
        };
    } catch (e) {}

    // RootBeer -> always report not rooted
    try {
        var RootBeer = Java.use('com.scottyab.rootbeer.RootBeer');
        RootBeer.isRooted.implementation = function () {
            return false;
        };
    } catch (e) {}
});
""",
    "ram-check-bypass.js": r"""// ram-check-bypass.js — make the device report plenty of RAM so
// "needs at least N GB" gates pass. Reports 8 GB total.
// Covers: ActivityManager.getMemoryInfo().totalMem, isLowRamDevice(),
// and /proc/meminfo "MemTotal" reads (RandomAccessFile / BufferedReader).
var FAKE_RAM_BYTES = 8 * 1024 * 1024 * 1024;       // 8 GB
var FAKE_MEMTOTAL_LINE = 'MemTotal:       8388608 kB';   // 8 GB in kB

Java.perform(function () {
    // 1) ActivityManager.getMemoryInfo(mi) -> inflate mi.totalMem
    try {
        var AM = Java.use('android.app.ActivityManager');
        AM.getMemoryInfo.implementation = function (mi) {
            this.getMemoryInfo(mi);
            try { mi.totalMem.value = FAKE_RAM_BYTES; } catch (e) {}
            return;
        };
    } catch (e) {}

    // 2) ActivityManager.isLowRamDevice() -> false
    try {
        var AM2 = Java.use('android.app.ActivityManager');
        if (AM2.isLowRamDevice) {
            AM2.isLowRamDevice.implementation = function () { return false; };
        }
    } catch (e) {}

    // 3) /proc/meminfo via RandomAccessFile.readLine()
    try {
        var RAF = Java.use('java.io.RandomAccessFile');
        RAF.readLine.implementation = function () {
            var line = this.readLine();
            if (line && line.indexOf('MemTotal') !== -1) return FAKE_MEMTOTAL_LINE;
            return line;
        };
    } catch (e) {}

    // 4) /proc/meminfo via BufferedReader.readLine()
    try {
        var BR = Java.use('java.io.BufferedReader');
        BR.readLine.overload().implementation = function () {
            var line = this.readLine();
            if (line && line.indexOf('MemTotal') !== -1) return FAKE_MEMTOTAL_LINE;
            return line;
        };
    } catch (e) {}
});
""",
    "gpay-billing-spoof.js": r"""// gpay-billing-spoof.js — AUTHORIZED SECURITY TESTING ONLY (your own apps).
// Purpose: attempt a CLIENT-SIDE fake of a successful Google Play purchase to
// verify your app REJECTS it. This is a spoof-resistance test, not a way to
// obtain paid content.
//
//   PASS (spoof-proof): the app still denies the feature -> it verifies the
//                       purchase server-side / by signature. Good.
//   FAIL (vulnerable):  the app unlocks the feature from this fake -> it trusts
//                       client-side billing state. Finding to fix.
//
// Optional: set the GUI "Script arg" (ARG) to your own verifier "class.method"
// (e.g. com.yourapp.util.Security.verifyPurchase) to also force it true.

Java.perform(function () {
    var OK = 0;          // BillingClient.BillingResponseCode.OK
    var PURCHASED = 1;   // Purchase.PurchaseState.PURCHASED

    // 1) Make every Purchase object look completed
    try {
        var Purchase = Java.use('com.android.billingclient.api.Purchase');
        Purchase.getPurchaseState.implementation = function () {
            console.log('[gpay] Purchase.getPurchaseState -> PURCHASED');
            return PURCHASED;
        };
        try { Purchase.isAcknowledged.implementation = function () { return true; }; } catch (e) {}
        try { Purchase.isAutoRenewing.implementation = function () { return true; }; } catch (e) {}
    } catch (e) { console.log('[gpay] Play Billing Purchase class not found'); }

    // 2) Make BillingResult report success
    try {
        var BillingResult = Java.use('com.android.billingclient.api.BillingResult');
        BillingResult.getResponseCode.implementation = function () { return OK; };
    } catch (e) {}

    // 3) Force common client-side signature verifiers to return true.
    var verifiers = [
        ['com.android.billingclient.util.Security', 'verifyPurchase'],
        ['util.Security', 'verifyPurchase']
    ];
    if (typeof ARG !== 'undefined' && ARG && ARG.indexOf('.') !== -1) {
        var i = ARG.lastIndexOf('.');
        verifiers.push([ARG.substring(0, i), ARG.substring(i + 1)]);
    }
    verifiers.forEach(function (pair) {
        try {
            var C = Java.use(pair[0]);
            C[pair[1]].overloads.forEach(function (ov) {
                ov.implementation = function () {
                    console.log('[gpay] ' + pair[0] + '.' + pair[1] + '() -> true');
                    return true;
                };
            });
        } catch (e) {}
    });

    console.log('[gpay] spoof active. If the feature unlocks now, the app is NOT ' +
                'spoof-proof (client-side trust). If it stays locked, server-side ' +
                'verification is doing its job.');
});
""",
    "pref-spoof.js": r"""// pref-spoof.js — return fake values for chosen SharedPreferences keys.
// The GUI injects an OVERRIDES array: [{key, type, value}, ...]
// type is one of: string | boolean | int | long | float
// Without OVERRIDES this script does nothing.

Java.perform(function () {
    if (typeof OVERRIDES === 'undefined' || !OVERRIDES || !OVERRIDES.length) return;

    var map = {};
    OVERRIDES.forEach(function (o) { map[o.type + '::' + o.key] = o.value; });

    function conv(type, v) {
        switch (type) {
            case 'boolean': return (v === true || v === 'true' || v === '1' || v === 1);
            case 'int':     return parseInt(v, 10);
            case 'long':    return parseInt(v, 10);
            case 'float':   return parseFloat(v);
            default:        return (v === null ? null : '' + v);
        }
    }

    function hookGetter(Cls, method, type) {
        try {
            Cls[method].implementation = function (key, defVal) {
                var k = type + '::' + key;
                if (map.hasOwnProperty(k)) {
                    var val = conv(type, map[k]);
                    console.log('[pref] ' + key + ' (' + type + ') -> ' + val);
                    return val;
                }
                return this[method](key, defVal);
            };
        } catch (e) {}
    }

    function hookAll(className) {
        try {
            var C = Java.use(className);
            hookGetter(C, 'getString', 'string');
            hookGetter(C, 'getBoolean', 'boolean');
            hookGetter(C, 'getInt', 'int');
            hookGetter(C, 'getLong', 'long');
            hookGetter(C, 'getFloat', 'float');
        } catch (e) {}
    }

    hookAll('android.app.SharedPreferencesImpl');
    hookAll('androidx.security.crypto.EncryptedSharedPreferences');
});
""",
    "class-tracer.js": r"""// Trace every method of one class.
// Set the class via the GUI "Script arg" field, or edit the fallback below.
var TARGET = (typeof ARG !== 'undefined' && ARG) ? ARG : 'com.example.TargetClass';

Java.perform(function () {
    try {
        var C = Java.use(TARGET);
        var methods = C.class.getDeclaredMethods();
        console.log('[*] Tracing ' + TARGET + ' (' + methods.length + ' methods)');
        methods.forEach(function (m) {
            var name = m.getName();
            var overloads = C[name].overloads;
            overloads.forEach(function (ov) {
                ov.implementation = function () {
                    console.log('--> ' + TARGET + '.' + name + '(' +
                                Array.prototype.join.call(arguments, ', ') + ')');
                    var ret = ov.apply(this, arguments);
                    console.log('<-- ' + name + ' = ' + ret);
                    return ret;
                };
            });
        });
    } catch (e) { console.log('[-] tracer error: ' + e); }
});
""",
    "list-classes.js": r"""// Enumerate loaded classes whose name contains FILTER.
// Set the filter via the GUI "Script arg" field, or edit the fallback below.
var FILTER = (typeof ARG !== 'undefined') ? ARG : 'com.example';

Java.perform(function () {
    console.log('[*] Listing loaded classes matching: "' + FILTER + '"');
    var count = 0;
    Java.enumerateLoadedClasses({
        onMatch: function (name) {
            if (FILTER === '' || name.indexOf(FILTER) !== -1) {
                console.log(name);
                count++;
            }
        },
        onComplete: function () { console.log('[*] done, ' + count + ' matches'); }
    });
});
""",
    "current-screen.js": r"""// current-screen.js — report the screen/UI context we're on.
// Works across: classic XML Activities & Fragments, Jetpack Compose
// (logs Navigation routes/destinations), and games (reports the host Activity).
// Navigate the app after it loads; each screen change is printed live.

Java.perform(function () {
    function tag(s) { console.log('[screen] ' + s); }

    // Skip framework / library classes — we only care about app screens.
    function skip(name) {
        return /^(android\.|androidx\.|com\.android\.|com\.google\.android\.|kotlin|kotlinx\.|java\.|javax\.|dalvik\.|sun\.|libcore\.)/.test(name);
    }

    // ---- current foreground Activity at startup ----
    try {
        var ActivityThread = Java.use('android.app.ActivityThread');
        var at = ActivityThread.currentActivityThread();
        var map = at.mActivities.value;
        var keys = map.keySet().toArray();
        for (var i = 0; i < keys.length; i++) {
            var record = map.get(keys[i]);
            if (!record.paused.value) {
                var n = record.activity.value.getClass().getName();
                if (!skip(n)) tag('current Activity: ' + n);
            }
        }
    } catch (e) { /* probe is best-effort */ }

    // ---- Activity changes ----
    try {
        var Activity = Java.use('android.app.Activity');
        Activity.onResume.implementation = function () {
            var n = this.getClass().getName();
            if (!skip(n)) tag('Activity -> ' + n);
            return this.onResume();
        };
    } catch (e) {}

    // ---- AndroidX Fragments ----
    try {
        var Fragment = Java.use('androidx.fragment.app.Fragment');
        Fragment.onResume.implementation = function () {
            var n = this.getClass().getName();
            if (!skip(n)) tag('Fragment -> ' + n);
            return this.onResume();
        };
    } catch (e) {}

    // ---- Platform Fragments (older apps) ----
    try {
        var PFragment = Java.use('android.app.Fragment');
        PFragment.onResume.implementation = function () {
            var n = this.getClass().getName();
            if (!skip(n)) tag('Fragment(platform) -> ' + n);
            return this.onResume();
        };
    } catch (e) {}

    // ---- Jetpack Compose / Navigation routes ----
    // Compose renders inside one Activity, so the "screen" is the nav route.
    try {
        var NavController = Java.use('androidx.navigation.NavController');
        NavController.navigate.overloads.forEach(function (ov) {
            ov.implementation = function () {
                try {
                    var a0 = arguments.length ? arguments[0] : '(none)';
                    tag('Compose/Nav navigate -> ' + a0);
                } catch (e2) {}
                return ov.apply(this, arguments);
            };
        });
    } catch (e) {}

    // ---- Games: identify the host engine ----
    try { Java.use('com.unity3d.player.UnityPlayer');
          tag('Unity game detected (screen = internal scene; host Activity logged above)'); } catch (e) {}
    try { Java.use('com.epicgames.ue4.GameActivity');
          tag('Unreal game detected (host Activity logged above)'); } catch (e) {}

    tag('ready — navigate the app to see screens');
});
""",
}


def seed_missing_scripts():
    """Write any starter scripts that don't already exist. Returns count written."""
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    written = 0
    for fn, body in SEED_SCRIPTS.items():
        path = os.path.join(SCRIPTS_DIR, fn)
        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(body)
                written += 1
            except Exception:
                pass
    return written


# ---------------------------------------------------------------------------
# Command runner (streams output into a queue consumed by the GUI)
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, log_queue):
        self.q = log_queue

    def log(self, text):
        self.q.put(text)

    def run(self, cmd, cwd=None):
        """Run a command, streaming combined stdout/stderr. Returns exit code."""
        printable = cmd if isinstance(cmd, str) else " ".join(
            f'"{c}"' if " " in c else c for c in cmd
        )
        self.log(f"\n$ {printable}\n")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=isinstance(cmd, str),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            self.log(f"[ERROR] Could not start process: {e}\n")
            return -1

        for line in proc.stdout:
            self.log(line)
        proc.wait()
        self.log(f"[exit code: {proc.returncode}]\n")
        return proc.returncode


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        ui = SETTINGS["ui"]
        self.title(ui["title"])
        self.geometry(ui["geometry"])
        self.minsize(ui["min_width"], ui["min_height"])

        self.log_queue = queue.Queue()
        self.busy = False
        self.fs_proc = None  # running frida script session (Popen) or None

        self._build_widgets()
        self._init_config()
        self._poll_log()
        self._show_tool_status()
        # First-run convenience: populate the library with starter scripts
        if not list_scripts():
            n = seed_missing_scripts()
            if n:
                self.write(f"[setup] Added {n} starter scripts to {SCRIPTS_DIR}\n\n")
            self._refresh_scripts()
        # Kick off an initial live-state check (non-blocking)
        self.after(400, self.refresh_state)
        # If a required tool is missing, walk the user through locating it
        self.after(600, self._prompt_missing_tools)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._save_config()
        if self.fs_proc is not None:
            try:
                self.fs_proc.kill()
            except Exception:
                pass
        self.destroy()

    # -- persistent settings -------------------------------------------------
    def _init_config(self):
        """Register the fields that should survive between launches, load saved
        values, and auto-save (debounced) whenever any of them change."""
        self._cfg_vars = {
            # decompile
            "dec_base": self.dec_base,
            "dec_force": self.dec_force,
            # recompile / signing
            "rec_ks": self.rec_ks,
            "rec_pass": self.rec_pass,
            "rec_alias": self.rec_alias,
            "rec_keypass": self.rec_keypass,
            # frida / adb
            "frida_host": self.frida_host,
            "frida_remote": self.frida_remote,
            "frida_su": self.frida_su,
            # frida script
            "fs_mode": self.fs_mode,
            "fs_target": self.fs_target,
            "fs_script": self.fs_script,
            "fs_choice": self.fs_choice,
            "fs_preload": self.fs_preload,
            "fs_arg": self.fs_arg,
            "fs_spoof_on": self.fs_spoof_on,
            "fs_spoof_json": self.fs_spoof_json,
            # prefs
            "prefs_pkg": self.prefs_pkg,
            "prefs_su": self.prefs_su,
        }
        self._save_after_id = None
        self._load_config()
        self._update_spoof_label()
        for v in self._cfg_vars.values():
            v.trace_add("write", lambda *a: self._schedule_save())

    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        for k, var in self._cfg_vars.items():
            if k in data:
                try:
                    var.set(data[k])
                except Exception:
                    pass

    def _schedule_save(self):
        if self._save_after_id:
            try:
                self.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.after(800, self._save_config)

    def _save_config(self):
        self._save_after_id = None
        if not hasattr(self, "_cfg_vars"):
            return
        data = {k: v.get() for k, v in self._cfg_vars.items()}
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception:
            pass

    # -- widget construction -------------------------------------------------
    def _build_widgets(self):
        # Live status header
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(header, text="Status:").pack(side="left")
        self.state_device = ttk.Label(header, text="Device: …", foreground="#888")
        self.state_device.pack(side="left", padx=(6, 12))
        self.state_server = ttk.Label(header, text="frida-server: …", foreground="#888")
        self.state_server.pack(side="left")
        ttk.Button(header, text="↻ Refresh", command=self.refresh_state).pack(side="right")
        ttk.Button(header, text="▶ Run last", command=self.run_last).pack(side="right", padx=6)
        ttk.Button(header, text="⚙ Tools", command=self.open_tools_dialog).pack(side="right", padx=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="x", padx=10, pady=(6, 0))

        self.tab_dec = ttk.Frame(nb)
        self.tab_rec = ttk.Frame(nb)
        self.tab_frida = ttk.Frame(nb)
        self.tab_scripts = ttk.Frame(nb)
        self.tab_fscript = ttk.Frame(nb)
        self.tab_prefs = ttk.Frame(nb)
        nb.add(self.tab_dec, text="  Decompile  ")
        nb.add(self.tab_rec, text="  Recompile + Sign  ")
        nb.add(self.tab_frida, text="  Frida / ADB  ")
        nb.add(self.tab_scripts, text="  Scripts  ")
        nb.add(self.tab_fscript, text="  Frida Script  ")
        nb.add(self.tab_prefs, text="  Prefs  ")

        self._build_decompile_tab()
        self._build_recompile_tab()
        self._build_frida_tab()
        self._build_scripts_tab()
        self._build_frida_script_tab()
        self._build_prefs_tab()

        # Shared log area
        logframe = ttk.LabelFrame(self, text="Output")
        logframe.pack(fill="both", expand=True, padx=10, pady=(4, 6))

        toolbar = ttk.Frame(logframe)
        toolbar.pack(side="top", fill="x", padx=4, pady=(2, 0))
        ttk.Button(toolbar, text="Clear", command=self.clear_log).pack(side="right")

        body = ttk.Frame(logframe)
        body.pack(side="top", fill="both", expand=True)
        self.log = tk.Text(body, wrap="word", bg="#101418", fg="#d6e2ee",
                           insertbackground="#d6e2ee", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)

        self.status = ttk.Label(self, text="Ready", anchor="w", relief="sunken")
        self.status.pack(fill="x", side="bottom")

    def _build_decompile_tab(self):
        f = self.tab_dec
        for i in range(3):
            f.columnconfigure(1, weight=1)

        ttk.Label(f, text="APK file:").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        self.dec_apk = tk.StringVar()
        ttk.Entry(f, textvariable=self.dec_apk).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse…", command=self._pick_apk_dec).grid(row=0, column=2, padx=8)

        ttk.Label(f, text="Output base dir:").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        self.dec_base = tk.StringVar(value=APP_DIR)
        ttk.Entry(f, textvariable=self.dec_base).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse…", command=self._pick_base_dec).grid(row=1, column=2, padx=8)

        ttk.Label(f, text="Folder name:").grid(row=2, column=0, sticky="w", padx=8, pady=3)
        self.dec_name = tk.StringVar()
        ttk.Entry(f, textvariable=self.dec_name).grid(row=2, column=1, sticky="ew", pady=3)

        self.dec_force = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Overwrite if folder exists (-f)",
                        variable=self.dec_force).grid(row=3, column=1, sticky="w", pady=3)

        ttk.Button(f, text="Decompile", command=self.start_decompile).grid(
            row=4, column=1, sticky="e", pady=6, padx=4)

    def _build_recompile_tab(self):
        f = self.tab_rec
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Code folder:").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        self.rec_src = tk.StringVar()
        ttk.Entry(f, textvariable=self.rec_src).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse…", command=self._pick_src_rec).grid(row=0, column=2, padx=8)

        ttk.Label(f, text="Output APK:").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        self.rec_out = tk.StringVar()
        ttk.Entry(f, textvariable=self.rec_out).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Save as…", command=self._pick_out_rec).grid(row=1, column=2, padx=8)

        ttk.Label(f, text="Keystore:").grid(row=2, column=0, sticky="w", padx=8, pady=3)
        self.rec_ks = tk.StringVar(value=DEFAULT_KEYSTORE)
        ttk.Entry(f, textvariable=self.rec_ks).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse…", command=self._pick_ks_rec).grid(row=2, column=2, padx=8)

        ttk.Label(f, text="Keystore password:").grid(row=3, column=0, sticky="w", padx=8, pady=3)
        self.rec_pass = tk.StringVar(value="")
        ttk.Entry(f, textvariable=self.rec_pass, show="•").grid(row=3, column=1, sticky="ew", pady=3)

        ttk.Label(f, text="Key alias:").grid(row=4, column=0, sticky="w", padx=8, pady=3)
        self.rec_alias = tk.StringVar(value="")
        ttk.Entry(f, textvariable=self.rec_alias).grid(row=4, column=1, sticky="ew", pady=3)

        ttk.Label(f, text="Key password:").grid(row=5, column=0, sticky="w", padx=8, pady=3)
        self.rec_keypass = tk.StringVar(value="")
        ttk.Entry(f, textvariable=self.rec_keypass, show="•").grid(row=5, column=1, sticky="ew", pady=3)

        ttk.Button(f, text="Build → Align → Sign → Verify",
                   command=self.start_recompile).grid(row=6, column=1, sticky="e", pady=6, padx=4)

    def _build_frida_tab(self):
        f = self.tab_frida
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="ADB device (host:port):").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        self.frida_host = tk.StringVar(value=DEFAULT_ADB_HOST)
        ttk.Entry(f, textvariable=self.frida_host).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Connect", command=self.frida_connect).grid(row=0, column=2, padx=8)

        ttk.Label(f, text="frida-server on device:").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        self.frida_remote = tk.StringVar(value=DEFAULT_FRIDA_REMOTE)
        ttk.Entry(f, textvariable=self.frida_remote).grid(row=1, column=1, sticky="ew", pady=3)

        self.frida_su = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Run as root via su (try this if Start fails on Nox)",
                        variable=self.frida_su).grid(row=2, column=1, sticky="w", pady=3)

        ttk.Button(f, text="⚡  Quick start   (connect → start server → check)",
                   command=self.frida_quickstart).grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 2))

        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 3))
        for i in range(3):
            btns.columnconfigure(i, weight=1)
        ttk.Button(btns, text="▶  Start frida-server",
                   command=self.frida_start).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(btns, text="■  Kill frida-server",
                   command=self.frida_kill).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(btns, text="✔  Check Frida",
                   command=self.frida_check).grid(row=0, column=2, sticky="ew", padx=4)

        hint = ("Quick start does the whole daily ritual in one click. "
                "Individual buttons are below if you need them.")
        ttk.Label(f, text=hint, foreground="#888", justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(3, 4))

    def _build_scripts_tab(self):
        f = self.tab_scripts
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text=f"Script library:  {SCRIPTS_DIR}",
                  foreground="#888").grid(row=0, column=0, columnspan=2,
                                          sticky="w", padx=8, pady=(6, 2))

        listwrap = ttk.Frame(f)
        listwrap.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=2)
        f.rowconfigure(1, weight=1)
        self.scripts_list = tk.Listbox(listwrap, height=7, activestyle="dotbox")
        self.scripts_list.pack(side="left", fill="both", expand=True)
        slb = ttk.Scrollbar(listwrap, command=self.scripts_list.yview)
        slb.pack(side="right", fill="y")
        self.scripts_list.config(yscrollcommand=slb.set)
        self.scripts_list.bind("<Double-Button-1>", lambda e: self.script_edit())

        side = ttk.Frame(f)
        side.grid(row=1, column=1, sticky="n", padx=(4, 8), pady=2)
        for i, (txt, cmd) in enumerate([
            ("New…", self.script_new),
            ("Edit", self.script_edit),
            ("Import…", self.script_import),
            ("Delete", self.script_delete),
            ("Seed starters", self.seed_scripts),
            ("Open folder", self.script_open_folder),
            ("Refresh", self._refresh_scripts),
        ]):
            ttk.Button(side, text=txt, width=12, command=cmd).grid(
                row=i, column=0, sticky="ew", pady=2)

        self._refresh_scripts()

    def _build_frida_script_tab(self):
        f = self.tab_fscript
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="From library:").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        self.fs_choice = tk.StringVar()
        self.fs_combo = ttk.Combobox(f, textvariable=self.fs_choice, state="readonly",
                                     values=["(none)"] + list_scripts())
        self.fs_combo.grid(row=0, column=1, sticky="ew", pady=3)
        self.fs_combo.bind("<<ComboboxSelected>>", self._on_pick_library_script)
        ttk.Button(f, text="Refresh", command=self._refresh_scripts).grid(row=0, column=2, padx=8)

        ttk.Label(f, text="Script (.js):").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        self.fs_script = tk.StringVar()
        ttk.Entry(f, textvariable=self.fs_script).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse…", command=self._pick_fs_script).grid(row=1, column=2, padx=8)

        ttk.Label(f, text="Mode:").grid(row=2, column=0, sticky="w", padx=8, pady=3)
        self.fs_mode = tk.StringVar(value="spawn")
        modes = ttk.Frame(f)
        modes.grid(row=2, column=1, sticky="w", pady=3)
        ttk.Radiobutton(modes, text="Spawn (-f)", value="spawn",
                        variable=self.fs_mode).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(modes, text="Attach by name (-n)", value="name",
                        variable=self.fs_mode).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(modes, text="Attach by PID (-p)", value="pid",
                        variable=self.fs_mode).pack(side="left")

        ttk.Label(f, text="Target (package / name / pid):").grid(
            row=3, column=0, sticky="w", padx=8, pady=3)
        self.fs_target = tk.StringVar()
        self.fs_target_combo = ttk.Combobox(f, textvariable=self.fs_target)
        self.fs_target_combo.grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Load", command=self.load_targets).grid(row=3, column=2, padx=8)

        ttk.Label(f, text="Script arg (ARG):").grid(row=4, column=0, sticky="w", padx=8, pady=3)
        self.fs_arg = tk.StringVar()
        ttk.Entry(f, textvariable=self.fs_arg).grid(row=4, column=1, sticky="ew", pady=3)

        self.fs_preload = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f, text='Auto-load helpers first ("*bypass*.js" + current-screen.js)',
            variable=self.fs_preload).grid(row=5, column=1, sticky="w", pady=3)

        # Pref spoofing: typed rules built in a popup, injected into pref-spoof.js
        self.fs_spoof_on = tk.BooleanVar(value=False)
        self.fs_spoof_json = tk.StringVar(value="[]")
        spoof = ttk.Frame(f)
        spoof.grid(row=6, column=1, sticky="w", pady=3)
        ttk.Checkbutton(spoof, text="Spoof prefs", variable=self.fs_spoof_on).pack(side="left")
        ttk.Button(spoof, text="Edit rules…", command=self.spoof_edit).pack(side="left", padx=6)
        self.fs_spoof_lbl = ttk.Label(spoof, text="", foreground="#888")
        self.fs_spoof_lbl.pack(side="left")

        btns = ttk.Frame(f)
        btns.grid(row=7, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 3))
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)
        self.fs_run_btn = ttk.Button(btns, text="▶  Run script", command=self.frida_run)
        self.fs_run_btn.grid(row=0, column=0, sticky="ew", padx=4)
        self.fs_stop_btn = ttk.Button(btns, text="■  Stop", command=self.frida_run_stop,
                                      state="disabled")
        self.fs_stop_btn.grid(row=0, column=1, sticky="ew", padx=4)

        hint = ('"Spoof prefs" hooks SharedPreferences getters to return your typed values '
                '(string/boolean/int/long/float). Build rules with "Edit rules…". Helpers, '
                'pref-spoof and your chosen script all run in one session.')
        ttk.Label(f, text=hint, foreground="#888", justify="left",
                  wraplength=760).grid(row=8, column=0, columnspan=3,
                                       sticky="w", padx=8, pady=(3, 4))
        self._update_spoof_label()

    def _build_prefs_tab(self):
        f = self.tab_prefs
        f.columnconfigure(0, weight=1)

        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 2))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Package:").grid(row=0, column=0, sticky="w")
        self.prefs_pkg = tk.StringVar()
        ttk.Entry(top, textvariable=self.prefs_pkg).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Use Frida target",
                   command=lambda: self.prefs_pkg.set(self.fs_target.get().strip())
                   ).grid(row=0, column=2)

        self.prefs_su = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Use root via su (needed for most apps)",
                        variable=self.prefs_su).grid(row=1, column=0, sticky="w", padx=8, pady=2)

        listwrap = ttk.Frame(f)
        listwrap.grid(row=2, column=0, sticky="nsew", padx=(8, 4), pady=2)
        f.rowconfigure(2, weight=1)
        self.prefs_list = tk.Listbox(listwrap, height=7, activestyle="dotbox")
        self.prefs_list.pack(side="left", fill="both", expand=True)
        plb = ttk.Scrollbar(listwrap, command=self.prefs_list.yview)
        plb.pack(side="right", fill="y")
        self.prefs_list.config(yscrollcommand=plb.set)
        self.prefs_list.bind("<Double-Button-1>", lambda e: self.prefs_edit())

        side = ttk.Frame(f)
        side.grid(row=2, column=1, sticky="n", padx=(4, 8), pady=2)
        for txt, cmd in [
            ("List files", self.prefs_listfiles),
            ("View / Edit", self.prefs_edit),
            ("Delete", self.prefs_delete),
        ]:
            ttk.Button(side, text=txt, width=12, command=cmd).pack(fill="x", pady=2)

        hint = ("Reads /data/data/<pkg>/shared_prefs/*.xml over adb (no Frida). "
                "Edit while the app is closed so it doesn't overwrite your changes, "
                "then restart the app to load them.")
        ttk.Label(f, text=hint, foreground="#888", wraplength=760, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 4))

    # -- file pickers --------------------------------------------------------
    def _pick_apk_dec(self):
        p = filedialog.askopenfilename(title="Select APK",
                                       filetypes=[("APK files", "*.apk"), ("All", "*.*")],
                                       initialdir=APP_DIR)
        if p:
            self.dec_apk.set(p)
            if not self.dec_name.get():
                self.dec_name.set(os.path.splitext(os.path.basename(p))[0])

    def _pick_base_dec(self):
        p = filedialog.askdirectory(title="Output base directory", initialdir=self.dec_base.get() or APP_DIR)
        if p:
            self.dec_base.set(p)

    def _pick_src_rec(self):
        p = filedialog.askdirectory(title="Select code folder", initialdir=APP_DIR)
        if p:
            self.rec_src.set(p)
            if not self.rec_out.get():
                self.rec_out.set(os.path.join(APP_DIR, os.path.basename(p.rstrip("\\/")) + "_signed.apk"))

    def _pick_out_rec(self):
        p = filedialog.asksaveasfilename(title="Output APK", defaultextension=".apk",
                                         filetypes=[("APK files", "*.apk")], initialdir=APP_DIR)
        if p:
            self.rec_out.set(p)

    def _pick_ks_rec(self):
        p = filedialog.askopenfilename(title="Select keystore", initialdir=APP_DIR,
                                       filetypes=[("Keystore", "*.keystore *.jks"), ("All", "*.*")])
        if p:
            self.rec_ks.set(p)

    def _pick_fs_script(self):
        p = filedialog.askopenfilename(title="Select Frida script", initialdir=SCRIPTS_DIR,
                                       filetypes=[("JavaScript", "*.js"), ("All", "*.*")])
        if p:
            self.fs_script.set(p)

    # -- script library ------------------------------------------------------
    def _refresh_scripts(self):
        """Reload the library listbox and the Frida Script combobox."""
        names = list_scripts()
        if hasattr(self, "scripts_list"):
            self.scripts_list.delete(0, "end")
            for n in names:
                self.scripts_list.insert("end", n)
        if hasattr(self, "fs_combo"):
            self.fs_combo["values"] = ["(none)"] + names

    def _on_pick_library_script(self, _evt=None):
        name = self.fs_choice.get()
        if not name or name == "(none)":
            self.fs_script.set("")
        else:
            self.fs_script.set(os.path.join(SCRIPTS_DIR, name))

    def _selected_script(self):
        sel = self.scripts_list.curselection()
        if not sel:
            return None
        return self.scripts_list.get(sel[0])

    def script_open_folder(self):
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        try:
            os.startfile(SCRIPTS_DIR)  # Windows
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n{e}")

    def script_new(self):
        self._open_script_editor(None)

    def script_edit(self):
        name = self._selected_script()
        if not name:
            messagebox.showinfo("No selection", "Select a script to edit.")
            return
        self._open_script_editor(name)

    def script_import(self):
        srcs = filedialog.askopenfilenames(
            title="Import .js script(s)", initialdir=APP_DIR,
            filetypes=[("JavaScript", "*.js"), ("All", "*.*")])
        if not srcs:
            return
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        for src in srcs:
            dst = os.path.join(SCRIPTS_DIR, os.path.basename(src))
            if os.path.abspath(src) == os.path.abspath(dst):
                continue
            if os.path.exists(dst) and not messagebox.askyesno(
                    "Overwrite?", f"{os.path.basename(dst)} already exists. Overwrite?"):
                continue
            try:
                shutil.copyfile(src, dst)
            except Exception as e:
                messagebox.showerror("Error", f"Could not import {src}:\n{e}")
        self._refresh_scripts()

    def script_delete(self):
        name = self._selected_script()
        if not name:
            messagebox.showinfo("No selection", "Select a script to delete.")
            return
        if not messagebox.askyesno("Delete", f"Delete '{name}' permanently?"):
            return
        try:
            os.remove(os.path.join(SCRIPTS_DIR, name))
        except Exception as e:
            messagebox.showerror("Error", f"Could not delete:\n{e}")
        self._refresh_scripts()

    def _open_script_editor(self, name):
        """Popup editor for a new (name=None) or existing script."""
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        win = tk.Toplevel(self)
        win.title(f"Edit: {name}" if name else "New script")
        win.geometry("720x520")
        win.transient(self)

        top = ttk.Frame(win)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="File name:").pack(side="left")
        name_var = tk.StringVar(value=name or "")
        name_entry = ttk.Entry(top, textvariable=name_var)
        name_entry.pack(side="left", fill="x", expand=True, padx=6)

        editor = tk.Text(win, wrap="none", bg="#101418", fg="#d6e2ee",
                         insertbackground="#d6e2ee", font=("Consolas", 10), undo=True)
        editor.pack(fill="both", expand=True, padx=8, pady=4)

        if name:
            try:
                with open(os.path.join(SCRIPTS_DIR, name), "r", encoding="utf-8") as fh:
                    editor.insert("1.0", fh.read())
            except Exception as e:
                editor.insert("1.0", f"// could not read file: {e}\n")
        else:
            editor.insert("1.0",
                          "// New Frida script\n"
                          "Java.perform(function () {\n"
                          "    console.log('[*] script loaded');\n"
                          "});\n")
            name_entry.focus_set()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(0, 8))

        def save():
            fn = name_var.get().strip()
            if not fn:
                messagebox.showerror("Error", "Enter a file name.", parent=win)
                return
            if not fn.lower().endswith(".js"):
                fn += ".js"
            path = os.path.join(SCRIPTS_DIR, fn)
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(editor.get("1.0", "end-1c"))
            except Exception as e:
                messagebox.showerror("Error", f"Could not save:\n{e}", parent=win)
                return
            self._refresh_scripts()
            self.set_status(f"Saved script: {fn}")
            win.destroy()

        ttk.Button(bar, text="Save", command=save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # -- logging / status ----------------------------------------------------
    def _poll_log(self):
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log.insert("end", text)
                self.log.see("end")
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    def write(self, text):
        self.log_queue.put(text)

    def clear_log(self):
        # drain anything still queued so it can't repopulate after clearing
        try:
            while True:
                self.log_queue.get_nowait()
        except queue.Empty:
            pass
        self.log.delete("1.0", "end")

    def set_status(self, text):
        self.status.config(text=text)

    # -- tool paths (generic discovery + manual override) --------------------
    def _prompt_missing_tools(self):
        """On startup, if a required tool wasn't found, offer to locate it."""
        missing = TOOLS.missing_required()
        if not missing:
            return
        names = ", ".join(s.label for s in missing)
        if messagebox.askyesno(
                "Set up tools",
                f"These required tools were not found automatically:\n\n  {names}\n\n"
                "Decompile / recompile / adb features need them.\n\n"
                "Open the Tools dialog now to locate them?"):
            self.open_tools_dialog()

    def open_tools_dialog(self):
        """Show / edit the resolved path of every external tool."""
        win = tk.Toplevel(self)
        win.title("Tool paths")
        win.transient(self)
        win.grab_set()
        win.resizable(True, False)

        intro = ttk.Label(
            win, wraplength=620, justify="left",
            text=("Paths are auto-detected from PATH, the Android SDK and common "
                  "emulator installs. Override any of them below if detection is "
                  "wrong or a tool lives somewhere unusual. Leave a field blank to "
                  "fall back to auto-detection."))
        intro.grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(10, 8))

        vars_by_key = {}
        status_by_key = {}
        row = 1
        for spec in TOOL_SPECS:
            req = " *" if spec.required else ""
            ttk.Label(win, text=f"{spec.label}{req}:").grid(
                row=row, column=0, sticky="w", padx=(10, 4), pady=3)

            var = tk.StringVar(value=TOOLS.get(spec.key) or "")
            vars_by_key[spec.key] = var
            entry = ttk.Entry(win, textvariable=var, width=64)
            entry.grid(row=row, column=1, sticky="ew", pady=3)

            ttk.Button(win, text="Browse…",
                       command=lambda k=spec.key: self._browse_tool(k, vars_by_key)
                       ).grid(row=row, column=2, padx=4)

            st = ttk.Label(win, text="", width=12)
            st.grid(row=row, column=3, padx=(4, 10), sticky="w")
            status_by_key[spec.key] = st

            ttk.Label(win, text=spec.hint, wraplength=600, foreground="#888",
                      justify="left").grid(row=row + 1, column=1, columnspan=3,
                                           sticky="w", pady=(0, 6))
            row += 2

        win.columnconfigure(1, weight=1)

        def refresh_status():
            for spec in TOOL_SPECS:
                p = vars_by_key[spec.key].get().strip()
                lbl = status_by_key[spec.key]
                if p and os.path.isfile(p):
                    lbl.config(text="● found", foreground="#3fb950")
                elif p:
                    lbl.config(text="● missing", foreground="#f85149")
                elif spec.required:
                    lbl.config(text="● not set", foreground="#f85149")
                else:
                    lbl.config(text="○ optional", foreground="#888")

        def redetect_all():
            # Clear overrides, re-run auto-detection, repopulate the fields.
            for spec in TOOL_SPECS:
                TOOLS.set_override(spec.key, "")
            TOOLS.resolve_all()
            for spec in TOOL_SPECS:
                vars_by_key[spec.key].set(TOOLS.get(spec.key) or "")
            refresh_status()

        def save_and_close():
            for spec in TOOL_SPECS:
                val = vars_by_key[spec.key].get().strip()
                # Only pin as an override when it differs from auto-detection;
                # this keeps fields auto-updating when the SDK/emulator moves.
                detected = spec.finder()
                if val and val != (detected or ""):
                    TOOLS.set_override(spec.key, val)
                else:
                    TOOLS.set_override(spec.key, "")
            TOOLS.resolve_all()
            win.destroy()
            self._show_tool_status()
            self.refresh_state()

        for var in vars_by_key.values():
            var.trace_add("write", lambda *a: refresh_status())
        refresh_status()

        btns = ttk.Frame(win)
        btns.grid(row=row, column=0, columnspan=4, sticky="e", padx=10, pady=(8, 12))
        ttk.Button(btns, text="Re-detect all", command=redetect_all).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=save_and_close).pack(side="left", padx=4)

    def _browse_tool(self, key, vars_by_key):
        spec = TOOLS.specs[key]
        path = filedialog.askopenfilename(
            title=f"Locate {spec.label}",
            initialdir=os.path.dirname(vars_by_key[key].get() or "") or APP_DIR)
        if path:
            vars_by_key[key].set(path)

    def _show_tool_status(self):
        self.write("=== APK Tool GUI ===\n")
        for spec in TOOL_SPECS:
            path = TOOLS.get(spec.key)
            if path:
                self.write(f"{spec.label:<9}: {path}\n")
            else:
                tag = "NOT FOUND" if spec.required else "not found (optional)"
                self.write(f"{spec.label:<9}: {tag}\n")
        missing = TOOLS.missing_required()
        if missing:
            names = ", ".join(s.label for s in missing)
            self.write(f"[WARN] Required tool(s) not found: {names}.\n"
                       "       Click '⚙ Tools' to locate them.\n")
        self.write("\n")

    def _set_busy(self, busy):
        self.busy = busy
        self.set_status("Working…" if busy else "Ready")

    # -- decompile -----------------------------------------------------------
    def start_decompile(self):
        if self.busy:
            messagebox.showinfo("Busy", "A task is already running.")
            return
        apk = self.dec_apk.get().strip()
        base = self.dec_base.get().strip() or APP_DIR
        name = self.dec_name.get().strip()

        if not apk or not os.path.isfile(apk):
            messagebox.showerror("Error", "Please select a valid APK file.")
            return
        if not name:
            messagebox.showerror("Error", "Please enter an output folder name.")
            return
        if not TOOLS.apktool:
            messagebox.showerror("Error", "apktool was not found. Click '⚙ Tools' to locate it.")
            return

        out = os.path.join(base, name)
        threading.Thread(target=self._do_decompile, args=(apk, out), daemon=True).start()

    def _do_decompile(self, apk, out):
        self._set_busy(True)
        r = Runner(self.log_queue)
        cmd = [TOOLS.apktool, "d"]
        if self.dec_force.get():
            cmd.append("-f")
        cmd += [apk, "-o", out]
        code = r.run(cmd)
        if code == 0:
            self.write(f"\n✔ Decompiled to: {out}\n")
            self.set_status("Decompile complete")
        else:
            self.write("\n✗ Decompile FAILED.\n")
            self.set_status("Decompile failed")
        self._set_busy(False)

    # -- recompile -----------------------------------------------------------
    def start_recompile(self):
        if self.busy:
            messagebox.showinfo("Busy", "A task is already running.")
            return
        src = self.rec_src.get().strip()
        out = self.rec_out.get().strip()
        ks = self.rec_ks.get().strip()
        kspass = self.rec_pass.get()
        alias = self.rec_alias.get().strip()
        keypass = self.rec_keypass.get()

        if not src or not os.path.isdir(src):
            messagebox.showerror("Error", "Please select a valid code folder.")
            return
        if not out:
            messagebox.showerror("Error", "Please choose an output APK path.")
            return
        if not ks or not os.path.isfile(ks):
            messagebox.showerror("Error", "Please select a valid keystore file.")
            return
        if not (TOOLS.apktool and TOOLS.zipalign and TOOLS.apksigner):
            messagebox.showerror(
                "Error",
                "apktool / zipalign / apksigner not all found.\n"
                "Click '⚙ Tools' to locate the missing one(s).")
            return

        threading.Thread(target=self._do_recompile,
                         args=(src, out, ks, kspass, alias, keypass), daemon=True).start()

    def _do_recompile(self, src, out, ks, kspass, alias, keypass):
        self._set_busy(True)
        r = Runner(self.log_queue)

        out_dir = os.path.dirname(out) or APP_DIR
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(out))[0]
        unsigned = os.path.join(out_dir, base + "_unsigned.apk")
        aligned = os.path.join(out_dir, base + "_aligned.apk")

        # 1) build
        self.set_status("Building APK…")
        self.write("\n--- STEP 1/4: apktool build ---\n")
        if r.run([TOOLS.apktool, "b", src, "-o", unsigned]) != 0:
            self.write("\n✗ Build FAILED.\n"); self.set_status("Build failed"); self._set_busy(False); return

        # 2) zipalign
        self.set_status("Aligning…")
        self.write("\n--- STEP 2/4: zipalign ---\n")
        if os.path.isfile(aligned):
            os.remove(aligned)
        if r.run([TOOLS.zipalign, "-f", "-p", "4", unsigned, aligned]) != 0:
            self.write("\n✗ zipalign FAILED.\n"); self.set_status("Align failed"); self._set_busy(False); return

        # 3) sign
        self.set_status("Signing…")
        self.write("\n--- STEP 3/4: apksigner sign ---\n")
        sign_cmd = [TOOLS.apksigner, "sign",
                    "--ks", ks,
                    "--ks-pass", f"pass:{kspass}",
                    "--key-pass", f"pass:{keypass}"]
        if alias:
            sign_cmd += ["--ks-key-alias", alias]
        sign_cmd += ["--out", out, aligned]
        if r.run(sign_cmd) != 0:
            self.write("\n✗ Signing FAILED.\n"); self.set_status("Sign failed"); self._set_busy(False); return

        # 4) verify
        self.set_status("Verifying…")
        self.write("\n--- STEP 4/4: apksigner verify ---\n")
        code = r.run([TOOLS.apksigner, "verify", "--verbose", out])
        if code == 0:
            self.write(f"\n✔ DONE. Signed & verified APK:\n   {out}\n")
            self.set_status("Success — signed & verified")
        else:
            self.write("\n✗ Verification FAILED.\n")
            self.set_status("Verify failed")
        self._set_busy(False)

    # -- frida / adb ---------------------------------------------------------
    def _adb(self, args):
        """Build an adb command list, applying the device serial from host field."""
        host = self.frida_host.get().strip()
        cmd = [TOOLS.adb]
        if host:
            cmd += ["-s", host]
        return cmd + args

    def _remote_shell(self, inner):
        """Wrap a device-side shell command, optionally through su."""
        if self.frida_su.get():
            return self._adb(["shell", "su", "-c", inner])
        return self._adb(["shell", inner])

    def _capture(self, cmd):
        """Run a command and return (returncode, stripped stdout)."""
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20,
                                 creationflags=CREATE_NO_WINDOW)
            return res.returncode, (res.stdout or "") + (res.stderr or "")
        except Exception as e:
            return -1, str(e)

    def _frida_pids(self):
        """Return device PIDs of any running frida-server process.

        Robust against truncated process names: scans `ps` for lines mentioning
        'frida' (the kernel comm is capped at 15 chars, so a binary launched as
        'frida-server-17.14.1-...' shows up as 'frida-server-17' and 'pidof
        frida-server' would miss it).
        """
        pids = []
        rc, out = self._capture(self._remote_shell("ps -A"))
        if rc != 0 or not out.strip():
            rc, out = self._capture(self._remote_shell("ps"))
        for line in out.splitlines():
            if "frida" in line.lower():
                for tok in line.split():
                    if tok.isdigit():
                        pids.append(tok)
                        break
        return sorted(set(pids), key=int)

    def _frida_guard(self):
        if self.busy:
            messagebox.showinfo("Busy", "A task is already running.")
            return False
        if not TOOLS.adb:
            messagebox.showerror(
                "Error",
                "adb was not found.\n"
                "Click '⚙ Tools' to locate it (Android platform-tools or your "
                "emulator's bin folder).")
            return False
        return True

    def frida_connect(self):
        if not self._frida_guard():
            return
        threading.Thread(target=self._do_frida_connect, daemon=True).start()

    def _do_frida_connect(self):
        self._set_busy(True)
        r = Runner(self.log_queue)
        host = self.frida_host.get().strip()
        self.write("\n--- adb connect ---\n")
        if host:
            r.run([TOOLS.adb, "connect", host])
        r.run([TOOLS.adb, "devices"])
        self.set_status("adb connect done")
        self._set_busy(False)

    def frida_start(self):
        if not self._frida_guard():
            return
        threading.Thread(target=self._do_frida_start, daemon=True).start()

    def _do_frida_start(self):
        self._set_busy(True)
        r = Runner(self.log_queue)
        host = self.frida_host.get().strip()
        remote = self.frida_remote.get().strip() or DEFAULT_FRIDA_REMOTE

        self.write("\n=== Starting frida-server ===\n")
        if host:
            r.run([TOOLS.adb, "connect", host])

        # already running?
        pids = self._frida_pids()
        if pids:
            self.write(f"frida-server already running (pid {', '.join(pids)}).\n")
            self.set_status("frida-server already running")
            self._set_busy(False)
            return

        # ensure it's executable, then launch detached on the device
        r.run(self._remote_shell(f"chmod 755 {remote}"))
        inner = f"{remote} > /dev/null 2>&1 &"
        launch = self._remote_shell(inner)
        self.write(f"\n$ {' '.join(launch)}  (detached)\n")
        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            subprocess.Popen(launch, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags)
        except Exception as e:
            self.write(f"[ERROR] Failed to launch: {e}\n")
            self.set_status("frida-server start failed")
            self._set_busy(False)
            return

        time.sleep(2.0)  # give the server a moment to come up
        pids = self._frida_pids()
        if pids:
            self.write(f"\n✔ frida-server is running (pid {', '.join(pids)}).\n")
            self.set_status("frida-server started")
        else:
            self.write("\n✗ Could not confirm frida-server started.\n"
                       "   Try enabling 'Run as root via su', and check the path.\n")
            self.set_status("frida-server not confirmed")
        self._set_busy(False)
        self.refresh_state()

    def frida_kill(self):
        if not self._frida_guard():
            return
        threading.Thread(target=self._do_frida_kill, daemon=True).start()

    def _do_frida_kill(self):
        self._set_busy(True)
        r = Runner(self.log_queue)
        self.write("\n=== Killing frida-server ===\n")

        pids = self._frida_pids()
        if not pids:
            self.write("Nothing to kill — no frida-server process found.\n")
            self.set_status("frida-server not running")
            self._set_busy(False)
            return

        self.write(f"Found frida-server pid(s): {', '.join(pids)}\n")
        # kill each pid directly (more reliable than pkill on toybox)
        r.run(self._remote_shell("kill -9 " + " ".join(pids)))
        time.sleep(0.8)

        left = self._frida_pids()
        if left:
            self.write(f"\n✗ Still running (pid {', '.join(left)}). "
                       "Enable 'Run as root via su' and try again — "
                       "the server was likely started as root.\n")
            self.set_status("frida-server still running")
        else:
            self.write("\n✔ frida-server stopped.\n")
            self.set_status("frida-server killed")
        self._set_busy(False)
        self.refresh_state()

    def frida_check(self):
        if not self._frida_guard():
            return
        threading.Thread(target=self._do_frida_check, daemon=True).start()

    def _do_frida_check(self):
        self._set_busy(True)
        r = Runner(self.log_queue)
        self.write("\n=== Checking Frida ===\n")

        # 1) is the frida-server process actually alive on the device?
        pids = self._frida_pids()
        if pids:
            self.write(f"• Device process: RUNNING (pid {', '.join(pids)}).\n")
            # show the matching ps lines so you can see exactly what's running
            rc, out = self._capture(self._remote_shell("ps -A"))
            for line in out.splitlines():
                if "frida" in line.lower():
                    self.write(f"    {line.strip()}\n")
        else:
            self.write("• Device process: NOT running.\n")

        # 2) does the local frida client connect? (port 27042 must answer)
        client_ok = False
        if TOOLS.frida_ps:
            self.write("\n--- frida-ps -U ---\n")
            client_ok = (r.run([TOOLS.frida_ps, "-U"]) == 0)
        else:
            self.write("\n[WARN] frida-ps not found on PATH. "
                       "Install it with: pip install frida-tools\n")

        # verdict combines both signals
        self.write("\n")
        if pids and client_ok:
            self.write("✔ Frida is WORKING — server running and client connected.\n")
            self.set_status("Frida OK")
        elif pids and not client_ok:
            self.write("⚠ Server process is running but the client could NOT connect.\n"
                       "  Likely a version mismatch (PC frida vs device frida-server).\n")
            self.set_status("Frida: server up, client failed")
        elif not pids and client_ok:
            self.write("⚠ Client connected but no frida-server process was detected.\n"
                       "  It may be running under a name ps didn't show, or via gadget.\n")
            self.set_status("Frida: connected, process unseen")
        else:
            self.write("✗ Frida is NOT running. Click 'Start frida-server'.\n")
            self.set_status("Frida not running")
        self._set_busy(False)

    def frida_quickstart(self):
        if not self._frida_guard():
            return
        threading.Thread(target=self._do_quickstart, daemon=True).start()

    def _do_quickstart(self):
        # connect → start server → check, reusing the individual steps
        self._do_frida_connect()
        self._do_frida_start()
        self._do_frida_check()

    # -- target picker -------------------------------------------------------
    def load_targets(self):
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found. Click '⚙ Tools' to locate it.")
            return
        threading.Thread(target=self._do_load_targets, daemon=True).start()

    def _do_load_targets(self):
        mode = self.fs_mode.get()
        self.write("\n--- Loading targets from device ---\n")
        items = []
        if mode == "spawn":
            # installed third-party packages (what you usually instrument)
            rc, out = self._capture(self._adb(["shell", "pm", "list", "packages", "-3"]))
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    items.append(line[len("package:"):])
            items = sorted(set(items))
            self.write(f"Found {len(items)} third-party packages.\n")
        else:
            # running processes (for attach by name / pid)
            if not TOOLS.frida_ps:
                self.write("[WARN] frida-ps not found; cannot list running processes.\n")
                return
            rc, out = self._capture([TOOLS.frida_ps, "-U"])
            for line in out.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    if mode == "pid":
                        items.append(parts[0])
                    else:
                        items.append(parts[1].strip())
            items = sorted(set(items), key=str.lower)
            self.write(f"Found {len(items)} running processes.\n")

        self.after(0, lambda: self.fs_target_combo.config(values=items))
        if items:
            self.set_status(f"Loaded {len(items)} targets ({mode})")
        else:
            self.write("No targets found — is the device connected / frida-server running?\n")

    # -- live status ---------------------------------------------------------
    def refresh_state(self):
        threading.Thread(target=self._do_refresh_state, daemon=True).start()

    def _set_state(self, label, name, state):
        """state: True=ok(green), False=bad(red), None=unknown(grey)."""
        if state is True:
            label.config(text=f"{name}: ● up", foreground="#3fb950")
        elif state is False:
            label.config(text=f"{name}: ● down", foreground="#f85149")
        else:
            label.config(text=f"{name}: ● ?", foreground="#888")

    def _do_refresh_state(self):
        host = self.frida_host.get().strip()
        connected = None
        if TOOLS.adb and host:
            rc, out = self._capture([TOOLS.adb, "-s", host, "get-state"])
            connected = (out.strip() == "device")
        self.after(0, lambda: self._set_state(self.state_device, "Device", connected))

        running = None
        if connected:
            running = bool(self._frida_pids())
        elif connected is False:
            running = False
        self.after(0, lambda: self._set_state(self.state_server, "frida-server", running))

    def run_last(self):
        if not self.fs_target.get().strip():
            messagebox.showinfo(
                "Run last",
                "Set a target in the Frida Script tab first.\n"
                "After that, 'Run last' replays it with one click.")
            return
        self.frida_run()

    # -- seed scripts --------------------------------------------------------
    def seed_scripts(self):
        n = seed_missing_scripts()
        self._refresh_scripts()
        if n:
            self.set_status(f"Added {n} starter script(s)")
            messagebox.showinfo("Seed", f"Added {n} starter script(s) to the library.")
        else:
            messagebox.showinfo("Seed", "All starter scripts already present.")

    # -- pref spoof rule builder ---------------------------------------------
    def _spoof_rules(self):
        try:
            rules = json.loads(self.fs_spoof_json.get() or "[]")
            return [r for r in rules if isinstance(r, dict) and r.get("key")]
        except Exception:
            return []

    def _update_spoof_label(self):
        if hasattr(self, "fs_spoof_lbl"):
            n = len(self._spoof_rules())
            self.fs_spoof_lbl.config(text=f"({n} rule{'s' if n != 1 else ''})" if n else "(none)")

    def spoof_edit(self):
        self._open_spoof_builder()

    def _open_spoof_builder(self):
        win = tk.Toplevel(self)
        win.title("Pref spoof rules")
        win.geometry("640x420")
        win.transient(self)

        ttk.Label(win, text="Return fake SharedPreferences values. "
                            "Key = pref name, Type, Value.",
                  foreground="#888", wraplength=600).pack(anchor="w", padx=10, pady=(10, 4))

        head = ttk.Frame(win)
        head.pack(fill="x", padx=10)
        ttk.Label(head, text="Key", width=26).pack(side="left")
        ttk.Label(head, text="Type", width=12).pack(side="left")
        ttk.Label(head, text="Value", width=22).pack(side="left")

        rows_wrap = ttk.Frame(win)
        rows_wrap.pack(fill="both", expand=True, padx=10, pady=2)
        self._spoof_rows = []

        def add_row(key="", typ="string", val=""):
            row = ttk.Frame(rows_wrap)
            row.pack(fill="x", pady=2)
            kv = tk.StringVar(value=key)
            tv = tk.StringVar(value=typ)
            vv = tk.StringVar(value=val)
            ttk.Entry(row, textvariable=kv, width=26).pack(side="left")
            ttk.Combobox(row, textvariable=tv, width=10, state="readonly",
                         values=["string", "boolean", "int", "long", "float"]).pack(side="left", padx=2)
            ttk.Entry(row, textvariable=vv, width=22).pack(side="left", padx=2)
            rec = {"frame": row, "k": kv, "t": tv, "v": vv}

            def remove():
                row.destroy()
                if rec in self._spoof_rows:
                    self._spoof_rows.remove(rec)
            ttk.Button(row, text="✕", width=3, command=remove).pack(side="left")
            self._spoof_rows.append(rec)

        existing = self._spoof_rules()
        if existing:
            for r in existing:
                add_row(r.get("key", ""), r.get("type", "string"), str(r.get("value", "")))
        else:
            add_row()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(bar, text="+ Add rule", command=lambda: add_row()).pack(side="left")

        def save():
            rules = []
            for rec in self._spoof_rows:
                k = rec["k"].get().strip()
                if not k:
                    continue
                rules.append({"key": k, "type": rec["t"].get(), "value": rec["v"].get()})
            self.fs_spoof_json.set(json.dumps(rules))
            if rules:
                self.fs_spoof_on.set(True)
            self._update_spoof_label()
            win.destroy()

        ttk.Button(bar, text="Save", command=save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # -- shared_prefs (static, no Frida) -------------------------------------
    def _prefs_base(self):
        return f"/data/data/{self.prefs_pkg.get().strip()}/shared_prefs"

    def _prefs_shell(self, inner):
        host = self.frida_host.get().strip()
        base = [TOOLS.adb] + (["-s", host] if host else [])
        if self.prefs_su.get():
            return base + ["shell", "su", "-c", inner]
        return base + ["shell", inner]

    def _prefs_guard(self):
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found. Click '⚙ Tools' to locate it.")
            return False
        if not self.prefs_pkg.get().strip():
            messagebox.showerror("Error", "Enter the app package name.")
            return False
        return True

    def _selected_pref(self):
        sel = self.prefs_list.curselection()
        return self.prefs_list.get(sel[0]) if sel else None

    def prefs_listfiles(self):
        if not self._prefs_guard():
            return
        threading.Thread(target=self._do_prefs_list, daemon=True).start()

    def _do_prefs_list(self):
        base = self._prefs_base()
        self.write(f"\n=== Prefs: {base} ===\n")
        rc, out = self._capture(self._prefs_shell(f"ls -1 {base}"))
        files = [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".xml")]
        if not files:
            low = out.lower()
            if "denied" in low or "not permitted" in low:
                self.write("Permission denied — tick 'Use root via su'.\n")
            elif "no such file" in low:
                self.write("No shared_prefs folder (app may not have written prefs yet).\n")
            else:
                self.write((out.strip() or "No .xml pref files found.") + "\n")
        self.after(0, lambda: self._fill_prefs_list(files))
        self.set_status(f"{len(files)} pref file(s)")

    def _fill_prefs_list(self, files):
        self.prefs_list.delete(0, "end")
        for fn in files:
            self.prefs_list.insert("end", fn)

    def prefs_edit(self):
        if not self._prefs_guard():
            return
        fn = self._selected_pref()
        if not fn:
            messagebox.showinfo("No selection", "Select a pref file (or click List files first).")
            return
        threading.Thread(target=self._do_prefs_open, args=(fn,), daemon=True).start()

    def _do_prefs_open(self, fn):
        rc, out = self._capture(self._prefs_shell(f"cat {self._prefs_base()}/{fn}"))
        self.after(0, lambda: self._open_pref_editor(fn, out))

    def _open_pref_editor(self, fn, content):
        win = tk.Toplevel(self)
        win.title(f"Prefs: {self.prefs_pkg.get().strip()} / {fn}")
        win.geometry("760x560")
        win.transient(self)
        ttk.Label(win, text=f"{self._prefs_base()}/{fn}",
                  foreground="#888").pack(anchor="w", padx=8, pady=(8, 2))
        editor = tk.Text(win, wrap="none", bg="#101418", fg="#d6e2ee",
                         insertbackground="#d6e2ee", font=("Consolas", 10), undo=True)
        editor.pack(fill="both", expand=True, padx=8, pady=4)
        editor.insert("1.0", content)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(0, 8))

        def save():
            text = editor.get("1.0", "end-1c")
            threading.Thread(target=self._do_prefs_save, args=(fn, text, win),
                             daemon=True).start()

        ttk.Button(bar, text="Save to device", command=save).pack(side="right")
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right", padx=6)

    def _do_prefs_save(self, fn, text, win):
        base = self._prefs_base()
        host = self.frida_host.get().strip()
        remote_tmp = "/data/local/tmp/_pref_edit.xml"
        try:
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False,
                                             encoding="utf-8", newline="\n")
            tf.write(text)
            tf.close()
        except Exception as e:
            self.write(f"[ERROR] temp write failed: {e}\n")
            return

        r = Runner(self.log_queue)
        self.write(f"\n=== Saving {fn} to device ===\n")
        push = [TOOLS.adb] + (["-s", host] if host else []) + ["push", tf.name, remote_tmp]
        if r.run(push) != 0:
            self.write("✗ push failed.\n")
            try:
                os.remove(tf.name)
            except Exception:
                pass
            return
        # cat > target preserves the existing file's owner/permissions
        rc, out = self._capture(self._prefs_shell(f"cat {remote_tmp} > {base}/{fn}"))
        self._capture(self._prefs_shell(f"rm -f {remote_tmp}"))
        try:
            os.remove(tf.name)
        except Exception:
            pass
        if rc == 0:
            self.write("✔ Saved. Restart the app to load the changes.\n")
            self.set_status("Pref saved")
            self.after(0, win.destroy)
        else:
            self.write(f"✗ Save failed: {out.strip()}\nTry ticking 'Use root via su'.\n")
            self.set_status("Pref save failed")

    def prefs_delete(self):
        if not self._prefs_guard():
            return
        fn = self._selected_pref()
        if not fn:
            messagebox.showinfo("No selection", "Select a pref file.")
            return
        if not messagebox.askyesno("Delete", f"Delete '{fn}' from the device?"):
            return
        threading.Thread(target=self._do_prefs_delete, args=(fn,), daemon=True).start()

    def _do_prefs_delete(self, fn):
        rc, out = self._capture(self._prefs_shell(f"rm -f {self._prefs_base()}/{fn}"))
        self.write(f"\n[deleted {fn}] {out.strip()}\n")
        self._do_prefs_list()

    # -- frida script runner -------------------------------------------------
    def frida_run(self):
        if self.fs_proc is not None:
            messagebox.showinfo("Running", "A script is already running. Stop it first.")
            return
        if not TOOLS.frida:
            messagebox.showerror("Error", "frida CLI not found. Install: pip install frida-tools")
            return
        script = self.fs_script.get().strip()
        target = self.fs_target.get().strip()
        mode = self.fs_mode.get()
        # A main script is optional — preloaded helpers may be all you need.
        if script and not os.path.isfile(script):
            messagebox.showerror("Error", "The selected script path is not a valid file.")
            return
        if not target:
            messagebox.showerror("Error", "Please enter a target (package / process name / pid).")
            return

        cmd = [TOOLS.frida, "-U"]
        if mode == "spawn":
            cmd += ["-f", target]
        elif mode == "name":
            cmd += ["-n", target]
        else:  # pid
            cmd += ["-p", target]

        self._fs_temps = []   # temp scripts to clean up when the session ends

        # Preload helper scripts first (multiple -l), then the chosen script.
        # Always-on: any *bypass*.js plus current-screen.js.
        preloaded = []
        if self.fs_preload.get():
            for name in list_scripts():
                nl = name.lower()
                if "bypass" in nl or nl == "current-screen.js":
                    p = os.path.join(SCRIPTS_DIR, name)
                    if not script or os.path.abspath(p) != os.path.abspath(script):
                        cmd += ["-l", p]
                        preloaded.append(name)
        if preloaded:
            self.write(f"\n[preloading: {', '.join(preloaded)}]\n")

        # Pref spoof: inject typed rules into a temp copy of pref-spoof.js
        spoof_added = False
        if self.fs_spoof_on.get():
            rules = self._spoof_rules()
            if rules:
                spoof_src = os.path.join(SCRIPTS_DIR, "pref-spoof.js")
                if not os.path.isfile(spoof_src):
                    seed_missing_scripts()
                try:
                    with open(spoof_src, "r", encoding="utf-8") as fh:
                        body = fh.read()
                    tf = tempfile.NamedTemporaryFile(
                        mode="w", suffix="_prefspoof.js", delete=False,
                        encoding="utf-8", newline="\n")
                    tf.write(f"var OVERRIDES = {json.dumps(rules)};\n" + body)
                    tf.close()
                    self._fs_temps.append(tf.name)
                    cmd += ["-l", tf.name]
                    spoof_added = True
                    self.write(f"[pref-spoof: {len(rules)} rule(s)]\n")
                except Exception as e:
                    self.write(f"[WARN] pref-spoof failed: {e}\n")

        # Need at least one script to load
        if not script and not preloaded and not spoof_added:
            messagebox.showinfo(
                "Nothing to run",
                "Nothing selected to run.\n"
                "Pick a script, enable 'Auto-load helpers', or enable 'Spoof prefs'.")
            return

        # Add the chosen main script, injecting the "Script arg" if provided.
        if script:
            arg = self.fs_arg.get().strip()
            main_script = script
            if arg:
                try:
                    with open(script, "r", encoding="utf-8") as fh:
                        body = fh.read()
                    tf = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".js", delete=False, encoding="utf-8", newline="\n")
                    tf.write(f"var ARG = {json.dumps(arg)};\n" + body)
                    tf.close()
                    self._fs_temps.append(tf.name)
                    main_script = tf.name
                    self.write(f'[arg: ARG = "{arg}"]\n')
                except Exception as e:
                    self.write(f"[WARN] could not apply arg, running without it: {e}\n")
            cmd += ["-l", main_script]
        elif not preloaded and not spoof_added:
            self.write("[no scripts to run]\n")

        # -q -t inf: no interactive prompt, stay attached/streaming until Stop
        cmd += ["-q", "-t", "inf"]

        threading.Thread(target=self._do_frida_run, args=(cmd,), daemon=True).start()

    def _do_frida_run(self, cmd):
        printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self.write(f"\n=== Running Frida script ===\n$ {printable}\n")
        try:
            self.fs_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            self.write(f"[ERROR] Could not start frida: {e}\n")
            self.fs_proc = None
            return

        # toggle button states on the UI thread
        self.after(0, lambda: self.fs_run_btn.config(state="disabled"))
        self.after(0, lambda: self.fs_stop_btn.config(state="normal"))
        self.set_status("Frida script running…")

        for line in self.fs_proc.stdout:
            self.write(line)
        self.fs_proc.wait()
        code = self.fs_proc.returncode
        self.write(f"\n[script session ended, exit code: {code}]\n")
        self.fs_proc = None
        # clean up any temp (arg/spoof-injected) scripts
        for tmp in getattr(self, "_fs_temps", []):
            try:
                os.remove(tmp)
            except Exception:
                pass
        self._fs_temps = []
        self.after(0, lambda: self.fs_run_btn.config(state="normal"))
        self.after(0, lambda: self.fs_stop_btn.config(state="disabled"))
        self.set_status("Frida script stopped")

    def frida_run_stop(self):
        proc = self.fs_proc
        if proc is None:
            return
        self.write("\n[stopping frida script…]\n")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        except Exception as e:
            self.write(f"[ERROR] stop failed: {e}\n")


if __name__ == "__main__":
    App().mainloop()
