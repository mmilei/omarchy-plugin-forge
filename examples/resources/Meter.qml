import QtQuick
import qs.Commons
import qs.Commons as Commons

// One labelled usage bar: "label ........ value" over a thin track.
// Colors come in from the panel so the whole plugin shares one palette.
Item {
  id: root

  property string label: ""
  property string value: ""
  property real fraction: 0
  property color fill: Commons.Color.accent
  property color foreground: Commons.Color.foreground
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.body

  implicitHeight: textRow.implicitHeight + Style.space(3) + Style.space(5)
  height: implicitHeight

  Item {
    id: textRow
    width: parent.width
    implicitHeight: valueText.implicitHeight

    Text {
      anchors.left: parent.left
      anchors.right: valueText.left
      anchors.rightMargin: Style.space(8)
      textFormat: Text.PlainText
      elide: Text.ElideRight
      text: root.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: root.fontSize
    }

    Text {
      id: valueText
      anchors.right: parent.right
      textFormat: Text.PlainText
      text: root.value
      color: Qt.alpha(root.foreground, 0.75)
      font.family: root.fontFamily
      font.pixelSize: root.fontSize
    }
  }

  Rectangle {
    id: track
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: Style.space(5)
    radius: height / 2
    color: Qt.alpha(root.foreground, 0.14)

    Rectangle {
      width: Math.max(track.height, track.width * Math.max(0, Math.min(1, root.fraction)))
      height: parent.height
      radius: parent.radius
      color: root.fill

      Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }
  }
}
