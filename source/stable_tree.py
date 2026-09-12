"""Update existing rows without resetting selection or the reader's scroll anchor."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView

KEY_ROLE = Qt.UserRole + 51


class StableTree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.pending_rows = None
        for bar in (self.verticalScrollBar(), self.horizontalScrollBar()):
            bar.sliderReleased.connect(self.apply_pending)

    def clear(self):
        self.pending_rows = None
        super().clear()

    def apply_pending(self):
        if self.pending_rows:
            args = self.pending_rows
            self.pending_rows = None
            reconcile(self, *args)


def reconcile(tree, rows, key, update, selected=None, follow_top=False):
    rows = list(rows)
    vertical, horizontal = tree.verticalScrollBar(), tree.horizontalScrollBar()
    if vertical.isSliderDown() or horizontal.isSliderDown():
        tree.pending_rows = (rows, key, update, selected, follow_top)
        return
    top = tree.itemAt(1, 1)
    anchor = top.data(0, KEY_ROLE) if top else None
    top_offset = tree.visualItemRect(top).top() if top else 0
    old_y, old_x = vertical.value(), horizontal.value()
    # Evaluate after a drag finishes, using the reader's current position.
    stay_at_top = follow_top and old_y == vertical.minimum()
    signals = tree.blockSignals(True)
    v_signals=vertical.blockSignals(True);h_signals=horizontal.blockSignals(True)
    tree.setUpdatesEnabled(False)
    try:
        existing = {tree.topLevelItem(i).data(0, KEY_ROLE): tree.topLevelItem(i)
                    for i in range(tree.topLevelItemCount())}
        wanted = {repr(key(row)) for row in rows}
        for tag, item in existing.items():
            if tag not in wanted:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        for position, row in enumerate(rows):
            tag = repr(key(row))
            item = existing.get(tag)
            if item is None:
                item = QTreeWidgetItem()
                item.setData(0, KEY_ROLE, tag)
                tree.insertTopLevelItem(position, item)
            elif tree.indexOfTopLevelItem(item) != position:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
                tree.insertTopLevelItem(position, item)
            update(item, row)
            if selected is not None:
                item.setSelected(key(row) == selected)
        tree.doItemsLayout()
        anchored = next((tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                         if tree.topLevelItem(i).data(0, KEY_ROLE) == anchor), None)
        if stay_at_top:
            vertical.setValue(vertical.minimum())
        elif anchored is not None:
            tree.scrollToItem(anchored, QAbstractItemView.PositionAtTop)
            vertical.setValue(vertical.value()-top_offset)
        else:
            vertical.setValue(old_y)
        horizontal.setValue(old_x)
    finally:
        tree.setUpdatesEnabled(True)
        vertical.blockSignals(v_signals);horizontal.blockSignals(h_signals)
        tree.blockSignals(signals)


def set_texts(item, values):
    for column, value in enumerate(values):
        if item.text(column) != value:
            item.setText(column, value)
