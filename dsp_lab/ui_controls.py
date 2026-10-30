"""Small presentation controls; device identities and audio stay in the model."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter, QFrame, QGridLayout


class DeviceComboBox(QComboBox):
    def display_text(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                            QStyle.SubControl.SC_ComboBoxEditField, self)
        # Keep both the source's leading name and the trailing host API visible.
        return self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideMiddle,
                                             max(0, field.width()))

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = self.display_text()
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

    def showPopup(self):
        screen = self.screen().availableGeometry()
        widest = max((self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0)
        self.view().setMinimumWidth(min(max(self.width(), widest + 40), screen.width() - 32))
        self.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        super().showPopup()


class AdaptiveToolbar(QFrame):
    """Reflow the same live controls without rebuilding their state or bindings."""
    def __init__(self, devices, actions, breakpoint):
        super().__init__()
        self.setObjectName("toolbarBar")
        self.devices, self.actions = devices, actions
        self.breakpoint = breakpoint
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(8, 6, 8, 6)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        self.stacked = None
        self._arrange(False)

    def _arrange(self, stacked):
        if self.stacked == stacked:
            return
        self.grid.removeWidget(self.devices)
        self.grid.removeWidget(self.actions)
        self.stacked = stacked
        if stacked:
            self.grid.addWidget(self.devices, 0, 0, 1, 2)
            self.grid.addWidget(self.actions, 1, 0, 1, 2)
        else:
            self.grid.addWidget(self.devices, 0, 0)
            self.grid.addWidget(self.actions, 0, 1)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 0)

    def resizeEvent(self, event):
        self._arrange(event.size().width() < self.breakpoint)
        super().resizeEvent(event)
