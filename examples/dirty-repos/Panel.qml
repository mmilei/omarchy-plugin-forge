import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Popup listing the git repos under the root folder that have pending work: name,
// branch and what is pending. Clicking a row opens a terminal in that repo. All data
// and actions live in bin/dirty-repos.py.
Panel {
  id: root
  moduleName: "mmilei.dirty-repos"
  ipcTarget: "mmilei.dirty-repos"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var snap: null
  property string error: ""

  readonly property int repoCount: snap ? snap.count : 0
  readonly property var rows: snap ? snap.repos : []
  readonly property string script:
    decodeURIComponent(Qt.resolvedUrl("bin/dirty-repos.py").toString().replace("file://", ""))

  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string home: Quickshell.env("HOME")

  function open() { root.controller.show() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // "open" also means "rediscover, rescan everything and print now".
  function tell(word) { if (watcher.running) watcher.write(word + "\n") }
  onOpenedChanged: root.tell(root.opened ? "open" : "closed")

  function openRepo(path) {
    actionProc.command = ["python3", root.script, "open", path]
    actionProc.running = true
    root.close()
  }

  function shortPath(p) {
    if (!p) return ""
    return root.home !== "" && p.indexOf(root.home) === 0 ? "~" + p.substring(root.home.length) : p
  }

  function pending(r) {
    var parts = []
    if (r.changed > 0) parts.push(r.changed + (r.changed === 1 ? " changed file" : " changed files"))
    if (r.ahead > 0) parts.push(r.ahead + " ahead")
    if (r.behind > 0) parts.push(r.behind + " behind")
    if (r.no_upstream) parts.push("no upstream")
    return parts.join(" · ")
  }

  readonly property var watchArgs: [
    "--root", String(setting("root", "~/git")), "--depth", String(setting("depth", 2)),
    "--interval", String(Math.max(10, Number(setting("interval", 60)))),
    "--closed-interval", String(Math.max(5, Number(setting("closedInterval", 15))))
  ]
  // A changed setting restarts the watcher; onExited brings it back.
  onWatchArgsChanged: if (watcher.running) watcher.running = false

  Process {
    id: watcher
    running: true
    stdinEnabled: true
    command: ["python3", root.script, "watch", "--mode", root.opened ? "open" : "closed"].concat(root.watchArgs)
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
            text: "REPOS WITH PENDING WORK"
            foreground: root.fg
            fontFamily: root.fontFamily
          }
          Text {
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: root.repoCount + " of " + (root.snap ? root.snap.scanned : 0)
            color: root.repoCount > 0 ? root.accent : Qt.alpha(root.fg, 0.6)
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
          text: root.snap && root.snap.missing_root
                ? "Root folder not found: " + root.snap.root
                : "Everything is committed and pushed."
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
            height: info.implicitHeight + Style.space(12)
            radius: Style.space(6)
            color: rowArea.containsMouse ? Commons.Util.alpha(root.accent, 0.12) : "transparent"

            MouseArea {
              id: rowArea
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openRepo(row.modelData.path)
            }

            Column {
              id: info
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.right: parent.right
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

              Row {
                width: parent.width
                spacing: Style.space(8)
                Text {
                  id: nameText
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  width: Math.min(implicitWidth, parent.width * 0.6)
                  text: row.modelData.name
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  width: parent.width - nameText.width - parent.spacing
                  text: row.modelData.branch
                  color: root.accent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
              }
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: root.pending(row.modelData)
                color: Qt.alpha(root.fg, 0.85)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                text: root.shortPath(row.modelData.path)
                color: Qt.alpha(root.fg, 0.5)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }
        }

        Text {
          visible: root.snap !== null && root.rows.length > 0
          width: parent.width
          textFormat: Text.PlainText
          text: "ahead/behind as of the last fetch"
          color: Qt.alpha(root.fg, 0.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
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
