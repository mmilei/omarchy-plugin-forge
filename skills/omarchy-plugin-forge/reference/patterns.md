# Patterns

Shapes that work with omarchy-shell 4.0.x. If the installed shell disagrees, trust the shell (SKILL.md §0).

## manifest.json

```json
{
  "schemaVersion": 1,
  "id": "owner.my-widget",
  "name": "My widget",
  "version": "0.1.0",
  "author": "owner",
  "license": "MIT",
  "description": "One sentence.",
  "kinds": ["bar-widget"],
  "entryPoints": { "barWidget": "BarWidget.qml" },
  "barWidget": {
    "displayName": "My widget",
    "description": "What the bar item shows; click for details.",
    "category": "Info",
    "allowMultiple": false,
    "defaultSection": "right",
    "defaults": { "interval": 5 },
    "schema": [
      { "key": "interval", "type": "integer", "label": "Refresh (s)", "defaultValue": 5,
        "min": 1, "max": 600, "step": 1, "description": "Seconds between refreshes." }
    ]
  }
}
```

Validate with `omarchy plugin validate <dir>`. Setting types used by the built-in plugins: `string`, `integer`
(with `min`/`max`/`step`), `boolean`, `path`, `enum` and `multiselect` (with `options`). Use `path` for folders.
`grep -h '"type"' /usr/share/omarchy/shell/plugins/*/manifest.json` shows what the installed version uses.

Settings reach the script only as arguments built in QML from `setting(...)`. `.forge/seed` cannot change them
(they live in the user's global `shell.json`). If demo data needs a different value, let the script honor a
plugin-local pointer file that seed writes and unseed deletes (for example `.forge/demo-root` overriding the
`root` setting), and document it in the script docstring.

Each plugin folder ships a `.gitignore` with `__pycache__/`: forge-check's test step creates it.

## BarWidget.qml: button + always-loaded panel

The bar widget hosts the popup through a `Loader` and hands it the button to anchor against. `open`, `close` and
`opened` on the root are a contract: `omarchy-shell shell summon|toggle|hide <id>` routes to them, and the popout
coordinator calls `closeForPopoutSwitch` when another popup opens.

```qml
import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "owner.my-widget"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: String.fromCodePoint(0xF0AE)   // a Nerd Font glyph, plus a short count if useful
    slotSize: Style.bar.statusSlot
    fixedWidth: vertical ? -1 : Math.max(slotSize, glyphPaintedWidth + Style.spaceReal(8))
    fontSize: Style.font.caption
    tooltipText: ""                       // the panel is the detail view
    onPressed: function(b) { root.togglePanel() }
  }
}
```

### A bar icon that lights up

`BarIconButton` (through `WidgetButton`) has `active` and `activeColor`. `activeColor` defaults to the theme's
urgent color; for "something needs attention but nothing is wrong", use the accent:

```qml
active: root.count > 0
activeColor: Commons.Color.accent
```

## Panel.qml: draws, never computes

```qml
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

Panel {
  id: root
  moduleName: "owner.my-widget"
  ipcTarget: "owner.my-widget"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  readonly property string script:
    decodeURIComponent(Qt.resolvedUrl("bin/my-widget.py").toString().replace("file://", ""))
  property var snap: null
  property string error: ""

  function open() { root.controller.show(); root.refresh() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }
  function refresh() { if (!snapProc.running) snapProc.running = true }

  Timer { interval: setting("interval", 5) * 1000; repeat: true; running: true; triggeredOnStart: true
          onTriggered: root.refresh() }

  Process {
    id: snapProc
    command: ["python3", root.script, "snapshot"]
    stdout: StdioCollector { onStreamFinished: {
      try { root.snap = JSON.parse(text); root.error = "" } catch (e) { root.error = String(e) } } }
    stderr: StdioCollector { onStreamFinished: if (text.length) root.error = text.trim() }
  }

  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher { id: keyCatcher /* Esc closes */ }
    Column { id: content /* PanelSectionHeader, PanelSeparator, rows… */ }
  }
}
```

Copy the exact `KeyboardPanel` / `PanelKeyCatcher` wiring from the installed weather `Panel.qml`; their
properties change between releases. Inside the panel, text uses `root.fg` and `root.fontFamily` (what the bar
uses) so it matches the bar; accents and states use `Commons.Color`.

## Script contract

- `bin/<name>.py snapshot` prints one JSON object on stdout and exits 0. Errors go to stderr with exit 1. Other
  subcommands (actions, hooks, printing config) follow their own contract; document it in the script docstring.
- Actions are subcommands (`kill <pid>`, `open <path>`), idempotent where possible, and print nothing on success.
- Python stdlib only, so `omarchy plugin add` needs no install step.
- Tests call the functions directly with fixtures (temp dirs, fake command output), never the live system.

## Demo data for the screenshots

A data-driven panel screenshots empty unless there is data. If the plugin's data can be faked safely, add an
executable `.forge/seed` (writes demo data) and `.forge/unseed` (removes exactly what seed wrote) in the plugin
folder; `forge-check.sh` runs them around the screenshots. Never seed into the user's real data, and never leave
demo data behind. `.forge/` ships with the plugin: it documents the states the panel can show.

- Seed runs on the real desktop: demo rows appear next to live ones (a real server, a real session). That is
  fine; write the demo data so it is obviously demo (`api-gateway`, `landing-page`).
- If seed starts processes, record the **real** PID. `(cd d && cmd &)` gives you the subshell's PID: unseed then
  kills the wrapper and the servers survive. `cd` first and background the command itself, or use
  `subprocess.Popen(..., start_new_session=True)`. After unseed, check with `pgrep` that nothing is left.

## Per-row action buttons

`PanelActionButton` from `qs.Ui`: `iconText` (a Nerd Font glyph), `tooltipText`, `foreground`, `hoverColor`,
`size`, and `onClicked`. Use `root.fg` for `foreground` and `Commons.Color.accent` (or `urgent` for destructive
actions) for `hoverColor`.

## Destructive actions

Anything that kills, deletes or discards asks first, inside the panel: a `ConfirmDialog` from `qs.Ui` or a
second click on the same row within a few seconds. Never on a single click.

`ConfirmDialog` wiring that works (it does not take keyboard focus on its own, and its default selection is
Confirm, so a stray Enter would run the action):

```qml
property var pending: null

function ask(item) { root.pending = item; confirm.selectedIndex = 0; confirmKeys.forceActiveFocus() }
function cancel() { root.pending = null; keyCatcher.forceActiveFocus() }
function confirmAction() { /* run the script subcommand with root.pending */ root.cancel() }

PanelKeyCatcher {
  id: keyCatcher
  blocked: root.pending !== null          // stop Esc/Tab handling while the dialog is up
  onCloseRequested: root.close()
  // …content…

  Item {                                   // hands keys to the dialog while it is open
    id: confirmKeys
    Keys.onPressed: function(event) { if (confirm.handleKey(event)) event.accepted = true }
  }

  ConfirmDialog {
    id: confirm
    anchors.fill: parent
    z: 10
    opened: root.pending !== null
    message: root.pending ? "Stop " + root.pending.name + "?" : ""
    confirmText: "Stop"
    background: Commons.Color.background
    foreground: root.fg
    selectedText: Commons.Color.accent
    fontFamily: root.fontFamily
    onOpenedChanged: if (opened) selectedIndex = 0   // 0 = Cancel
    onCanceled: root.cancel()
    onConfirmed: root.confirmAction()
  }
}
```

For actions that check a PID, pass its start time too and have the script refuse when it changed (see
`gotchas.md`, "PIDs from hooks and launchers").

## Extra theme hues

`Commons.Color` has five roles. For more, read the theme file and fall back to a role:

```qml
property var palette: ({})
FileView {
  id: paletteFile
  path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
  printErrors: false
  onLoaded: {
    var out = {}
    text().split("\n").forEach(function(line) {
      var m = line.match(/^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6,8})"/)
      if (m) out[m[1]] = m[2]
    })
    root.palette = out
  }
}
// Theme switches push new colors over IPC; reload when the accent moves.
Connections { target: Commons.Color; function onAccentChanged() { paletteFile.reload() } }
function hue(name, fallback) { return palette[name] ? palette[name] : fallback }
// color: hue("green", Commons.Color.accent)
```

Keys present in current themes include `red`, `green`, `yellow`, `orange`, `blue`, `magenta`, `cyan`.
