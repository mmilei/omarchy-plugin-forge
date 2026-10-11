import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar item: CPU and RAM percentages as one short label. Turns urgent-colored
// when either one is at or above the panel's critical threshold.
BarWidget {
  id: root
  moduleName: "mmilei.resources"

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

  readonly property string label: panelLoader.item ? panelLoader.item.barLabel : ""
  readonly property bool critical: panelLoader.item ? panelLoader.item.critical : false

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
    // nf-md-chip and nf-md-memory
    text: root.label !== "" ? root.label : String.fromCodePoint(0xF061A)
    active: root.critical
    activeColor: Commons.Color.urgent
    slotSize: Style.bar.statusSlot
    fixedWidth: vertical ? -1 : Math.max(slotSize, glyphPaintedWidth + Style.spaceReal(8))
    fontSize: Style.font.caption
    tooltipText: ""
    onPressed: function(b) { root.togglePanel() }
  }
}
