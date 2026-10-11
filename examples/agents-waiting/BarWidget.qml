import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar item: a robot glyph that lights up in the accent color, with the count,
// while at least one Claude Code session is waiting for the user.
BarWidget {
  id: root
  moduleName: "mmilei.agents-waiting"

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

  readonly property int waitingCount: panelLoader.item ? panelLoader.item.waitingCount : 0

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
    text: String.fromCodePoint(0xF06A9) + (root.waitingCount > 0 ? " " + root.waitingCount : "")
    active: root.waitingCount > 0
    activeColor: Commons.Color.accent
    slotSize: Style.bar.statusSlot
    fixedWidth: vertical ? -1 : Math.max(slotSize, glyphPaintedWidth + Style.spaceReal(8))
    fontSize: Style.font.caption
    tooltipText: ""
    onPressed: function(b) { root.togglePanel() }
  }
}
