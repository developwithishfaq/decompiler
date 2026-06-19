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
import copy
import time
import shutil
import tempfile
import threading
import subprocess
import configparser
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
# Editable project settings (settings.ini)
#
# Everything a user might reasonably want to tune — window size, the default
# adb host, the executable names we look for, exact tool paths, and any extra
# folders to search — lives in settings.ini next to this script. It's a plain
# INI file, committed to the repo, and friendly to hand-edit. Crucially, you
# can paste a Windows path exactly as-is (no backslash escaping):
#
#     [tools.path]
#     adb = D:\Program Files\Nox\bin\adb.exe
#
# Any missing/invalid key silently falls back to the built-in defaults below,
# so you can delete the file (it regenerates) or keep only what you change.
# ---------------------------------------------------------------------------

SETTINGS_PATH = os.path.join(APP_DIR, "settings.ini")

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
        # Which adb to auto-pick first: "emulator" (Nox/BlueStacks/…) or "sdk".
        "prefer": "emulator",
    },
    "keystore": {
        # If exactly these patterns match a file next to the app, prefill it.
        "auto_detect_globs": ["*.keystore", "*.jks"],
    },
    # Per tool:
    #   "path"  - the exact full path to the binary; pin a tool explicitly.
    #             Leave empty to auto-detect. An explicit path always wins.
    #   "names" - the executable filenames to look for on PATH / search dirs
    #             when "path" is empty.
    "tools": {
        "apktool":   {"path": "", "names": ["apktool.bat", "apktool", "apktool.jar"]},
        "zipalign":  {"path": "", "names": ["zipalign.exe", "zipalign"]},
        "apksigner": {"path": "", "names": ["apksigner.bat", "apksigner", "apksigner.jar"]},
        "adb":       {"path": "", "names": ["adb.exe", "nox_adb.exe", "adb"]},
        "java":      {"path": "", "names": ["java.exe", "java"]},
        "apkeditor": {"path": "", "names": ["APKEditor.jar", "apkeditor.jar"]},
        "frida_ps":  {"path": "", "names": ["frida-ps.exe", "frida-ps"]},
        "frida":     {"path": "", "names": ["frida.exe", "frida"]},
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

# Written verbatim if settings.ini is missing, so users get a documented file.
SETTINGS_TEMPLATE = r"""# ===========================================================================
# APK Tool GUI - settings
#
# Plain text. Edit freely; lines starting with #  are comments.
# Paste Windows paths EXACTLY as they are - no escaping, no doubling slashes:
#       adb = D:\Program Files\Nox\bin\adb.exe
# Leave a value blank to fall back to automatic detection.
# Lists may be comma-separated or one item per line.
# Delete this file to regenerate it with defaults.
# ===========================================================================

[ui]
title = APK Tool GUI
geometry = 820x480
min_width = 700
min_height = 380

[adb]
# Prefilled ADB device (host:port) and the on-device frida-server path.
default_host = 127.0.0.1:62001
frida_remote = /data/local/tmp/frida-server
# Which adb to auto-pick first when several exist:
#   emulator = an emulator's own adb (Nox / BlueStacks / LDPlayer / ...)   <- default
#   sdk      = the Android SDK / PATH copy
prefer = emulator

[keystore]
# Patterns used to auto-fill the keystore field from files next to the app.
auto_detect_globs = *.keystore, *.jks

# ---------------------------------------------------------------------------
# Pin a tool to an EXACT binary. Paste the full path as-is. Example:
#       adb = D:\Program Files\Nox\bin\adb.exe
# Leave blank to auto-detect (PATH -> Android SDK -> emulator installs).
# ---------------------------------------------------------------------------
[tools.path]
apktool =
zipalign =
apksigner =
adb =
java =
apkeditor =
frida_ps =
frida =

# ---------------------------------------------------------------------------
# Executable filenames to look for when the path above is blank.
# ---------------------------------------------------------------------------
[tools.names]
apktool = apktool.bat, apktool, apktool.jar
zipalign = zipalign.exe, zipalign
apksigner = apksigner.bat, apksigner, apksigner.jar
adb = adb.exe, nox_adb.exe, adb
java = java.exe, java
apkeditor = APKEditor.jar, apkeditor.jar
frida_ps = frida-ps.exe, frida-ps
frida = frida.exe, frida

# ---------------------------------------------------------------------------
# Extra folders to search, on top of auto-detection.
# One folder per line or comma-separated. Paste as-is.
# ---------------------------------------------------------------------------
[search_paths]
android_sdk_roots =
build_tools_dirs =
platform_tools_dirs =
emulator_dirs =
extra_tool_dirs =
"""


def _split_list(raw):
    """Parse an INI value into a list: split on newlines and commas, trim."""
    if not raw:
        return []
    items = []
    for chunk in raw.replace(",", "\n").splitlines():
        s = chunk.strip()
        if s:
            items.append(s)
    return items


def _load_settings():
    """Built-in defaults overlaid with settings.ini (creating it if absent)."""
    settings = copy.deepcopy(DEFAULT_SETTINGS)

    if not os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                fh.write(SETTINGS_TEMPLATE)
        except Exception:
            pass
        return settings

    # interpolation=None so literal '%' in paths is never treated specially.
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # keep key case as written
    try:
        cp.read(SETTINGS_PATH, encoding="utf-8")
    except Exception:
        return settings  # malformed file -> just use defaults

    def gets(section, key):
        return cp.get(section, key).strip() if cp.has_option(section, key) else None

    # [ui]
    for k in ("title", "geometry"):
        v = gets("ui", k)
        if v:
            settings["ui"][k] = v
    for k in ("min_width", "min_height"):
        if cp.has_option("ui", k):
            try:
                settings["ui"][k] = cp.getint("ui", k)
            except Exception:
                pass

    # [adb]
    for k in ("default_host", "frida_remote", "prefer"):
        v = gets("adb", k)
        if v:
            settings["adb"][k] = v

    # [keystore]
    if cp.has_option("keystore", "auto_detect_globs"):
        globs = _split_list(cp.get("keystore", "auto_detect_globs"))
        if globs:
            settings["keystore"]["auto_detect_globs"] = globs

    # [tools.path] / [tools.names]
    for tool in settings["tools"]:
        p = gets("tools.path", tool)
        if p is not None:
            settings["tools"][tool]["path"] = p
        if cp.has_option("tools.names", tool):
            names = _split_list(cp.get("tools.names", tool))
            if names:
                settings["tools"][tool]["names"] = names

    # [search_paths]
    for k in settings["search_paths"]:
        if cp.has_option("search_paths", k):
            settings["search_paths"][k] = _split_list(cp.get("search_paths", k))

    return settings


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
# Each looks for the executable names from settings.ini on PATH first, then
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
    """Find adb. By default an emulator's own adb (Nox / BlueStacks / …) is
    preferred over the Android SDK / PATH copy, because its version matches the
    emulator and avoids 'adb server version' conflicts. Set [adb] prefer = sdk
    in settings.ini to flip the order."""
    names = _tool_names("adb")
    from_emulator = lambda: _find_in_dirs(_emulator_dirs(), names)
    from_path = lambda: _which(names)
    from_sdk = lambda: _find_in_dirs(_platform_tools_dirs(), names)

    prefer = str(SETTINGS["adb"].get("prefer", "emulator")).lower()
    if prefer.startswith("emu"):
        order = (from_emulator, from_path, from_sdk)
    else:
        order = (from_path, from_sdk, from_emulator)

    for finder in order:
        found = finder()
        if found:
            return found
    return None


def _java_dirs():
    """Candidate dirs holding a java launcher (JDK/JRE), best-effort."""
    dirs = []
    for env in ("JAVA_HOME", "JRE_HOME"):
        v = os.environ.get(env)
        if v:
            dirs.append(os.path.join(v, "bin"))
    # Android Studio bundles a JDK (jbr); present on most machines that have apktool.
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(os.path.join(base, "Android", "Android Studio", "jbr", "bin"))
            dirs.append(os.path.join(base, "Android", "Android Studio", "jre", "bin"))
    dirs += [os.path.join(d, "bin") for d in _android_sdk_roots()]
    dirs += SETTINGS["search_paths"]["extra_tool_dirs"]
    return _dedup_existing_dirs(dirs)


def _find_java():
    names = _tool_names("java") or ["java.exe", "java"]
    return _which(names) or _find_in_dirs(_java_dirs(), names)


def _find_apkeditor():
    """APKEditor.jar — used to merge split APKs into one standalone APK.
    Looked for first in this app's tools/ folder, then next to the app."""
    names = _tool_names("apkeditor") or ["APKEditor.jar", "apkeditor.jar"]
    dirs = [os.path.join(APP_DIR, "tools"), APP_DIR] + _build_tools_dirs()
    return _find_in_dirs(dirs, names) or _which(names)


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
    ToolSpec("java", "java", _find_java, False,
             "Java runtime (JDK/JRE) — needed only to merge split APKs. "
             "Bundled with Android Studio (jbr), or install a JDK and add it "
             "to PATH / JAVA_HOME, then re-detect."),
    ToolSpec("apkeditor", "APKEditor", _find_apkeditor, False,
             "Merges split APKs into one standalone APK before decompiling so "
             "native libs (.so) and split resources are included. Put "
             "APKEditor.jar in this app's tools/ folder; get it from "
             "https://github.com/REAndroid/APKEditor/releases."),
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
        """Resolve one tool, most explicit source first:
        1. an exact "path" pinned in settings.ini
        2. a path picked earlier via the ⚙ Tools dialog (apk_tool_gui.tools.json)
        3. auto-detection (PATH / Android SDK / emulator installs)
        """
        sp = (SETTINGS["tools"].get(key, {}).get("path") or "").strip()
        if sp and os.path.isfile(sp):
            self.paths[key] = sp
            return sp

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
    def java(self):
        return self.paths.get("java")

    @property
    def apkeditor(self):
        return self.paths.get("apkeditor")

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
    "disable-ads.js": r"""// disable-ads.js — block common ad SDKs (for testing your own apps' ad-free /
// premium flows). Best effort across the major networks; each hook is guarded,
// so missing SDKs are simply skipped.
//
// Approach: no-op the load/show methods and make "is ready" checks return false.
// Returns a type-correct default so it won't crash non-void methods.

Java.perform(function () {
    function block(className, methodName) {
        try {
            var C = Java.use(className);
            if (!C[methodName]) return;
            C[methodName].overloads.forEach(function (ov) {
                var rt = ov.returnType.className;
                ov.implementation = function () {
                    console.log('[ads] blocked ' + className.split('.').pop() + '.' + methodName);
                    switch (rt) {
                        case 'void':    return;
                        case 'boolean': return false;
                        case 'int': case 'long': case 'short': case 'byte': return 0;
                        case 'float': case 'double': return 0;
                        default:        return null;
                    }
                };
            });
        } catch (e) {}
    }

    // Google Mobile Ads / AdMob
    block('com.google.android.gms.ads.BaseAdView', 'loadAd');
    block('com.google.android.gms.ads.interstitial.InterstitialAd', 'load');
    block('com.google.android.gms.ads.interstitial.InterstitialAd', 'show');
    block('com.google.android.gms.ads.rewarded.RewardedAd', 'load');
    block('com.google.android.gms.ads.rewarded.RewardedAd', 'show');
    block('com.google.android.gms.ads.rewardedinterstitial.RewardedInterstitialAd', 'load');
    block('com.google.android.gms.ads.rewardedinterstitial.RewardedInterstitialAd', 'show');
    block('com.google.android.gms.ads.appopen.AppOpenAd', 'load');
    block('com.google.android.gms.ads.appopen.AppOpenAd', 'show');

    // AppLovin MAX
    block('com.applovin.mediation.ads.MaxAdView', 'loadAd');
    block('com.applovin.mediation.ads.MaxInterstitialAd', 'loadAd');
    block('com.applovin.mediation.ads.MaxInterstitialAd', 'showAd');
    block('com.applovin.mediation.ads.MaxInterstitialAd', 'isReady');
    block('com.applovin.mediation.ads.MaxRewardedAd', 'loadAd');
    block('com.applovin.mediation.ads.MaxRewardedAd', 'showAd');
    block('com.applovin.mediation.ads.MaxRewardedAd', 'isReady');
    block('com.applovin.mediation.ads.MaxAppOpenAd', 'loadAd');
    block('com.applovin.mediation.ads.MaxAppOpenAd', 'showAd');

    // Unity Ads
    block('com.unity3d.ads.UnityAds', 'load');
    block('com.unity3d.ads.UnityAds', 'show');
    block('com.unity3d.ads.UnityAds', 'isReady');

    // IronSource / LevelPlay
    block('com.ironsource.mediationsdk.IronSource', 'loadInterstitial');
    block('com.ironsource.mediationsdk.IronSource', 'showInterstitial');
    block('com.ironsource.mediationsdk.IronSource', 'showRewardedVideo');
    block('com.ironsource.mediationsdk.IronSource', 'loadBanner');
    block('com.ironsource.mediationsdk.IronSource', 'isInterstitialReady');
    block('com.ironsource.mediationsdk.IronSource', 'isRewardedVideoAvailable');

    // Meta / Facebook Audience Network
    block('com.facebook.ads.InterstitialAd', 'loadAd');
    block('com.facebook.ads.InterstitialAd', 'show');
    block('com.facebook.ads.RewardedVideoAd', 'loadAd');
    block('com.facebook.ads.RewardedVideoAd', 'show');
    block('com.facebook.ads.AdView', 'loadAd');

    // Vungle / Liftoff
    block('com.vungle.warren.Vungle', 'loadAd');
    block('com.vungle.warren.Vungle', 'playAd');
    block('com.vungle.warren.Vungle', 'canPlayAd');

    // AdColony
    block('com.adcolony.sdk.AdColony', 'requestInterstitial');
    block('com.adcolony.sdk.AdColony', 'requestAdView');

    // Chartboost
    block('com.chartboost.sdk.Chartboost', 'showInterstitial');
    block('com.chartboost.sdk.Chartboost', 'cacheInterstitial');
    block('com.chartboost.sdk.Chartboost', 'showRewardedVideo');

    console.log('[ads] ad-blocking hooks installed. Watch for "[ads] blocked ..." lines; ' +
                'if none appear, the app may use a network not covered here (run ' +
                'list-classes.js with the SDK package to identify it).');
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
        self.logcat_proc = None  # running adb logcat (Popen) or None
        self.logcat_queue = queue.Queue()

        self._build_widgets()
        self._init_config()
        self._poll_log()
        self._poll_logcat()
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
        if self.logcat_proc is not None:
            try:
                self.logcat_proc.kill()
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
            "prefs_thirdparty": self.prefs_thirdparty,
            # pull apk
            "pull_outdir": self.pull_outdir,
            "pull_thirdparty": self.pull_thirdparty,
            # logs
            "logcat_tag": self.logcat_tag,
            "logcat_prio": self.logcat_prio,
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
        self.tab_pull = ttk.Frame(nb)
        self.tab_logs = ttk.Frame(nb)
        nb.add(self.tab_dec, text="  Decompile  ")
        nb.add(self.tab_rec, text="  Recompile + Sign  ")
        nb.add(self.tab_frida, text="  Frida / ADB  ")
        nb.add(self.tab_scripts, text="  Scripts  ")
        nb.add(self.tab_fscript, text="  Frida Script  ")
        nb.add(self.tab_prefs, text="  Prefs  ")
        nb.add(self.tab_pull, text="  Pull APK  ")
        nb.add(self.tab_logs, text="  Logs  ")

        self._build_decompile_tab()
        self._build_recompile_tab()
        self._build_frida_tab()
        self._build_scripts_tab()
        self._build_frida_script_tab()
        self._build_prefs_tab()
        self._build_pull_tab()
        self._build_logs_tab()

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

        self.dec_merge = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f, text="Merge split APKs first  (include .so native libs + split "
                    "resources — needed for Play / bundle apps)",
            variable=self.dec_merge).grid(row=4, column=1, sticky="w", pady=3)

        ttk.Label(
            f, foreground="#888", wraplength=560, justify="left",
            text="Pick base.apk (with its split_*.apk siblings in the same "
                 "folder) or an .xapk/.apkm/.apks bundle, and they're merged "
                 "into one complete APK before decompiling."
        ).grid(row=5, column=1, sticky="w", padx=2)

        ttk.Button(f, text="Decompile", command=self.start_decompile).grid(
            row=6, column=1, sticky="e", pady=6, padx=4)

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

        # Package picker (same pattern as the Pull APK screen)
        self.prefs_thirdparty = tk.BooleanVar(value=True)
        pkgctrl = ttk.Frame(f)
        pkgctrl.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 2))
        pkgctrl.columnconfigure(1, weight=1)
        ttk.Label(pkgctrl, text="Filter:").grid(row=0, column=0, sticky="w")
        self.prefs_filter = tk.StringVar()
        self.prefs_filter.trace_add("write", lambda *_: self._apply_prefs_pkg_filter())
        ttk.Entry(pkgctrl, textvariable=self.prefs_filter, width=24).grid(
            row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Checkbutton(pkgctrl, text="3rd-party only",
                        variable=self.prefs_thirdparty).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(pkgctrl, text="↻ Refresh list",
                   command=self.prefs_refresh).grid(row=0, column=3)

        pkgwrap = ttk.Frame(f)
        pkgwrap.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=8, pady=2)
        f.rowconfigure(3, weight=1)
        pkgwrap.columnconfigure(0, weight=1)
        pkgwrap.rowconfigure(0, weight=1)
        self.prefs_pkg_list = tk.Listbox(pkgwrap, height=5, activestyle="dotbox")
        self.prefs_pkg_list.grid(row=0, column=0, sticky="nsew")
        pkgsb = ttk.Scrollbar(pkgwrap, command=self.prefs_pkg_list.yview)
        pkgsb.grid(row=0, column=1, sticky="ns")
        self.prefs_pkg_list.config(yscrollcommand=pkgsb.set)
        self._prefs_all_packages = []   # full unfiltered list
        # Single-click fills the Package field; double-click also lists its prefs.
        self.prefs_pkg_list.bind("<<ListboxSelect>>", self._on_prefs_pkg_select)
        self.prefs_pkg_list.bind("<Double-Button-1>", self._on_prefs_pkg_activate)

        listwrap = ttk.Frame(f)
        listwrap.grid(row=4, column=0, sticky="nsew", padx=(8, 4), pady=2)
        f.rowconfigure(4, weight=1)
        self.prefs_list = tk.Listbox(listwrap, height=7, activestyle="dotbox")
        self.prefs_list.pack(side="left", fill="both", expand=True)
        plb = ttk.Scrollbar(listwrap, command=self.prefs_list.yview)
        plb.pack(side="right", fill="y")
        self.prefs_list.config(yscrollcommand=plb.set)
        self.prefs_list.bind("<Double-Button-1>", lambda e: self.prefs_edit())

        side = ttk.Frame(f)
        side.grid(row=4, column=1, sticky="n", padx=(4, 8), pady=2)
        for txt, cmd in [
            ("List files", self.prefs_listfiles),
            ("View / Edit", self.prefs_edit),
            ("Delete", self.prefs_delete),
        ]:
            ttk.Button(side, text=txt, width=12, command=cmd).pack(fill="x", pady=2)

        hint = ("Refresh to list installed packages, click one to fill Package "
                "(double-click also lists its prefs). Reads "
                "/data/data/<pkg>/shared_prefs/*.xml over adb (no Frida). Edit while "
                "the app is closed so it doesn't overwrite your changes, then "
                "restart the app to load them.")
        ttk.Label(f, text=hint, foreground="#888", wraplength=760, justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 4))

    # -- file pickers --------------------------------------------------------
    def _pick_apk_dec(self):
        p = filedialog.askopenfilename(
            title="Select APK or split bundle",
            filetypes=[("APK / bundles", "*.apk *.xapk *.apkm *.apks *.apkx"),
                       ("APK files", "*.apk"),
                       ("Split bundles", "*.xapk *.apkm *.apks *.apkx"),
                       ("All", "*.*")],
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

    # -- split-APK merging ---------------------------------------------------
    # An app installed from Play as an "App Bundle" is split across several
    # APKs: base.apk (code + most resources) plus config splits that carry the
    # native libraries (lib/<abi>/*.so), per-density drawables and per-language
    # strings. Decompiling base.apk alone therefore loses the .so files and the
    # split resources, so the rebuilt app crashes with "libXxx.so not found".
    # To get a COMPLETE decompile we first merge all splits into one standalone
    # ("universal") APK with APKEditor, then hand that to apktool.
    BUNDLE_EXTS = (".xapk", ".apkm", ".apks", ".apkx")

    def _can_merge(self):
        return bool(TOOLS.apkeditor and (TOOLS.java or shutil.which("java")))

    def _jar_cmd(self, jar, args):
        """Run a .jar via java (uses the resolved java, else whatever's on PATH)."""
        java = TOOLS.java or "java"
        return [java, "-jar", jar] + list(args)

    @staticmethod
    def _is_split_name(fname):
        f = fname.lower()
        return (f.startswith("split") or f.startswith("config.")
                or ".config." in f)

    def _sibling_split_set(self, apk):
        """If `apk` sits next to split_*.apk / config*.apk siblings (i.e. it's the
        base of a split set spilled into one folder), return the full list of
        APKs to merge; otherwise None."""
        d = os.path.dirname(os.path.abspath(apk))
        try:
            entries = [e for e in os.listdir(d) if e.lower().endswith(".apk")]
        except OSError:
            return None
        splits = [e for e in entries if self._is_split_name(e)]
        if not splits:
            return None
        bases = [e for e in entries if e.lower() == "base.apk"]
        picked = os.path.basename(apk)
        wanted = sorted(set(bases) | set(splits) | {picked})
        full = [os.path.join(d, e) for e in wanted
                if os.path.isfile(os.path.join(d, e))]
        return full if len(full) > 1 else None

    def _merge_dir(self, r, in_path, out_apk):
        """Merge a directory (or .xapk/.apkm/.apks bundle) of splits into one
        standalone APK via APKEditor. Returns out_apk on success, else None."""
        if os.path.exists(out_apk):
            try:
                os.remove(out_apk)
            except OSError:
                pass
        self.write("\n--- Merging split APKs → one standalone APK (APKEditor) ---\n")
        cmd = self._jar_cmd(TOOLS.apkeditor, ["m", "-i", in_path, "-o", out_apk, "-f"])
        if r.run(cmd) == 0 and os.path.isfile(out_apk):
            self.write(f"\n✔ Merged standalone APK: {out_apk}\n")
            return out_apk
        self.write("\n✗ Split merge FAILED — falling back to the base APK only.\n")
        return None

    def _resolve_decompile_source(self, r, apk, work_dir):
        """Decide what to actually feed apktool. For a split-APK app (a bundle
        file, or a base.apk with sibling splits) merge first so the decompile is
        complete. For an ordinary standalone APK, return it unchanged."""
        ext = os.path.splitext(apk)[1].lower()
        is_bundle = ext in self.BUNDLE_EXTS
        split_set = None if is_bundle else self._sibling_split_set(apk)

        if not is_bundle and not split_set:
            return apk   # ordinary standalone APK — nothing to merge

        if not self._can_merge():
            self.write(
                "\n[!] This is a split-APK app, but APKEditor/Java were not found, "
                "so only the base APK can be decompiled — native libs (.so) and "
                "split resources will be MISSING and the rebuilt app may crash.\n"
                "    Put APKEditor.jar in this app's tools/ folder (⚙ Tools) to "
                "enable automatic merging.\n")
            return apk

        stem = os.path.splitext(os.path.basename(apk))[0] or "app"
        out_apk = os.path.join(work_dir, stem + "_universal.apk")

        if is_bundle:
            return self._merge_dir(r, apk, out_apk) or apk

        # base.apk + sibling splits: stage them in a temp dir so APKEditor only
        # sees this app's parts (the source folder may hold unrelated APKs).
        tmp = tempfile.mkdtemp(prefix="apkmerge_")
        try:
            for f in split_set:
                shutil.copy2(f, os.path.join(tmp, os.path.basename(f)))
            return self._merge_dir(r, tmp, out_apk) or apk
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _report_lib_status(self, dec_dir):
        """After a decompile, summarise the native libs that made it in."""
        libdir = os.path.join(dec_dir, "lib")
        sos, abis = [], []
        if os.path.isdir(libdir):
            abis = sorted(d for d in os.listdir(libdir)
                          if os.path.isdir(os.path.join(libdir, d)))
            for root, _dirs, files in os.walk(libdir):
                sos += [f for f in files if f.endswith(".so")]
        if sos:
            self.write(f"   ✔ Native libs included: {len(sos)} .so "
                       f"({', '.join(abis)})\n")
        else:
            self.write("   ℹ No native .so libraries in this APK "
                       "(fine if the app has none).\n")

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

        source = apk
        if self.dec_merge.get():
            work_dir = os.path.dirname(os.path.abspath(out)) or APP_DIR
            source = self._resolve_decompile_source(r, apk, work_dir)

        cmd = [TOOLS.apktool, "d"]
        if self.dec_force.get():
            cmd.append("-f")
        cmd += [source, "-o", out]
        code = r.run(cmd)
        if code == 0:
            self.write(f"\n✔ Decompiled to: {out}\n")
            self._report_lib_status(out)
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

        # signing succeeded — the final signed APK is all we need; drop the
        # intermediate build artifacts so the output folder isn't cluttered.
        for tmp in (unsigned, aligned):
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
                    self.write(f"Cleaned up intermediate: {os.path.basename(tmp)}\n")
            except OSError as e:
                self.write(f"(could not remove {os.path.basename(tmp)}: {e})\n")

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

    # -- package picker ------------------------------------------------------
    def prefs_refresh(self):
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found. Click '⚙ Tools' to locate it.")
            return
        threading.Thread(target=self._do_prefs_pkg_refresh, daemon=True).start()

    def _do_prefs_pkg_refresh(self):
        self.write("\n--- Listing device packages ---\n")
        host = self.frida_host.get().strip()
        cmd = [TOOLS.adb] + (["-s", host] if host else []) + \
              ["shell", "pm", "list", "packages"]
        if self.prefs_thirdparty.get():
            cmd.append("-3")
        rc, out = self._capture(cmd)
        packages = sorted(
            line.strip()[len("package:"):] for line in out.splitlines()
            if line.strip().startswith("package:")
        )
        self.write(f"Found {len(packages)} package(s).\n")
        self._prefs_all_packages = packages
        self.after(0, self._apply_prefs_pkg_filter)

    def _apply_prefs_pkg_filter(self):
        filt = self.prefs_filter.get().strip().lower()
        shown = [p for p in self._prefs_all_packages if filt in p.lower()] \
                if filt else list(self._prefs_all_packages)
        self.prefs_pkg_list.delete(0, "end")
        for p in shown:
            self.prefs_pkg_list.insert("end", p)

    def _on_prefs_pkg_select(self, _event=None):
        sel = self.prefs_pkg_list.curselection()
        if sel:
            self.prefs_pkg.set(self.prefs_pkg_list.get(sel[0]))

    def _on_prefs_pkg_activate(self, _event=None):
        self._on_prefs_pkg_select()
        if self.prefs_pkg.get().strip():
            self.prefs_listfiles()

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

    # -- pull apk tab --------------------------------------------------------
    def _build_pull_tab(self):
        f = self.tab_pull
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        self.pull_outdir = tk.StringVar(value=APP_DIR)
        self.pull_thirdparty = tk.BooleanVar(value=True)
        self.pull_merge = tk.BooleanVar(value=True)

        # Top controls
        ctrl = ttk.Frame(f)
        ctrl.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        ctrl.columnconfigure(1, weight=1)

        ttk.Label(ctrl, text="Filter:").grid(row=0, column=0, sticky="w")
        self.pull_filter = tk.StringVar()
        self.pull_filter.trace_add("write", lambda *_: self._apply_pull_filter())
        ttk.Entry(ctrl, textvariable=self.pull_filter, width=24).grid(
            row=0, column=1, sticky="ew", padx=(4, 8))

        ttk.Checkbutton(ctrl, text="3rd-party only",
                        variable=self.pull_thirdparty).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(ctrl, text="↻ Refresh list",
                   command=self.pull_refresh).grid(row=0, column=3)

        # Package list
        listwrap = ttk.Frame(f)
        listwrap.grid(row=1, column=0, sticky="nsew", padx=8, pady=2)
        listwrap.columnconfigure(0, weight=1)
        listwrap.rowconfigure(0, weight=1)

        self.pull_list = tk.Listbox(listwrap, height=5, activestyle="dotbox",
                                    selectmode="extended")
        self.pull_list.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(listwrap, command=self.pull_list.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.pull_list.config(yscrollcommand=vsb.set)
        self._pull_all_packages = []   # full unfiltered list

        # Output dir row
        outrow = ttk.Frame(f)
        outrow.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 2))
        outrow.columnconfigure(1, weight=1)
        ttk.Label(outrow, text="Save to:").grid(row=0, column=0, sticky="w")
        ttk.Entry(outrow, textvariable=self.pull_outdir).grid(
            row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(outrow, text="Browse…",
                   command=self._pick_pull_outdir).grid(row=0, column=2)

        # Action buttons
        btnrow = ttk.Frame(f)
        btnrow.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 2))
        ttk.Button(btnrow, text="Pull APK(s)",
                   command=self.pull_apks).pack(side="left", padx=(0, 6))
        ttk.Button(btnrow, text="Pull + Decompile",
                   command=self.pull_and_decompile).pack(side="left")
        ttk.Checkbutton(btnrow, text="Merge splits into one APK",
                        variable=self.pull_merge).pack(side="left", padx=(12, 0))
        ttk.Label(btnrow, text="Ctrl/Shift-click to select multiple",
                  foreground="#888").pack(side="right")

        # Split-APK hint
        hint = (
            "Pull grabs ALL of an app's APKs (base.apk + every split) into "
            "<save-to>/<package>/.  Play-installed games are split bundles: the "
            "native libraries (libgame.so, etc.) and per-density / per-language "
            "resources live in the config splits, NOT in base.apk.  With \"Merge "
            "splits\" on, they're combined into one complete <package>_universal.apk "
            "(via APKEditor) before decompiling — so the .so files and split "
            "resources are all there and the rebuilt APK won't crash with "
            "\"libXxx.so not found\".  Merging needs APKEditor.jar (in tools/) + Java."
        )
        ttk.Label(f, text=hint, foreground="#888", wraplength=780,
                  justify="left").grid(row=4, column=0, sticky="w",
                                       padx=8, pady=(2, 6))

    def _pick_pull_outdir(self):
        p = filedialog.askdirectory(title="Save APKs to",
                                    initialdir=self.pull_outdir.get() or APP_DIR)
        if p:
            self.pull_outdir.set(p)

    def pull_refresh(self):
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found. Click '⚙ Tools' to locate it.")
            return
        threading.Thread(target=self._do_pull_refresh, daemon=True).start()

    def _do_pull_refresh(self):
        self.write("\n--- Listing device packages ---\n")
        host = self.frida_host.get().strip()
        cmd = [TOOLS.adb] + (["-s", host] if host else []) + \
              ["shell", "pm", "list", "packages"]
        if self.pull_thirdparty.get():
            cmd.append("-3")
        rc, out = self._capture(cmd)
        packages = sorted(
            line.strip()[len("package:"):] for line in out.splitlines()
            if line.strip().startswith("package:")
        )
        self.write(f"Found {len(packages)} package(s).\n")
        self._pull_all_packages = packages
        self.after(0, self._apply_pull_filter)

    def _apply_pull_filter(self):
        filt = self.pull_filter.get().strip().lower()
        shown = [p for p in self._pull_all_packages if filt in p.lower()] \
                if filt else list(self._pull_all_packages)
        self.pull_list.delete(0, "end")
        for p in shown:
            self.pull_list.insert("end", p)

    def _selected_packages(self):
        return [self.pull_list.get(i) for i in self.pull_list.curselection()]

    def pull_apks(self):
        pkgs = self._selected_packages()
        if not pkgs:
            messagebox.showinfo("No selection", "Select one or more packages first.")
            return
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found.")
            return
        outdir = self.pull_outdir.get().strip() or APP_DIR
        threading.Thread(target=self._do_pull_apks,
                         args=(pkgs, outdir, False), daemon=True).start()

    def pull_and_decompile(self):
        pkgs = self._selected_packages()
        if not pkgs:
            messagebox.showinfo("No selection", "Select a package first.")
            return
        if len(pkgs) > 1:
            messagebox.showinfo("One at a time",
                                "Pull + Decompile works on one package at a time.")
            return
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found.")
            return
        if not TOOLS.apktool:
            messagebox.showerror("Error", "apktool not found. Click '⚙ Tools' to locate it.")
            return
        outdir = self.pull_outdir.get().strip() or APP_DIR
        threading.Thread(target=self._do_pull_apks,
                         args=(pkgs, outdir, True), daemon=True).start()

    def _do_pull_apks(self, packages, outdir, decompile_after):
        host = self.frida_host.get().strip()
        adb_base = [TOOLS.adb] + (["-s", host] if host else [])
        r = Runner(self.log_queue)

        for pkg in packages:
            self.write(f"\n=== {pkg} ===\n")
            pkg_dir = os.path.join(outdir, pkg)
            os.makedirs(pkg_dir, exist_ok=True)

            # Get all APK paths for this package (handles splits)
            rc, out = self._capture(adb_base + ["shell", "pm", "path", pkg])
            paths = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    paths.append(line[len("package:"):])

            if not paths:
                self.write(f"[WARN] No APK paths found for {pkg}. "
                           f"Is the package installed?\n")
                continue

            self.write(f"Found {len(paths)} APK file(s):\n")
            for p in paths:
                self.write(f"  {p}\n")

            # Pull each APK — keep its original filename (base.apk, split_*.apk, …)
            pulled = []
            for remote in paths:
                fname = os.path.basename(remote)
                local = os.path.join(pkg_dir, fname)
                if r.run(adb_base + ["pull", remote, local]) == 0:
                    pulled.append(local)

            if not pulled:
                self.write(f"✗ No files pulled for {pkg}.\n")
                continue

            self.write(f"\n✔ Pulled to: {pkg_dir}\n")

            bases = [p for p in pulled if os.path.basename(p) == "base.apk"]
            splits = [p for p in pulled if os.path.basename(p) != "base.apk"]

            # Merge splits into one standalone APK so the result is complete
            # (native .so libs + per-density / per-language split resources).
            source_apk = bases[0] if bases else pulled[0]
            want_merge = bool(splits) and self.pull_merge.get()
            if want_merge and not self._can_merge():
                self.write(
                    f"\n[Split APK] {len(splits)} split(s) detected, but "
                    "APKEditor/Java were not found, so they can't be merged.\n"
                    "  base.apk alone is MISSING the native libs (.so) and split "
                    "resources — a rebuilt app will likely crash.\n"
                    "  Put APKEditor.jar in this app's tools/ folder (⚙ Tools), "
                    "then pull again.\n")
            elif want_merge:
                self.write(
                    f"\n[Split APK] {len(splits)} split(s) detected — merging "
                    "into one standalone APK so nothing is missing:\n")
                for s in splits:
                    self.write(f"  {os.path.basename(s)}\n")
                universal = os.path.join(outdir, pkg + "_universal.apk")
                merged = self._merge_dir(r, pkg_dir, universal)
                if merged:
                    source_apk = merged

            # Decompile if requested
            if decompile_after:
                if not TOOLS.apktool:
                    self.write("[WARN] apktool not found — skipped decompile.\n")
                else:
                    dec_out = os.path.join(outdir, pkg + "_decompiled")
                    self.write(f"\n--- Decompiling {os.path.basename(source_apk)} ---\n")
                    cmd = [TOOLS.apktool, "d", "-f", source_apk, "-o", dec_out]
                    if r.run(cmd) == 0:
                        self.write(f"\n✔ Decompiled to: {dec_out}\n")
                        self._report_lib_status(dec_out)
                    else:
                        self.write("\n✗ Decompile failed.\n")

        self.set_status("Pull complete")

    # -- logcat tab ----------------------------------------------------------
    def _build_logs_tab(self):
        f = self.tab_logs
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        self.logcat_tag = tk.StringVar(value="")
        self.logcat_prio = tk.StringVar(value="*:V")

        # Controls row
        ctrl = ttk.Frame(f)
        ctrl.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

        ttk.Label(ctrl, text="Tag filter:").pack(side="left")
        ttk.Entry(ctrl, textvariable=self.logcat_tag, width=20).pack(side="left", padx=(4, 12))

        ttk.Label(ctrl, text="Priority:").pack(side="left")
        prio_box = ttk.Combobox(ctrl, textvariable=self.logcat_prio, state="readonly", width=8,
                                values=["*:V", "*:D", "*:I", "*:W", "*:E"])
        prio_box.pack(side="left", padx=(4, 12))

        self.logcat_attach_btn = ttk.Button(ctrl, text="▶  Attach", command=self.logcat_attach)
        self.logcat_attach_btn.pack(side="left", padx=4)
        self.logcat_detach_btn = ttk.Button(ctrl, text="■  Detach", command=self.logcat_detach,
                                            state="disabled")
        self.logcat_detach_btn.pack(side="left", padx=4)
        ttk.Button(ctrl, text="Clear", command=self._clear_logcat).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Save…", command=self._save_logcat).pack(side="left", padx=4)

        hint = ttk.Label(ctrl, text="Uses ADB device from Frida/ADB tab.", foreground="#888")
        hint.pack(side="right", padx=8)

        # Log area with its own scrollbar
        logwrap = ttk.Frame(f)
        logwrap.grid(row=1, column=0, sticky="nsew", padx=8, pady=(2, 6))
        logwrap.columnconfigure(0, weight=1)
        logwrap.rowconfigure(0, weight=1)

        self.logcat_text = tk.Text(logwrap, height=10, wrap="none", bg="#0d1117",
                                   fg="#c9d1d9", insertbackground="#c9d1d9",
                                   font=("Consolas", 9))
        self.logcat_text.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(logwrap, command=self.logcat_text.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.logcat_text.config(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(logwrap, orient="horizontal", command=self.logcat_text.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.logcat_text.config(xscrollcommand=hsb.set)

        # Colour tags for log levels
        self.logcat_text.tag_configure("V", foreground="#8b949e")
        self.logcat_text.tag_configure("D", foreground="#79c0ff")
        self.logcat_text.tag_configure("I", foreground="#56d364")
        self.logcat_text.tag_configure("W", foreground="#e3b341")
        self.logcat_text.tag_configure("E", foreground="#f85149")
        self.logcat_text.tag_configure("F", foreground="#ff7b72")

    def _poll_logcat(self):
        try:
            while True:
                line = self.logcat_queue.get_nowait()
                tag = self._logcat_level_tag(line)
                self.logcat_text.insert("end", line, tag)
                self.logcat_text.see("end")
        except queue.Empty:
            pass
        self.after(80, self._poll_logcat)

    @staticmethod
    def _logcat_level_tag(line):
        """Return a colour tag based on the log-level letter in a logcat line."""
        # Standard logcat format: "MM-DD HH:MM:SS.mmm  PID  TID LEVEL tag: msg"
        # The level character is at index 31 on fixed-width output, but we do a
        # simple scan of the first two fields for a single-letter level word.
        parts = line.split()
        for i, p in enumerate(parts):
            if len(p) == 1 and p in "VDIWEF" and i >= 2:
                return p
        return ""

    def logcat_attach(self):
        if not TOOLS.adb:
            messagebox.showerror("Error", "adb not found. Click '⚙ Tools' to locate it.")
            return
        if self.logcat_proc is not None:
            messagebox.showinfo("Already running", "Logcat is already attached. Detach first.")
            return
        threading.Thread(target=self._do_logcat_attach, daemon=True).start()

    def _do_logcat_attach(self):
        host = self.frida_host.get().strip()
        tag = self.logcat_tag.get().strip()
        prio = self.logcat_prio.get().strip() or "*:V"

        cmd = [TOOLS.adb]
        if host:
            cmd += ["-s", host]
        cmd.append("logcat")

        # Build the filter spec
        if tag:
            level = prio.split(":")[-1] if ":" in prio else "V"
            cmd += [f"{tag}:{level}", "*:S"]
        else:
            cmd.append(prio)

        printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self.logcat_queue.put(f"\n$ {printable}\n")

        try:
            self.logcat_proc = subprocess.Popen(
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
            self.logcat_queue.put(f"[ERROR] Could not start logcat: {e}\n")
            self.logcat_proc = None
            return

        self.after(0, lambda: self.logcat_attach_btn.config(state="disabled"))
        self.after(0, lambda: self.logcat_detach_btn.config(state="normal"))
        self.set_status("Logcat attached…")

        for line in self.logcat_proc.stdout:
            self.logcat_queue.put(line)

        self.logcat_proc.wait()
        code = self.logcat_proc.returncode
        self.logcat_queue.put(f"\n[logcat ended, exit code: {code}]\n")
        self.logcat_proc = None
        self.after(0, lambda: self.logcat_attach_btn.config(state="normal"))
        self.after(0, lambda: self.logcat_detach_btn.config(state="disabled"))
        self.set_status("Logcat detached")

    def logcat_detach(self):
        proc = self.logcat_proc
        if proc is None:
            return
        self.logcat_queue.put("\n[detaching logcat…]\n")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        except Exception as e:
            self.logcat_queue.put(f"[ERROR] detach failed: {e}\n")

    def _clear_logcat(self):
        try:
            while True:
                self.logcat_queue.get_nowait()
        except queue.Empty:
            pass
        self.logcat_text.delete("1.0", "end")

    def _save_logcat(self):
        content = self.logcat_text.get("1.0", "end-1c")
        if not content.strip():
            messagebox.showinfo("Empty", "No log content to save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save logcat", defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("Log files", "*.log"), ("All", "*.*")],
            initialdir=APP_DIR)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            self.set_status(f"Saved logcat: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save:\n{e}")

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
