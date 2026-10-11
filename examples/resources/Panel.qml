import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Popup with the system snapshot: CPU (total + per core), RAM, swap, load,
// temperature (only when a sensor exists) and the top CPU processes, each
// with a terminate button that asks first. All numbers come from
// bin/resources.py; this file only draws them.
Panel {
  id: root
  moduleName: "mmilei.resources"
  ipcTarget: "mmilei.resources"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var snap: null
  property string error: ""
  // Process waiting for the user's yes/no: { pid, start, name } or null.
  property var pendingKill: null

  readonly property string script:
    decodeURIComponent(Qt.resolvedUrl("bin/resources.py").toString().replace("file://", ""))

  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property real warnAt: 70
  readonly property real criticalAt: 90

  readonly property real cpuPercent: snap ? snap.cpu.percent : 0
  readonly property real ramPercent: snap ? snap.ram.percent : 0
  readonly property bool critical: cpuPercent >= criticalAt || ramPercent >= criticalAt
  // nf-md-chip, nf-md-memory
  readonly property string barLabel: snap
    ? String.fromCodePoint(0xF061A) + " " + Math.round(cpuPercent) + "%  " + String.fromCodePoint(0xF035B) + " " + Math.round(ramPercent) + "%"
    : ""

  property var processes: snap ? snap.processes : []
  property var cores: snap ? snap.cpu.cores : []
  readonly property bool hasTemperature: snap !== null && snap.temperature !== null && snap.temperature !== undefined

  // Extra hue for the "getting busy" level; falls back to the accent.
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
  Connections { target: Commons.Color; function onAccentChanged() { paletteFile.reload() } }

  function hue(name, fallback) { return palette[name] ? palette[name] : fallback }

  function levelColor(percent) {
    if (percent >= criticalAt) return root.urgent
    if (percent >= warnAt) return root.hue("bright_yellow", root.accent)
    return root.accent
  }

  function fmtBytes(n) {
    var units = ["B", "KiB", "MiB", "GiB", "TiB"]
    var i = 0
    var v = n
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
    return (i >= 3 ? v.toFixed(1) : Math.round(v)) + " " + units[i]
  }

  function fmtMemory(m) {
    if (!m || m.total <= 0) return "none"
    return fmtBytes(m.used) + " / " + fmtBytes(m.total) + "  " + Math.round(m.percent) + "%"
  }

  function open() { root.controller.show(); root.refresh() }
  function close() { cancelKill(); root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function refresh() { if (!snapProc.running) snapProc.running = true }

  function askKill(proc) {
    root.pendingKill = proc
    confirm.selectedIndex = 0   // default to Cancel: a stray Enter must not kill
    confirmKeys.forceActiveFocus()
  }

  function cancelKill() {
    root.pendingKill = null
    keyCatcher.forceActiveFocus()
  }

  function confirmKill() {
    var p = root.pendingKill
    if (!p) return
    killProc.command = ["python3", root.script, "kill", String(p.pid), String(p.start)]
    killProc.running = true
    cancelKill()
  }

  Timer {
    interval: Math.max(1, Number(setting("interval", 2))) * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: snapProc
    command: ["python3", root.script, "snapshot", "--top", String(Math.max(1, Math.round(Number(setting("processCount", 5)))))]
    stdout: StdioCollector {
      onStreamFinished: {
        if (!text.length) return
        try { root.snap = JSON.parse(text); root.error = "" }
        catch (e) { root.error = "unreadable snapshot: " + e }
      }
    }
    stderr: StdioCollector {
      onStreamFinished: if (text.length) root.error = text.trim()
    }
  }

  Process {
    id: killProc
    onExited: root.refresh()
    stderr: StdioCollector {
      onStreamFinished: if (text.length) root.error = text.trim()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(Math.max(content.implicitHeight, Style.space(190)))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.pendingKill !== null
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(8)

        // ---- CPU ----
        Item {
          width: parent.width
          height: cpuHeader.implicitHeight
          PanelSectionHeader {
            id: cpuHeader
            text: "CPU"
            foreground: root.fg
            fontFamily: root.fontFamily
          }
          Text {
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: root.snap ? root.snap.ncpu + " cores" : ""
            color: Qt.alpha(root.fg, 0.6)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Meter {
          width: parent.width
          label: "Total"
          value: Math.round(root.cpuPercent) + "%"
          fraction: root.cpuPercent / 100
          fill: root.levelColor(root.cpuPercent)
          foreground: root.fg
          fontFamily: root.fontFamily
        }

        Grid {
          width: parent.width
          columns: root.cores.length > 16 ? 4 : 2
          columnSpacing: Style.space(14)
          rowSpacing: Style.space(4)
          readonly property real cellWidth: (width - columnSpacing * (columns - 1)) / columns

          Repeater {
            model: root.cores
            delegate: Meter {
              required property int index
              required property real modelData
              width: parent.cellWidth
              label: "Core " + index
              value: Math.round(modelData) + "%"
              fraction: modelData / 100
              fill: root.levelColor(modelData)
              foreground: root.fg
              fontFamily: root.fontFamily
              fontSize: Style.font.caption
            }
          }
        }

        PanelSeparator { foreground: root.fg }

        // ---- Memory ----
        PanelSectionHeader {
          text: "MEMORY"
          foreground: root.fg
          fontFamily: root.fontFamily
        }

        Meter {
          width: parent.width
          label: "RAM"
          value: root.snap ? root.fmtMemory(root.snap.ram) : ""
          fraction: root.ramPercent / 100
          fill: root.levelColor(root.ramPercent)
          foreground: root.fg
          fontFamily: root.fontFamily
        }

        Meter {
          width: parent.width
          label: "Swap"
          value: root.snap ? root.fmtMemory(root.snap.swap) : ""
          fraction: root.snap ? root.snap.swap.percent / 100 : 0
          fill: root.levelColor(root.snap ? root.snap.swap.percent : 0)
          foreground: root.fg
          fontFamily: root.fontFamily
        }

        PanelSeparator { foreground: root.fg }

        // ---- Load and temperature ----
        Item {
          width: parent.width
          height: loadText.implicitHeight

          Text {
            id: loadText
            anchors.left: parent.left
            textFormat: Text.PlainText
            text: root.snap ? "Load  " + root.snap.load.map(function(v) { return v.toFixed(2) }).join("  ") : ""
            color: root.snap && root.snap.load[0] > root.snap.ncpu ? root.urgent : root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            anchors.right: parent.right
            visible: root.hasTemperature
            textFormat: Text.PlainText
            text: root.hasTemperature ? "Temp  " + Math.round(root.snap.temperature) + " °C" : ""
            color: root.hasTemperature ? root.levelColor(root.snap.temperature) : root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }
        }

        PanelSeparator { foreground: root.fg }

        // ---- Processes ----
        PanelSectionHeader {
          text: "TOP PROCESSES"
          foreground: root.fg
          fontFamily: root.fontFamily
        }

        Text {
          visible: root.snap !== null && root.processes.length === 0
          width: parent.width
          textFormat: Text.PlainText
          text: "Nothing is using the CPU right now."
          color: Qt.alpha(root.fg, 0.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Repeater {
          model: root.processes
          delegate: Rectangle {
            id: row
            required property var modelData
            width: content.width
            height: Math.max(rowName.implicitHeight, killButton.implicitHeight) + Style.space(8)
            radius: Style.space(6)
            color: rowArea.containsMouse ? Commons.Util.alpha(root.accent, 0.12) : "transparent"

            MouseArea { id: rowArea; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }

            Text {
              id: rowName
              anchors.left: parent.left
              anchors.leftMargin: Style.space(8)
              anchors.right: rowPid.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              elide: Text.ElideRight
              text: row.modelData.name
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Text {
              id: rowPid
              anchors.right: rowCpu.left
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: row.modelData.pid
              color: Qt.alpha(root.fg, 0.5)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Text {
              id: rowCpu
              anchors.right: killButton.left
              anchors.rightMargin: Style.space(6)
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(52)
              horizontalAlignment: Text.AlignRight
              textFormat: Text.PlainText
              text: row.modelData.cpu.toFixed(1) + "%"
              color: root.levelColor(row.modelData.cpu)
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            // Placeholder keeps the columns aligned for processes that are not ours.
            PanelActionButton {
              id: killButton
              anchors.right: parent.right
              anchors.rightMargin: Style.space(4)
              anchors.verticalCenter: parent.verticalCenter
              visible: row.modelData.mine
              iconText: String.fromCodePoint(0xF0156)   // nf-md-close
              foreground: root.fg
              hoverColor: root.urgent
              fontFamily: root.fontFamily
              onClicked: root.askKill({ pid: row.modelData.pid, start: row.modelData.start, name: row.modelData.name })
            }
          }
        }

        Text {
          visible: root.error !== ""
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: root.error
          color: root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      // Receives keys while the confirmation is open (the key catcher is
      // blocked then) and hands them to the dialog.
      Item {
        id: confirmKeys
        focus: false
        Keys.onPressed: function(event) {
          if (confirm.handleKey(event)) event.accepted = true
        }
      }

      ConfirmDialog {
        id: confirm
        anchors.fill: parent
        z: 10
        opened: root.pendingKill !== null
        message: root.pendingKill ? "Terminate " + root.pendingKill.name + " (pid " + root.pendingKill.pid + ")?" : ""
        confirmText: "Terminate"
        background: Commons.Color.background
        foreground: root.fg
        selectedText: root.accent
        fontFamily: root.fontFamily
        // Always open on Cancel: a stray Enter must never terminate anything.
        onOpenedChanged: if (opened) selectedIndex = 0
        onCanceled: root.cancelKill()
        onConfirmed: root.confirmKill()
      }
    }
  }
}
