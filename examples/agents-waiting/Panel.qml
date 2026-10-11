import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Popup listing coding agents (Claude Code, herdr panes, reported agents), one
// row each: title, state, agent, time since the state changed. Clicking a row
// focuses it (and acknowledges a finished one). All data and the open action
// live in bin/agents-waiting.py.
Panel {
  id: root
  moduleName: "mmilei.agents-waiting"
  ipcTarget: "mmilei.agents-waiting"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var snap: null
  property string error: ""

  readonly property int waitingCount: snap ? snap.waiting : 0
  readonly property int totalCount: snap ? snap.total : 0
  readonly property string script:
    decodeURIComponent(Qt.resolvedUrl("bin/agents-waiting.py").toString().replace("file://", ""))

  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function open() { root.controller.show() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // Tell the watcher what to do: full snapshots every few seconds while open
  // (and one right away), quiet change detection when closed.
  function tell(word) { if (watcher.running) watcher.write(word + "\n") }
  onOpenedChanged: root.tell(root.opened ? "open" : "closed")

  function openRow(key) {
    focusProc.command = ["python3", root.script, "open", key]
    focusProc.running = true
    root.close()
  }

  function stateText(r) {
    if (r.state === "blocked") return "needs you"
    if (r.state === "waiting") return "your move"
    if (r.state === "done") return r.unread ? "finished" : "done"
    return "working"
  }

  function stateColor(r) {
    if (r.state === "blocked") return root.urgent
    if (r.state === "waiting" || (r.state === "done" && r.unread)) return root.accent
    return Qt.alpha(root.fg, 0.45)
  }

  function ageText(s) {
    if (s < 60) return s + "s"
    if (s < 3600) return Math.floor(s / 60) + "m"
    if (s < 86400) return Math.floor(s / 3600) + "h"
    return Math.floor(s / 86400) + "d"
  }

  property var rows: snap ? snap.rows : []

  readonly property string watchArgs: [
    "--interval", String(Math.max(1, Number(setting("interval", 3)))),
    "--closed-interval", String(Math.max(1, Number(setting("closedInterval", 2))))
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
    id: focusProc
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
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
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
            text: "AGENTS"
            foreground: root.fg
            fontFamily: root.fontFamily
          }
          Text {
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: root.waitingCount + " waiting"
            color: root.waitingCount > 0 ? root.accent : Qt.alpha(root.fg, 0.6)
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
          text: "No agents running."
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
            height: rowText.implicitHeight + Style.space(12)
            radius: Style.space(6)
            color: rowArea.containsMouse ? Commons.Util.alpha(root.accent, 0.18)
             : (row.modelData.unread ? Commons.Util.alpha(root.accent, 0.08) : "transparent")

            Rectangle {
              id: dot
              anchors.left: parent.left
              anchors.leftMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(9)
              height: width
              radius: width / 2
              color: root.stateColor(row.modelData)
            }

            Column {
              id: rowText
              anchors.left: dot.right
              anchors.leftMargin: Style.space(10)
              anchors.right: age.left
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: row.modelData.title
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: row.modelData.state !== "working" && !(row.modelData.state === "done" && !row.modelData.unread)
              }
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: root.stateText(row.modelData) + " \u00b7 " + row.modelData.agent_label
                      + (row.modelData.project ? " \u00b7 " + row.modelData.project : "")
                color: root.stateColor(row.modelData)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Text {
              id: age
              anchors.right: parent.right
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: root.ageText(row.modelData.age)
              color: Qt.alpha(root.fg, 0.55)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            MouseArea {
              id: rowArea
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openRow(row.modelData.key)
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
    }
  }
}
