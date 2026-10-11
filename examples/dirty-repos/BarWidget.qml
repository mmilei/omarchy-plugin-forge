import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar item: a git glyph, plus the number of repos with pending work (hidden at zero).

BarWidget {
  id: root
  moduleName: "mmilei.dirty-repos"

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

  readonly property int repoCount: panelLoader.item ? panelLoader.item.repoCount : 0

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
    text: String.fromCodePoint(0xF02A2) + (root.repoCount > 0 ? " " + root.repoCount : "")
    active: root.repoCount > 0
    activeColor: Commons.Color.accent
    slotSize: Style.bar.statusSlot
    fixedWidth: vertical ? -1 : Math.max(slotSize, glyphPaintedWidth + Style.spaceReal(8))
    fontSize: Style.font.caption
    tooltipText: ""
    onPressed: function(b) { root.togglePanel() }
  }
}
