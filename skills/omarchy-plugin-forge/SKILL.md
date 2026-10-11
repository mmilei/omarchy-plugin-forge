---
name: omarchy-plugin-forge
description: Build an Omarchy 4 shell plugin that lives in the bar — a bar widget plus an anchored popup panel — from a one-line idea, and keep iterating until scripts/forge-check.sh passes. Colors always come from the active theme. Use when asked to create, scaffold or fix an Omarchy bar widget, bar plugin, taskbar widget or omarchy-shell panel plugin. Not for configuring the built-in bar (that is the `omarchy` skill).
---

# omarchy-plugin-forge

Builds one third-party **bar-widget** plugin for `omarchy-shell` (Omarchy 4, Quickshell/QML): an icon or short
label in the bar that opens an anchored panel on click. Other plugin kinds (overlay, menu, service, full bar) are
out of scope.

The plugin is done when `scripts/forge-check.sh <plugin-dir>` exits 0. Not before, and not because it "looks
right".

## 0. Read the installed shell, not your memory

Omarchy 4 is young and its shell API moves between releases. Before writing QML, read what is installed:

- `/usr/share/omarchy/shell/README.md`: manifest format and plugin kinds. Full schema in
  `/usr/share/omarchy/shell/services/PluginRegistry.qml`.
- `/usr/share/omarchy/shell/plugins/panels/weather/`: the reference shape for widget + popup. Copy its structure.
- `/usr/share/omarchy/shell/Ui/` (`BarWidget`, `BarIconButton`, `Panel`, `KeyboardPanel`, `PanelKeyCatcher`,
  `PanelSectionHeader`, `PanelSeparator`, `PanelActionButton`, `ConfirmDialog`) and
  `/usr/share/omarchy/shell/Commons/` (`Color`, `Style`, `Util`).

If a path is missing, look under `~/.local/share/omarchy/shell/` and `$OMARCHY_PATH/shell/`. If a component you
expected is gone, adapt to what is there and say so in your report.

Then read `reference/patterns.md` and `reference/gotchas.md` next to this file.

## 1. Pin down the idea

Write down, before coding: what the bar shows (glyph, count, short text), what the panel shows, which actions it
offers, where the data comes from, and how often it refreshes. Ask only if the data source is ambiguous;
everything else takes a sensible default.

## 2. Layout

```
<plugin-id>/
  manifest.json        # kinds: ["bar-widget"], entryPoints.barWidget
  BarWidget.qml        # bar button + Loader of Panel.qml
  Panel.qml            # the popup; draws only
  bin/<name>.py        # data and actions; `snapshot` prints JSON; stdlib only
  .forge/seed, unseed  # optional: demo data for forge-check screenshots (reference/patterns.md)
  tests/test_<name>.py # unittest for the script
  README.md            # what it does, install, settings
```

- **id:** `<owner>.<name>`, lowercase, dashes. Use the owner the user gives; otherwise `$USER`.
- **All logic in the script.** The QML runs it with `Process` + `StdioCollector`, parses JSON and draws. Every
  action (kill, open, mark done) is a script subcommand. This keeps the logic testable without the shell, which
  is the slow part to iterate on.
- **User-tunable values** (paths, thresholds, intervals) go in `barWidget.defaults` + `schema` in the manifest and
  are read with `setting(key, fallback)`. Never hardcode a home path.

## 3. Theme colors: hard rule

Every color comes from the theme so the plugin follows `omarchy-theme-set`:

- `import qs.Commons as Commons` and use `Commons.Color.foreground | background | accent | urgent | muted`. The
  bar also exposes `bar.foreground`.
- Translucency: `Commons.Util.alpha(Commons.Color.accent, 0.3)`. Ready-made fills and borders live in
  `Commons.Style` (`normalFill`, `hoverFill`, `selectedFill`, `normalBorderColor`…).
- More hues (red, green, blue, yellow…): read `~/.local/state/omarchy/current/theme/colors.toml` with a
  `FileView`, and fall back to a `Commons.Color` role when a key is missing. See `reference/patterns.md`.
- **Forbidden in QML:** hex literals (`"#33ccff"`), named colors (`"red"`, `"white"`), and `Qt.rgba/hsla/hsva`
  with constant arguments. `"transparent"` is fine. `forge-check.sh` fails on any of these.

## 4. Check, fix, repeat

```bash
<this-skill-dir>/scripts/forge-check.sh <plugin-dir>
```

It validates the manifest, runs the tests, scans the QML for hardcoded colors, links and enables the plugin,
restarts the shell, fails on any journal error or warning that mentions the plugin, opens the panel and takes
screenshots under two themes (failing if the screen did not change when the panel was summoned). It restores
the user's theme when done. The restart makes the bar blink for a second: warn the user before the first run.
Only one forge-check at a time on a machine: two runs restart the same shell and switch the same theme.

On failure, read the step that failed and fix the cause. Do not weaken the check. When it passes, **open the
screenshots and look at them**: the script cannot tell a populated panel from an empty, clipped or off-theme
one. If the panel is data-driven, add `.forge/seed` so the shots show real rows. A panel that is empty,
clipped, or not the color of the theme is not done, even with exit 0.

## 5. Report

What the plugin does, the final `forge-check.sh` output, the screenshot paths, and anything from section 0 that
did not match what this skill or its references say. That last item is how the skill stays current.
