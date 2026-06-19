# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

A single-window Tkinter app (`apk_tool_gui.py`) for Android app analysis:
decompile/recompile/sign APKs, manage a Frida server, run Frida scripts, and
inspect/spoof SharedPreferences. See `README.md` for the user-facing overview.

## STRICT RULE — Project features documentation

This rule is mandatory. Do not skip it.

1. **A `project-features.md` file MUST exist at the repo root.** It is the single
   source of truth for what the app does, screen by screen.

2. **Every screen MUST have its own dedicated markdown file** describing its use
   case and features in full — e.g. `frida_script_screen.md`,
   `decompile_screen.md`, `prefs_screen.md`, one file per screen. Name each file
   `<screen>_screen.md`.

3. **`project-features.md` MUST contain a short description of every screen** (a
   couple of sentences each) **and MUST link/reference each screen's dedicated
   `*_screen.md` file** for the full detail. It is the index; the per-screen
   files hold the depth.

4. **Whenever context about this project is needed, `project-features.md` MUST be
   read first.** It is the entry point. Follow its references into the relevant
   `*_screen.md` file(s) for deeper detail before answering or making changes.

5. **Keep the docs in sync with the code.** Whenever a screen is added, removed,
   or its features change in `apk_tool_gui.py`, update `project-features.md` AND
   the affected `*_screen.md` file in the same change. Adding a new screen
   REQUIRES creating its `*_screen.md` file and adding its short description +
   reference to `project-features.md`.

6. **EVERY SESSION MUST update the docs when code changes.** This is mandatory
   and applies to every working session — no exceptions:
   - If you change or add features to an **existing screen**, you MUST update
     that screen's `*_screen.md` file in the same session.
   - If you add a **completely new screen**, you MUST write its own separate
     `<screen>_screen.md` file AND link it from the parent `project-features.md`
     (add the short description + reference there).
   - If you remove a screen, remove its `*_screen.md` file and its entry in
     `project-features.md`.
   - A session that changes screen behaviour but leaves the docs stale is
     INCOMPLETE. Do not consider the work done until the docs match the code.

### Current screens (Notebook tabs in `apk_tool_gui.py`)

| Screen | Screen doc file |
|---|---|
| Decompile | `decompile_screen.md` |
| Recompile + Sign | `recompile_sign_screen.md` |
| Frida / ADB | `frida_adb_screen.md` |
| Scripts | `scripts_screen.md` |
| Frida Script | `frida_script_screen.md` |
| Prefs | `prefs_screen.md` |
| Pull APK | `pull_apk_screen.md` |
| Logs | `logs_screen.md` |
