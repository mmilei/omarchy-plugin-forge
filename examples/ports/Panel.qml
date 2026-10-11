import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Popup listing the TCP ports your processes listen on: port, process, PID and
// project folder (the process's cwd; full path on the second line). Per row:
// open in the browser, stop (SIGTERM) after an in-panel confirmation. All data
// and actions live in bin/ports.py.
Panel {
  id: root
  moduleName: "mmilei.ports"
  ipcTarget: "mmilei.ports"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var snap: null
  property string error: ""
  property var pending: null

  readonly property int portCount: snap ? snap.count : 0
  readonly property var rows: snap ? snap.ports : []
  readonly property string script:
    decodeURIComponent(Qt.resolvedUrl("bin/ports.py").toString().replace("file://", ""))

  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string home: Quickshell.env("HOME")

  function open() { root.controller.show() }
  function close() { root.cancel(); root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // Tell the watcher what to compute: the full list while open, only the count
  // when closed. "open" also means "print now".
  function tell(word) { if (watcher.running) watcher.write(word + "\n") }
  onOpenedChanged: root.tell(root.opened ? "open" : "closed")

  function openPort(port) {
    actionProc.command = ["python3", root.script, "open", String(port)]
    actionProc.running = true
    root.close()
  }

  function ask(row) {
    root.pending = row
    confirm.selectedIndex = 0   // Cancel: a stray Enter must not stop anything
    confirmKeys.forceActiveFocus()
  }
  function cancel() {
    root.pending = null
    keyCatcher.forceActiveFocus()
  }
  function confirmStop() {
    var p = root.pending
    if (!p) return
    actionProc.command = ["python3", root.script, "stop", String(p.pid), String(p.start)]
    actionProc.running = true
    root.cancel()
    refreshSoon.restart()
  }

  function shortPath(p) {
    if (!p) return "(unknown)"
    return root.home !== "" && p.indexOf(root.home) === 0 ? "~" + p.substring(root.home.length) : p
  }

  Timer { id: refreshSoon; interval: 600; onTriggered: root.tell("open") }

  readonly property string watchArgs: [
    "--interval", String(Math.max(1, Number(setting("interval", 3)))),
    "--closed-interval", String(Math.max(1, Number(setting("closedInterval", 5))))
  ].join(" ")
  // A changed setting restarts the watcher; onExited brings it back.
  onWatchArgsChanged: if (watcher.running) watcher.running = false

  Process {
    id: watcher
    running: true
    stdinEnabled: true
    command: ["python3", root.script, "watch", "--mode", root.opened ? "open" : "closed"].concat(root.watchArgs.split(" "))
    stdout: SplitParser {
      onRead: function(line) {
        try { root.snap = JSON.parse(line); root.error = "" }
        catch (e) { root.error = "unreadable snapshot: " + e }
      }
    }
    stderr: StdioCollector {
      onStreamFinished: if (text.length) root.error = text.trim()
    }
    onExited: restart.start()
  }
  Timer { id: restart; interval: 3000; onTriggered: watcher.running = true }

  Process {
    id: actionProc
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
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.pending !== null
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(8)

        Item {
          width: parent.width
          height: header.implicitHeight
          PanelSectionHeader {
            id: header
            text: "LISTENING PORTS"
            foreground: root.fg
            fontFamily: root.fontFamily
          }
          Text {
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: root.portCount + " open"
            color: root.portCount > 0 ? root.accent : Qt.alpha(root.fg, 0.6)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
        }

        PanelSeparator { foreground: root.fg }

        Text {
          visible: root.rows.length === 0
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: "Nothing of yours is listening."
          color: Qt.alpha(root.fg, 0.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Repeater {
          model: root.rows
          delegate: Rectangle {
            id: row
            required property var modelData
            width: content.width
            height: Math.max(info.implicitHeight, stopBtn.height) + Style.space(12)
            radius: Style.space(6)
            color: rowArea.containsMouse ? Commons.Util.alpha(root.accent, 0.12) : "transparent"

            MouseArea { id: rowArea; anchors.fill: parent; hoverEnabled: true }

            Text {
              id: portText
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(56)
              textFormat: Text.PlainText
              text: row.modelData.port
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Column {
              id: info
              anchors.left: portText.right
              anchors.leftMargin: Style.space(8)
              anchors.right: openBtn.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: row.modelData.project !== "" ? row.modelData.project : row.modelData.name
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: row.modelData.name + " · pid " + row.modelData.pid
                color: Qt.alpha(root.fg, 0.75)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                text: root.shortPath(row.modelData.cwd)
                color: Qt.alpha(root.fg, 0.5)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            PanelActionButton {
              id: openBtn
              anchors.right: stopBtn.left
              anchors.rightMargin: Style.space(4)
              anchors.verticalCenter: parent.verticalCenter
              iconText: String.fromCodePoint(0xF03CC)
              tooltipText: "Open http://localhost:" + row.modelData.port
              foreground: root.fg
              hoverColor: root.accent
              fontFamily: root.fontFamily
              onClicked: root.openPort(row.modelData.port)
            }

            PanelActionButton {
              id: stopBtn
              anchors.right: parent.right
              anchors.rightMargin: Style.space(6)
              anchors.verticalCenter: parent.verticalCenter
              iconText: String.fromCodePoint(0xF0156)
              tooltipText: "Stop process (SIGTERM)"
              foreground: root.fg
              hoverColor: root.urgent
              fontFamily: root.fontFamily
              onClicked: root.ask(row.modelData)
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
        opened: root.pending !== null
        message: root.pending ? "Stop " + root.pending.name + " (pid " + root.pending.pid + ") on port " + root.pending.port + "?" : ""
        confirmText: "Stop"
        background: Commons.Color.background
        foreground: root.fg
        selectedText: root.accent
        fontFamily: root.fontFamily
        onOpenedChanged: if (opened) selectedIndex = 0
        onCanceled: root.cancel()
        onConfirmed: root.confirmStop()
      }
    }
  }
}
